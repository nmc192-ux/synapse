from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.platform import APIKeyCreateRequest, APIKeyStatus, OrganizationCreateRequest, ProjectCreateRequest, UserCreateRequest
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.security.auth import Authenticator
from synapse.security.policies import Scope
from synapse.transports.websocket_manager import WebSocketManager


class _StubBrowserService:
    def __init__(self) -> None:
        self.browser = object()
        self.sandbox = object()
        self.budget_service = SimpleNamespace(budget_manager=AgentBudgetManager())

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return SimpleNamespace(session_id=session_id)

    async def save_session_state(self, *args, **kwargs):
        return None

    async def restore_session_state(self, *args, **kwargs):
        return None


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


class _StubMemoryManager:
    async def store(self, request):
        return request

    async def search(self, request):
        return []

    async def get_recent(self, agent_id: str, limit: int = 10):
        return []

    async def get_recent_by_type(self, agent_id: str, limit_per_type: int = 4):
        return {}


def _build_hosted_client() -> tuple[TestClient, RuntimeOrchestrator]:
    store = InMemoryRuntimeStateStore()
    settings = Settings(
        auth_required=True,
        jwt_secret="api-key-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    registry = AgentRegistry(state_store=store)
    registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
    orchestrator = RuntimeOrchestrator(
        browser=_StubBrowserService(),
        agents=registry,
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
        authenticator=authenticator,
    )
    orchestrator.scheduler = None
    orchestrator.task_runtime.scheduler = None

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    return TestClient(app), orchestrator


def test_api_key_wrong_project_revoked_and_expired_are_rejected() -> None:
    client, orchestrator = _build_hosted_client()

    organization = asyncio.run(orchestrator.create_organization(OrganizationCreateRequest(name="Acme", slug="acme")))
    project = asyncio.run(
        orchestrator.create_project(ProjectCreateRequest(organization_id=organization.organization_id, name="Core", slug="core"))
    )
    user = asyncio.run(
        orchestrator.create_user(
            UserCreateRequest(
                organization_id=organization.organization_id,
                project_ids=[project.project_id],
                email="ops@example.com",
                display_name="Ops",
            )
        )
    )
    issued = asyncio.run(
        orchestrator.create_api_key(
            APIKeyCreateRequest(
                organization_id=organization.organization_id,
                project_id=project.project_id,
                user_id=user.user_id,
                name="Hosted Key",
                scopes=[Scope.TASKS_WRITE.value],
            )
        )
    )

    wrong_project = client.post(
        "/api/cloud/projects/project-other/runs",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "noop"},
        headers={"X-API-Key": issued.api_key, "X-Synapse-Project-Id": "project-other"},
    )
    assert wrong_project.status_code == 403

    revoked = issued.record.model_copy(update={"status": APIKeyStatus.REVOKED})
    asyncio.run(orchestrator.state_store.store_api_key(revoked.api_key_id, revoked.model_dump(mode="json")))
    revoked_response = client.get(
        f"/api/cloud/projects/{project.project_id}/capabilities",
        headers={"X-API-Key": issued.api_key, "X-Synapse-Project-Id": project.project_id},
    )
    assert revoked_response.status_code == 401

    issued = asyncio.run(
        orchestrator.create_api_key(
            APIKeyCreateRequest(
                organization_id=organization.organization_id,
                project_id=project.project_id,
                user_id=user.user_id,
                name="Expired Key",
                scopes=[Scope.TASKS_WRITE.value],
                expires_at="2000-01-01T00:00:00Z",
            )
        )
    )
    expired_response = client.get(
        f"/api/cloud/projects/{project.project_id}/capabilities",
        headers={"X-API-Key": issued.api_key, "X-Synapse-Project-Id": project.project_id},
    )
    assert expired_response.status_code == 401
