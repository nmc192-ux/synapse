from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope


class _StubOrchestrator:
    def __init__(self) -> None:
        self.sockets = SimpleNamespace(connect=self._connect, disconnect=lambda websocket: None)
        self.connected = False
        self._agent = AgentDefinition(
            agent_id="agent-1",
            kind=AgentKind.CUSTOM,
            name="Agent 1",
            organization_id="org-1",
            project_id="project-1",
        )

    async def execute_task(self, request):
        return {"task_id": request.task_id, "status": "completed"}

    async def get_persisted_agent(self, agent_id: str):
        if agent_id != self._agent.agent_id:
            raise KeyError(agent_id)
        return self._agent

    async def _connect(self, websocket, principal=None):
        self.connected = principal is not None
        await websocket.accept()


def _build_client() -> tuple[TestClient, Authenticator, _StubOrchestrator]:
    settings = Settings(
        auth_required=True,
        jwt_secret="test-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    orchestrator = _StubOrchestrator()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    return TestClient(app), authenticator, orchestrator


def test_missing_token_returns_401() -> None:
    client, _, _ = _build_client()
    response = client.post("/api/tasks", json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"})
    assert response.status_code == 401


def test_invalid_token_returns_401() -> None:
    client, _, _ = _build_client()
    response = client.post(
        "/api/tasks",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        headers={"Authorization": "Bearer invalid.token.value"},
    )
    assert response.status_code == 401


def test_insufficient_scope_returns_403() -> None:
    client, authenticator, _ = _build_client()
    token = authenticator.issue_token(
        subject="operator-1",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-1",
    )
    response = client.post(
        "/api/tasks",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_successful_http_and_websocket_auth() -> None:
    client, authenticator, orchestrator = _build_client()
    token = authenticator.issue_token(
        subject="operator-1",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_WRITE.value, Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-1",
    )
    response = client.post(
        "/api/tasks",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    with client.websocket_connect(f"/api/ws?token={token}") as websocket:
        websocket.close()
    assert orchestrator.connected is True


def test_successful_api_key_http_and_websocket_auth() -> None:
    client, authenticator, orchestrator = _build_client()

    async def validate_api_key(raw_secret: str, project_id: str | None):
        assert raw_secret == "synp_valid"
        assert project_id == "project-1"
        return authenticator.authenticate_token(
            authenticator.issue_token(
                subject="svc-key-1",
                principal_type=PrincipalType.SERVICE,
                scopes=[Scope.TASKS_WRITE.value, Scope.TASKS_READ.value],
                organization_id="org-1",
                project_id="project-1",
                api_key_id="key-1",
            )
        )

    authenticator.set_api_key_validator(validate_api_key)

    response = client.post(
        "/api/tasks",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        headers={"X-API-Key": "synp_valid", "X-Synapse-Project-Id": "project-1"},
    )
    assert response.status_code == 200

    with client.websocket_connect("/api/ws?api_key=synp_valid&project_id=project-1") as websocket:
        websocket.close()
    assert orchestrator.connected is True


def test_expired_or_revoked_api_key_is_rejected() -> None:
    client, authenticator, _ = _build_client()

    async def validate_api_key(raw_secret: str, project_id: str | None):
        if raw_secret == "synp_expired":
            raise PermissionError("API key has expired.")
        raise PermissionError("API key has been revoked.")

    authenticator.set_api_key_validator(validate_api_key)

    expired = client.post(
        "/api/tasks",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        headers={"X-API-Key": "synp_expired", "X-Synapse-Project-Id": "project-1"},
    )
    revoked = client.post(
        "/api/tasks",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        headers={"X-API-Key": "synp_revoked", "X-Synapse-Project-Id": "project-1"},
    )

    assert expired.status_code == 401
    assert revoked.status_code == 401


def test_websocket_missing_token_is_rejected() -> None:
    client, _, _ = _build_client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws"):
            pass
