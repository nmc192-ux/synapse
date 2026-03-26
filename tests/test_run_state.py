import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.models.a2a import AgentRegistrationRequest
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.run import RunStatus
from synapse.models.runtime_event import EventType
from synapse.models.task import TaskRequest, TaskResult, TaskStatus
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.a2a import A2AHub
from synapse.runtime.checkpoint_service import CheckpointService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.task_runtime import TaskRuntime
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope
from synapse.transports.websocket_manager import WebSocketManager


class _StubBrowserService:
    def __init__(self) -> None:
        self.browser = object()
        self.sandbox = object()
        self.budget_service = SimpleNamespace(budget_manager=AgentBudgetManager())
        self.saved_sessions: list[dict[str, object]] = []

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return SimpleNamespace(session_id=session_id)

    async def save_session_state(
        self,
        session_id: str,
        agent_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.saved_sessions.append({"session_id": session_id, "agent_id": agent_id, "task_id": task_id, "run_id": run_id})

    async def restore_session_state(self, session_id: str, agent_id: str | None = None, checkpoint_id: str | None = None, run_id: str | None = None):
        return None


class _StubToolService:
    async def call_tool(self, tool_name: str, arguments: dict[str, object], agent_id: str | None = None) -> dict[str, object]:
        return {"ok": True}


class _StubTaskManager:
    async def create_task(self, request):
        return request

    async def claim_task(self, task_id, request):
        return request

    async def update_task(self, task_id, request):
        return request

    async def list_active_tasks(self):
        return []


class _StubSafety:
    def validate_task(self, request):
        return None

    def build_operator_intervention_payload(
        self,
        *,
        event_type: str,
        run_id: str | None,
        agent_id: str | None,
        task_id: str | None,
        payload: dict[str, object] | None = None,
        source: str,
    ) -> dict[str, object]:
        return {
            "event_type": event_type,
            "run_id": run_id,
            "agent_id": agent_id,
            "task_id": task_id,
            "source": source,
            "reason": (payload or {}).get("reason", event_type),
            "category": (payload or {}).get("challenge_type", event_type),
            "details": payload or {},
        }


class _StubMemoryManager:
    async def store(self, request):
        return request

    async def search(self, request):
        return []

    async def get_recent(self, agent_id: str, limit: int = 10):
        return []

    async def get_recent_by_type(self, agent_id: str, limit_per_type: int = 4):
        return {}


class _StubAdapter:
    async def execute_task(self, request: TaskRequest) -> TaskResult:
        return TaskResult(
            task_id=request.task_id,
            run_id=request.run_id,
            status=TaskStatus.COMPLETED,
            message="done",
            artifacts={"echo_run_id": request.run_id},
        )


class _CapturingAdapter:
    def __init__(self) -> None:
        self.requests: list[TaskRequest] = []

    async def execute_task(self, request: TaskRequest) -> TaskResult:
        self.requests.append(request)
        return TaskResult(
            task_id=request.task_id,
            run_id=request.run_id,
            status=TaskStatus.COMPLETED,
            message="done",
            artifacts={"constraints": request.constraints},
        )


def test_run_store_crud() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        run = await run_store.create_run(task_id="task-1", agent_id="agent-1", correlation_id="task-1")
        assert run.status == RunStatus.RUNNING
        fetched = await run_store.get(run.run_id)
        assert fetched.task_id == "task-1"
        runs = await run_store.list(agent_id="agent-1")
        assert len(runs) == 1

    asyncio.run(scenario())


def test_task_runtime_creates_and_persists_run_state() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
        await agents.save_to_store(agent)
        agents.build_adapter = lambda *args, **kwargs: _StubAdapter()  # type: ignore[method-assign]

        browser_service = _StubBrowserService()
        events = EventBus(WebSocketManager(state_store=store))
        checkpoint_service = CheckpointService(store, browser_service, events)
        run_store = RunStore(store)
        runtime = TaskRuntime(
            agents=agents,
            browser_service=browser_service,
            tool_service=_StubToolService(),
            memory_service=MemoryService(_StubMemoryManager()),
            task_manager=_StubTaskManager(),
            checkpoint_service=checkpoint_service,
            run_store=run_store,
            events=events,
            safety=_StubSafety(),
            llm=None,
        )

        request = TaskRequest(task_id="task-1", agent_id="agent-1", goal="Do work")
        result = await runtime.execute_task(request)

        assert result.run_id is not None
        persisted = await run_store.get(result.run_id)
        assert persisted.status == RunStatus.COMPLETED
        assert browser_service.saved_sessions[0]["run_id"] == result.run_id
        events_for_run = await store.get_runtime_events(run_id=result.run_id)
        assert any(event["run_id"] == result.run_id for event in events_for_run)

    asyncio.run(scenario())


def test_run_api_endpoints() -> None:
    async def scenario() -> RuntimeOrchestrator:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
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
        run = await orchestrator.run_store.create_run(
            task_id="task-2",
            agent_id="agent-1",
            project_id="development",
            correlation_id="task-2",
        )
        await orchestrator.event_bus.emit(EventType.TASK_UPDATED, run_id=run.run_id, agent_id="agent-1", task_id="task-2", payload={"ok": True})
        return orchestrator

    orchestrator = asyncio.run(scenario())
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
    headers = {"Authorization": f"Bearer {token}"}

    list_response = client.get("/api/runs", headers=headers)
    assert list_response.status_code == 200
    run_id = list_response.json()[0]["run_id"]

    get_response = client.get(f"/api/runs/{run_id}", headers=headers)
    assert get_response.status_code == 200

    events_response = client.get(f"/api/runs/{run_id}/events", headers=headers)
    assert events_response.status_code == 200
    assert any(event["run_id"] == run_id for event in events_response.json())

    timeline_response = client.get(f"/api/runs/{run_id}/timeline", headers=headers)
    assert timeline_response.status_code == 200
    assert timeline_response.json()["run_id"] == run_id
    assert timeline_response.json()["event_count"] >= 1
    assert "task" in timeline_response.json()["phases"]

    replay_response = client.get(f"/api/runs/{run_id}/replay", headers=headers)
    assert replay_response.status_code == 200
    assert replay_response.json()["run_id"] == run_id
    assert len(replay_response.json()["timeline"]) >= 1

    graph_response = client.get(f"/api/runs/{run_id}/graph", headers=headers)
    assert graph_response.status_code == 200
    assert graph_response.json()["root_run_id"] == run_id
    assert graph_response.json()["nodes"][0]["run_id"] == run_id

    children_response = client.get(f"/api/runs/{run_id}/children", headers=headers)
    assert children_response.status_code == 200
    assert children_response.json() == []

    cancel_response = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


def test_run_timeline_orders_events_and_groups_replay() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        run = await run_store.create_run(task_id="task-1", agent_id="agent-1", correlation_id="task-1")

        await store.store_runtime_event(
            "evt-2",
            {
                "event_id": "evt-2",
                "run_id": run.run_id,
                "event_type": "loop.acted",
                "timestamp": "2026-03-26T10:00:02+00:00",
                "phase": "act",
                "payload": {"action": "click"},
                "correlation_id": "task-1",
                "severity": "info",
                "source": "agent_loop",
            },
        )
        await store.store_runtime_event(
            "evt-1",
            {
                "event_id": "evt-1",
                "run_id": run.run_id,
                "event_type": "loop.planned",
                "timestamp": "2026-03-26T10:00:01+00:00",
                "phase": "plan",
                "payload": {"actions": [{"type": "click"}]},
                "correlation_id": "task-1",
                "severity": "info",
                "source": "agent_loop",
            },
        )
        await store.store_runtime_event(
            "evt-3",
            {
                "event_id": "evt-3",
                "run_id": run.run_id,
                "event_type": "budget.updated",
                "timestamp": "2026-03-26T10:00:03+00:00",
                "phase": "evaluate",
                "payload": {"usage": {"steps_used": 1}},
                "correlation_id": "task-1",
                "severity": "info",
                "source": "budget_service",
            },
        )

        timeline = await run_store.get_timeline(run.run_id)
        replay = await run_store.get_replay(run.run_id, checkpoints=[{"checkpoint_id": "cp-1"}])

        assert [entry.event_id for entry in timeline.entries] == ["evt-1", "evt-2", "evt-3"]
        assert timeline.phases == ["plan", "act", "evaluate"]
        assert replay.phase_transitions[0]["phase"] == "plan"
        assert replay.browser_actions[0]["event_id"] == "evt-2"
        assert replay.planner_outputs[0]["event_id"] == "evt-1"
        assert replay.budget_updates[0]["event_id"] == "evt-3"
        assert replay.checkpoints == [{"checkpoint_id": "cp-1"}]

    asyncio.run(scenario())


def test_task_runtime_creates_child_run_for_capability_delegation() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        parent_agent = registry.register(
            AgentDefinition(
                agent_id="research-agent",
                kind=AgentKind.CUSTOM,
                name="Research Agent",
                capability_tags=["web_scraping"],
            )
        )
        child_agent = registry.register(
            AgentDefinition(
                agent_id="analysis-agent",
                kind=AgentKind.A2A,
                name="Analysis Agent",
                capability_tags=["analysis"],
            )
        )
        await registry.save_to_store(parent_agent)
        await registry.save_to_store(child_agent)
        registry.build_adapter = lambda *args, **kwargs: _StubAdapter()  # type: ignore[method-assign]

        sockets = WebSocketManager(state_store=store)
        a2a = A2AHub(registry, state_store=store, sockets=sockets)
        a2a.register_agent(
            AgentRegistrationRequest(
                agent_id="analysis-agent",
                name="Analysis Agent",
                capabilities=["analysis"],
                verification_key="analysis-key",
            )
        )
        events = EventBus(sockets)
        browser_service = _StubBrowserService()
        checkpoint_service = CheckpointService(store, browser_service, events)
        run_store = RunStore(store)
        runtime = TaskRuntime(
            agents=registry,
            browser_service=browser_service,
            tool_service=_StubToolService(),
            memory_service=MemoryService(_StubMemoryManager()),
            task_manager=_StubTaskManager(),
            checkpoint_service=checkpoint_service,
            run_store=run_store,
            events=events,
            safety=_StubSafety(),
            llm=None,
            a2a=a2a,
        )
        a2a.set_task_executor(runtime.execute_task)

        result = await runtime.execute_task(
            TaskRequest(
                task_id="task-delegate",
                agent_id="research-agent",
                goal="Analyze the extracted paper",
                constraints={"required_capability": "analysis"},
            )
        )

        parent_run = await run_store.get(result.run_id)
        child_runs = [run for run in await run_store.list() if run.parent_run_id == parent_run.run_id]
        assert result.artifacts["delegated"] is True
        assert len(child_runs) == 1
        assert child_runs[0].agent_id == "analysis-agent"

        events_for_parent = await store.get_runtime_events(run_id=parent_run.run_id)
        event_types = {event["event_type"] for event in events_for_parent}
        assert EventType.TASK_DELEGATION_REQUESTED.value in event_types
        assert EventType.TASK_DELEGATION_COMPLETED.value in event_types

    asyncio.run(scenario())


def test_run_store_builds_multi_agent_graph() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)

        parent = await run_store.create_run(
            task_id="task-graph",
            agent_id="research-agent",
            correlation_id="task-graph",
            metadata={"goal": "Research and analyze"},
        )
        child = await run_store.create_run(
            task_id="task-graph",
            agent_id="analysis-agent",
            correlation_id="task-graph",
            parent_run_id=parent.run_id,
            metadata={"required_capability": "analysis", "delegated_by": "research-agent"},
        )
        await run_store.update_status(parent.run_id, RunStatus.RUNNING, metadata={"delegated_run_id": child.run_id})
        await run_store.update_status(child.run_id, RunStatus.COMPLETED)

        graph = await run_store.get_graph(parent.run_id)

        assert graph.root_run_id == parent.run_id
        assert graph.total_runs == 2
        assert graph.completed_runs == 1
        assert len(graph.edges) == 1
        assert graph.edges[0].source_run_id == parent.run_id
        assert graph.edges[0].target_run_id == child.run_id
        assert graph.edges[0].required_capability == "analysis"
        assert {node.run_id for node in graph.nodes} == {parent.run_id, child.run_id}

    asyncio.run(scenario())


