from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.sdk.client import SynapseClient
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope


def test_sdk_attaches_bearer_and_project_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        captured["project"] = request.headers.get("X-Synapse-Project-Id", "")
        return httpx.Response(200, json=[])

    client = SynapseClient(
        base_url="http://testserver",
        bearer_token="token-123",
        project_id="project-1",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.list_tools()
    finally:
        client.close()

    assert captured["authorization"] == "Bearer token-123"
    assert captured["project"] == "project-1"


def test_sdk_attaches_api_key_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        captured["api_key"] = request.headers.get("X-API-Key", "")
        return httpx.Response(200, json=[])

    client = SynapseClient(
        base_url="http://testserver",
        api_key="key-abc",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.list_tools()
    finally:
        client.close()

    assert captured["api_key"] == "key-abc"
    assert captured["authorization"] == ""


def test_sdk_bearer_takes_precedence_over_api_key() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        captured["api_key"] = request.headers.get("X-API-Key", "")
        return httpx.Response(200, json=[])

    client = SynapseClient(
        base_url="http://testserver",
        api_key="key-abc",
        bearer_token="token-123",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.list_tools()
    finally:
        client.close()

    assert captured["authorization"] == "Bearer token-123"
    assert captured["api_key"] == ""


def test_sdk_refreshes_bearer_token_on_401() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization", ""))
        if len(calls) == 1:
            return httpx.Response(401, json={"detail": "token expired"})
        return httpx.Response(200, json=[])

    refreshed = {"count": 0}

    def refresh() -> str:
        refreshed["count"] += 1
        return "token-new"

    client = SynapseClient(
        base_url="http://testserver",
        bearer_token="token-old",
        token_refresh_callback=refresh,
        transport=httpx.MockTransport(handler),
    )

    try:
        client.list_tools()
    finally:
        client.close()

    assert refreshed["count"] == 1
    assert calls == ["Bearer token-old", "Bearer token-new"]


def test_sdk_auth_failure_message_is_improved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Run is outside the caller project scope."})

    client = SynapseClient(
        base_url="http://testserver",
        bearer_token="token-123",
        project_id="project-a",
        transport=httpx.MockTransport(handler),
    )

    try:
        with pytest.raises(PermissionError) as exc_info:
            client.list_tools()
    finally:
        client.close()

    message = str(exc_info.value)
    assert "Authorization failed" in message
    assert "project-a" in message
    assert "outside the caller project scope" in message


def test_sdk_builds_hosted_websocket_url() -> None:
    client = SynapseClient(
        base_url="https://synapse.example.com",
        api_key="key-abc",
        project_id="project-1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )
    try:
        websocket_url = client.build_websocket_url()
    finally:
        client.close()

    assert websocket_url.startswith("wss://synapse.example.com/api/ws?")
    assert "api_key=key-abc" in websocket_url
    assert "project_id=project-1" in websocket_url


def test_sdk_api_key_auth_succeeds_against_real_route() -> None:
    class _StubOrchestrator:
        def __init__(self) -> None:
            self._agent = AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent 1",
                organization_id="org-1",
                project_id="project-1",
            )

        async def execute_task(self, request):
            return {"task_id": request.task_id, "status": "completed", "run_id": "run-1"}

        async def get_persisted_agent(self, agent_id: str):
            if agent_id != self._agent.agent_id:
                raise KeyError(agent_id)
            return self._agent

    settings = Settings(
        auth_required=True,
        jwt_secret="sdk-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)

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
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    app.dependency_overrides[get_orchestrator] = lambda: _StubOrchestrator()
    server = TestClient(app)

    def handler(request: httpx.Request) -> httpx.Response:
        response = server.request(
            request.method,
            request.url.path,
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(response.status_code, json=response.json())

    client = SynapseClient(
        base_url="http://testserver",
        api_key="synp_valid",
        project_id="project-1",
        transport=httpx.MockTransport(handler),
    )

    try:
        response = client._request(
            "POST",
            "/api/tasks",
            json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        )
    finally:
        client.close()
        server.close()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
