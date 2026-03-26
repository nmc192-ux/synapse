from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.run import RunState
from synapse.models.runtime_event import EventType, RuntimeEvent
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope
from synapse.transports.websocket_manager import WebSocketManager


class _StubOrchestrator:
    def __init__(self) -> None:
        self.sockets = WebSocketManager()
        self.event_bus = None
        self.runs = {
            "run-project-a": RunState(
                run_id="run-project-a",
                task_id="task-a",
                agent_id="agent-a",
                project_id="project-a",
                correlation_id="corr-a",
                metadata={},
            ),
            "run-project-b": RunState(
                run_id="run-project-b",
                task_id="task-b",
                agent_id="agent-b",
                project_id="project-b",
                correlation_id="corr-b",
                metadata={},
            ),
        }

    async def get_run(self, run_id: str) -> RunState:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise KeyError(run_id) from exc


def _build_app() -> tuple[TestClient, Authenticator, _StubOrchestrator]:
    settings = Settings(
        auth_required=True,
        jwt_secret="ws-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    orchestrator = _StubOrchestrator()

    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.post("/emit")
    async def emit_event(payload: dict[str, object]) -> dict[str, bool]:
        await orchestrator.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType(str(payload["event_type"])),
                organization_id=str(payload.get("organization_id")) if payload.get("organization_id") is not None else None,
                project_id=str(payload.get("project_id")) if payload.get("project_id") is not None else None,
                run_id=str(payload.get("run_id")) if payload.get("run_id") is not None else None,
                agent_id=str(payload.get("agent_id")) if payload.get("agent_id") is not None else None,
                correlation_id=str(payload.get("correlation_id")) if payload.get("correlation_id") is not None else None,
                payload=dict(payload.get("payload", {})) if isinstance(payload.get("payload"), dict) else {},
                source="test",
            )
        )
        return {"ok": True}

    app.dependency_overrides[get_authenticator] = lambda: authenticator
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    return TestClient(app), authenticator, orchestrator


def test_websocket_events_are_isolated_by_project() -> None:
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
            emit_a = client.post(
                "/emit",
                json={
                    "event_type": "task.updated",
                    "organization_id": "org-1",
                    "project_id": "project-a",
                    "run_id": "run-project-a",
                    "agent_id": "agent-a",
                    "correlation_id": "corr-a",
                    "payload": {"label": "project-a"},
                },
            )
            assert emit_a.status_code == 200
            event_a = ws_a.receive_json()
            assert event_a["project_id"] == "project-a"
            assert event_a["payload"]["label"] == "project-a"

            emit_b = client.post(
                "/emit",
                json={
                    "event_type": "task.updated",
                    "organization_id": "org-1",
                    "project_id": "project-b",
                    "run_id": "run-project-b",
                    "agent_id": "agent-b",
                    "correlation_id": "corr-b",
                    "payload": {"label": "project-b"},
                },
            )
            assert emit_b.status_code == 200
            event_b = ws_b.receive_json()
            assert event_b["project_id"] == "project-b"
            assert event_b["payload"]["label"] == "project-b"


def test_websocket_events_can_be_filtered_to_a_run() -> None:
    client, authenticator, _ = _build_app()
    token = authenticator.issue_token(
        subject="operator-a",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-a",
    )

    with client.websocket_connect(f"/api/ws?token={token}&run_id=run-project-a") as websocket:
        skip = client.post(
            "/emit",
            json={
                "event_type": "task.updated",
                "organization_id": "org-1",
                "project_id": "project-a",
                "run_id": "run-project-b",
                "agent_id": "agent-b",
                "correlation_id": "corr-b",
                "payload": {"label": "other-run"},
            },
        )
        assert skip.status_code == 200

        match = client.post(
            "/emit",
            json={
                "event_type": "task.updated",
                "organization_id": "org-1",
                "project_id": "project-a",
                "run_id": "run-project-a",
                "agent_id": "agent-a",
                "correlation_id": "corr-a",
                "payload": {"label": "target-run"},
            },
        )
        assert match.status_code == 200

        event = websocket.receive_json()
        assert event["run_id"] == "run-project-a"
        assert event["payload"]["label"] == "target-run"


def test_websocket_run_filter_rejects_cross_project_runs() -> None:
    client, authenticator, _ = _build_app()
    token = authenticator.issue_token(
        subject="operator-a",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-a",
    )

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/ws?token={token}&run_id=run-project-b"):
            pass