def test_operator_intervention_transitions_run_and_resume() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
        await agents.save_to_store(agent)
        adapter = _CapturingAdapter()
        agents.build_adapter = lambda *args, **kwargs: adapter  # type: ignore[method-assign]

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
        run = await orchestrator.run_store.create_run(
            task_id="task-operator",
            agent_id="agent-1",
            correlation_id="task-operator",
            metadata={"goal": "Continue after approval"},
        )

        await orchestrator.event_bus.emit(
            EventType.APPROVAL_REQUIRED,
            run_id=run.run_id,
            agent_id=run.agent_id,
            task_id=run.task_id,
            source="browser_service",
            payload={"action": "upload", "reason": "Sensitive action", "operator_handoff": True},
        )

        waiting = await orchestrator.get_run(run.run_id)
        assert waiting.status == RunStatus.WAITING_FOR_OPERATOR
        assert waiting.checkpoint_id is not None
        assert waiting.metadata["operator_intervention"]["ui"]["operator_required"] is True
        interventions = await orchestrator.list_interventions(run_id=run.run_id)
        assert len(interventions) == 1
        assert interventions[0].state.value == "pending"

        with_input = await orchestrator.provide_run_input(
            run.run_id,
            operator_id="operator-1",
            input_payload={"note": "approved after review"},
        )
        assert with_input.metadata["operator_input"]["input"]["note"] == "approved after review"

        orchestrator.task_runtime.scheduler = None
        resumed = await orchestrator.approve_run(run.run_id, operator_id="operator-1")
        assert resumed.run_id == run.run_id
        completed = await orchestrator.get_run(run.run_id)
        assert completed.status == RunStatus.COMPLETED
        assert completed.metadata["operator_decision"] == "approved"
        assert adapter.requests[-1].constraints["operator_context"]["input"]["note"] == "approved after review"
        updated_intervention = await orchestrator.get_intervention(interventions[0].intervention_id)
        assert updated_intervention.state.value == "approved"
        assert updated_intervention.resolved_at is not None

    asyncio.run(scenario())


