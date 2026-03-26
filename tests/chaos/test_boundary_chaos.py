from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.a2a import AgentRegistrationRequest
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_event import EventType
from synapse.models.run import RunStatus
from synapse.runtime.a2a import A2AHub
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.event_bus import EventBus
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope
from synapse.transports.websocket_manager import WebSocketManager
from tests.chaos.helpers import ChaosScenarioReport
from tests.test_a2a_tenant_binding import _build_a2a_client
from tests.test_run_state import _CapturingAdapter, _StubBrowserService, _StubMemoryManager, _StubSafety, _StubTaskManager
from tests.test_websocket_tenant_delivery import _build_app


def test_cross_tenant_access_attempts_remain_isolated_under_repeated_pressure() -> None:
    client, authenticator, _ = _build_app()
    token_a = authenticator.issue_token(
        subject="operator-a",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-a",
    )
    token_b = authenticator.issue_token(
        subject="operator-b",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-b",
    )

    with client.websocket_connect(f"/api/ws?token={token_a}") as ws_a:
        with client.websocket_connect(f"/api/ws?token={token_b}") as ws_b:
            for index in range(3):
                response_a = client.post(
                    "/emit",
                    json={
                        "event_type": "task.updated",
                        "organization_id": "org-1",
                        "project_id": "project-a",
                        "run_id": f"run-{index}",
                        "agent_id": "agent-a",
                        "correlation_id": f"corr-{index}",
                        "payload": {"label": f"project-a-{index}"},
                    },
                )
                response_b = client.post(
                    "/emit",
                    json={
                        "event_type": "task.updated",
                        "organization_id": "org-1",
                        "project_id": "project-b",
                        "run_id": f"run-b-{index}",
                        "agent_id": "agent-b",
                        "correlation_id": f"corr-b-{index}",
                        "payload": {"label": f"project-b-{index}"},
                    },
                )
                assert response_a.status_code == 200
                assert response_b.status_code == 200
                event_a = ws_a.receive_json()
                event_b = ws_b.receive_json()
                assert event_a["project_id"] == "project-a"
                assert event_a["payload"]["label"] == f"project-a-{index}"
                assert event_b["project_id"] == "project-b"
                assert event_b["payload"]["label"] == f"project-b-{index}"


def test_a2a_abuse_attempts_are_rejected_without_delivery() -> None:
    client, authenticator = _build_a2a_client(service_allowlist={})
    denied_token = authenticator.issue_token(
        subject="service-rogue",
        principal_type=PrincipalType.SERVICE,
        scopes=[Scope.A2A_RECEIVE.value],
        organization_id="org-1",
        project_id="project-1",
    )

    disconnected = False
    try:
        with client.websocket_connect(f"/api/a2a/ws/agent-1?token={denied_token}"):
            pass
    except Exception:
        disconnected = True
    assert disconnected is True


def test_websocket_disconnect_during_intervention_required_state_preserves_pending_queue() -> None:
    async def scenario() -> tuple[RuntimeOrchestrator, str]:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent One",
                organization_id="development",
                project_id="development",
            )
        )
        await agents.save_to_store(agent)
        orchestrator = RuntimeOrchestrator(
            browser=_StubBrowserService(),
            agents=agents,
            tools=SimpleNamespace(),
            messages=SimpleNamespace(),
            a2a=A2AHub(agents, state_store=store, sockets=WebSocketManager(state_store=store)),
            memory_manager=_StubMemoryManager(),
            task_manager=_StubTaskManager(),
            sockets=WebSocketManager(state_store=store),
            sandbox=SimpleNamespace(set_state_store=lambda store: None),
            safety=_StubSafety(),
            budget_manager=AgentBudgetManager(),
            state_store=store,
            llm=None,
        )
        run = await orchestrator.run_store.create_run(
            task_id="task-intervention-disconnect",
            agent_id="agent-1",
            project_id="development",
            correlation_id="corr-intervention",
        )
        return orchestrator, run.run_id

    orchestrator, run_id = asyncio.run(scenario())
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    authenticator = Authenticator(Settings(auth_required=True, jwt_secret="test-secret", jwt_issuer="synapse-test", jwt_audience="synapse-test-api"))
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    client = TestClient(app)
    token = authenticator.issue_token(
        subject="operator-1",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value, Scope.TASKS_WRITE.value],
        organization_id="development",
        project_id="development",
    )
    with client.websocket_connect(f"/api/ws?token={token}&run_id={run_id}") as websocket:
        orchestrator_loop = asyncio.new_event_loop()
        try:
            orchestrator_loop.run_until_complete(
                orchestrator.event_bus.emit(
                    EventType.BROWSER_CAPTCHA_DETECTED,
                    organization_id="development",
                    project_id="development",
                    run_id=run_id,
                    agent_id="agent-1",
                    task_id="task-intervention-disconnect",
                    source="browser_service",
                    payload={"reason": "captcha loop", "challenge_type": "captcha", "operator_handoff": True},
                )
            )
        finally:
            orchestrator_loop.close()
        first_event = websocket.receive_json()
        assert first_event["event_type"] == EventType.BROWSER_CAPTCHA_DETECTED.value
    interventions = client.get("/api/interventions", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(interventions) == 1
    assert interventions[0]["state"] == "pending"


def test_repeated_challenge_loop_keeps_run_waiting_for_operator() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent One",
                organization_id="development",
                project_id="development",
            )
        )
        await agents.save_to_store(agent)
        orchestrator = RuntimeOrchestrator(
            browser=_StubBrowserService(),
            agents=agents,
            tools=SimpleNamespace(),
            messages=SimpleNamespace(),
            a2a=SimpleNamespace(),
            memory_manager=_StubMemoryManager(),
            task_manager=_StubTaskManager(),
            sockets=WebSocketManager(state_store=store),
            sandbox=SimpleNamespace(),
            safety=_StubSafety(),
            budget_manager=AgentBudgetManager(),
            state_store=store,
            llm=None,
        )
        adapter = _CapturingAdapter()
        agents.build_adapter = lambda *args, **kwargs: adapter  # type: ignore[method-assign]
        run = await orchestrator.run_store.create_run(
            task_id="task-captcha-loop",
            agent_id="agent-1",
            project_id="development",
            correlation_id="task-captcha-loop",
            metadata={"goal": "Continue only with operator"},
        )
        for _ in range(2):
            await orchestrator.event_bus.emit(
                EventType.BROWSER_CAPTCHA_DETECTED,
                organization_id="development",
                project_id="development",
                run_id=run.run_id,
                agent_id=run.agent_id,
                task_id=run.task_id,
                source="browser_service",
                payload={"reason": "captcha loop", "challenge_type": "captcha", "operator_handoff": True},
            )
        waiting = await orchestrator.get_run(run.run_id)
        interventions = await orchestrator.list_interventions(run_id=run.run_id)
        assert waiting.status == RunStatus.WAITING_FOR_OPERATOR
        assert len(interventions) >= 1
        assert adapter.requests == []

        report = ChaosScenarioReport(
            scenario="repeated-captcha-loop",
            severity="high",
            failure_mode="challenge repeats without operator resolution",
            safe=True,
            recovered=False,
            manual_intervention_required=True,
            expected_behavior="run must not continue autonomously after repeated challenge events",
            evidence={"run_status": waiting.status.value, "intervention_count": len(interventions)},
        )
        assert report.as_dict()["safe"] is True

    asyncio.run(scenario())