def test_operator_intervention_api_endpoints() -> None:
    async def scenario() -> RuntimeOrchestrator:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
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
        run = await orchestrator.run_store.create_run(
            task_id="task-api-operator",
            agent_id="agent-1",
            project_id="development",
            correlation_id="task-api-operator",
            metadata={"goal": "Need operator"},
        )
        await orchestrator.event_bus.emit(
            EventType.BROWSER_CAPTCHA_DETECTED,
            run_id=run.run_id,
            agent_id=run.agent_id,
            task_id=run.task_id,
            source="browser_service",
            payload={"reason": "Likely CAPTCHA", "challenge_type": "captcha", "operator_handoff": True},
        )
        return orchestrator

    orchestrator = asyncio.run(scenario())
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
    headers = {"Authorization": f"Bearer {token}"}

    run_id = client.get("/api/runs", headers=headers).json()[0]["run_id"]
    interventions = client.get("/api/interventions", headers=headers)
    assert interventions.status_code == 200
    assert len(interventions.json()) == 1
    intervention_id = interventions.json()[0]["intervention_id"]

    intervention_detail = client.get(f"/api/interventions/{intervention_id}", headers=headers)
    assert intervention_detail.status_code == 200
    assert intervention_detail.json()["run_id"] == run_id

    provide = client.post(f"/api/runs/{run_id}/provide_input", json={"captcha_note": "operator saw challenge"}, headers=headers)
    assert provide.status_code == 200
    assert provide.json()["metadata"]["operator_input"]["input"]["captcha_note"] == "operator saw challenge"

    reject = client.post(f"/api/interventions/{intervention_id}/reject", json={"reason": "Do not continue"}, headers=headers)
    assert reject.status_code == 200
    assert reject.json()["status"] == "cancelled"
    assert reject.json()["metadata"]["operator_decision"] == "rejected"
