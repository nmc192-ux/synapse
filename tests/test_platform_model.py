import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_state import BrowserWorkerState, WorkerRuntimeStatus
from synapse.models.platform import (
    APIKeyCreateRequest,
    AgentOwnershipRequest,
    OrganizationCreateRequest,
    ProjectCreateRequest,
    UserCreateRequest,
)
from synapse.models.task import TaskRequest, TaskResult, TaskStatus
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.checkpoint_service import CheckpointService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
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

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return SimpleNamespace(session_id=session_id)

    async def save_session_state(self, *args, **kwargs):
        return None

    async def restore_session_state(self, *args, **kwargs):
        return None

    def list_workers(self):
        return [
            BrowserWorkerState(
                worker_id="browser-worker-1",
                queue_name="synapse:browser:worker:browser-worker-1",
                status=WorkerRuntimeStatus.IDLE,
            )
        ]


class _StubToolService:
    async def call_tool(self, *args, **kwargs):
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
        return TaskResult(task_id=request.task_id, run_id=request.run_id, status=TaskStatus.COMPLETED, message="done")


def test_platform_service_persists_tenant_records_and_project_scoped_token() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        settings = Settings(
            auth_required=True,
            jwt_secret="tenant-secret",
            jwt_issuer="synapse-test",
            jwt_audience="synapse-test-api",
        )
        authenticator = Authenticator(settings)
        registry = AgentRegistry(state_store=store)
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

        organization = await orchestrator.create_organization(OrganizationCreateRequest(name="Acme", slug="acme"))
        project = await orchestrator.create_project(
            ProjectCreateRequest(organization_id=organization.organization_id, name="Core", slug="core")
        )
        user = await orchestrator.create_user(
            UserCreateRequest(
                organization_id=organization.organization_id,
                project_ids=[project.project_id],
                email="ops@example.com",
                display_name="Ops",
            )
        )
        issued = await orchestrator.create_api_key(
            APIKeyCreateRequest(
                organization_id=organization.organization_id,
                project_id=project.project_id,
                user_id=user.user_id,
                name="SDK Key",
                scopes=[Scope.TASKS_READ.value],
            )
        )
        principal = authenticator.authenticate_token(issued.access_token)

        assert principal.project_id == project.project_id
        assert principal.organization_id == organization.organization_id
        assert principal.api_key_id == issued.record.api_key_id
        assert len(await orchestrator.list_projects(organization_id=organization.organization_id)) == 1

    asyncio.run(scenario())


def test_agent_ownership_and_run_project_propagation() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        settings = Settings(auth_required=False)
        authenticator = Authenticator(settings)
        registry = AgentRegistry(state_store=store)
        agent = registry.register(
            AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One")
        )
        await registry.save_to_store(agent)
        registry.build_adapter = lambda *args, **kwargs: _StubAdapter()  # type: ignore[method-assign]

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
        organization = await orchestrator.create_organization(OrganizationCreateRequest(name="Acme", slug="acme"))
        project = await orchestrator.create_project(
            ProjectCreateRequest(organization_id=organization.organization_id, name="Core", slug="core")
        )
        owner = await orchestrator.create_user(
            UserCreateRequest(
                organization_id=organization.organization_id,
                project_ids=[project.project_id],
                email="owner@example.com",
                display_name="Owner",
            )
        )

        ownership = await orchestrator.assign_agent_ownership(
            "agent-1",
            AgentOwnershipRequest(
                organization_id=organization.organization_id,
                project_id=project.project_id,
                owner_user_id=owner.user_id,
            ),
        )
        assert ownership.project_id == project.project_id

        result = await orchestrator.task_runtime.execute_task(TaskRequest(task_id="task-1", agent_id="agent-1", goal="Do work"))
        run = await orchestrator.get_run(result.run_id)
        assert run.project_id == project.project_id

        checkpoint_service = CheckpointService(store, _StubBrowserService(), EventBus(WebSocketManager(state_store=store)))
        checkpoint_service.remember_task_context(TaskRequest(task_id="task-1", agent_id="agent-1", goal="Do work", run_id=run.run_id))
        checkpoint = await checkpoint_service.save_checkpoint("task-1", {"agent_id": "agent-1", "run_id": run.run_id, "current_goal": "Do work"})
        assert checkpoint.project_id == project.project_id

    asyncio.run(scenario())


def test_platform_api_routes() -> None:
    store = InMemoryRuntimeStateStore()
    settings = Settings(
        auth_required=True,
        jwt_secret="platform-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    orchestrator = RuntimeOrchestrator(
        browser=_StubBrowserService(),
        agents=AgentRegistry(state_store=store),
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
    client = TestClient(app)
    token = authenticator.issue_token(
        subject="operator-1",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.ADMIN.value],
        organization_id="bootstrap-org",
        project_id="bootstrap-project",
    )
    headers = {"Authorization": f"Bearer {token}"}

    org_response = client.post("/api/platform/organizations", json={"name": "Acme", "slug": "acme"}, headers=headers)
    assert org_response.status_code == 200
    organization_id = org_response.json()["organization_id"]

    project_response = client.post(
        "/api/platform/projects",
        json={"organization_id": organization_id, "name": "Core", "slug": "core"},
        headers=headers,
    )
    assert project_response.status_code == 200

    list_response = client.get("/api/platform/projects", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()[0]["organization_id"] == organization_id


def test_hosted_control_plane_project_scoped_routes_and_audit_logs() -> None:
    store = InMemoryRuntimeStateStore()
    settings = Settings(
        auth_required=True,
        jwt_secret="cloud-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    registry = AgentRegistry(state_store=store)
    agent = registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
    registry.build_adapter = lambda *args, **kwargs: _StubAdapter()  # type: ignore[method-assign]
    asyncio.run(registry.save_to_store(agent))

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
    organization = asyncio.run(orchestrator.create_organization(OrganizationCreateRequest(name="Acme", slug="acme")))
    project = asyncio.run(
        orchestrator.create_project(ProjectCreateRequest(organization_id=organization.organization_id, name="Core", slug="core"))
    )
    owner = asyncio.run(
        orchestrator.create_user(
            UserCreateRequest(
                organization_id=organization.organization_id,
                project_ids=[project.project_id],
                email="ops@example.com",
                display_name="Ops",
            )
        )
    )
    asyncio.run(
        orchestrator.assign_agent_ownership(
            "agent-1",
            AgentOwnershipRequest(
                organization_id=organization.organization_id,
                project_id=project.project_id,
                owner_user_id=owner.user_id,
            ),
        )
    )

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    client = TestClient(app)

    project_token = authenticator.issue_token(
        subject="svc-1",
        principal_type=PrincipalType.SERVICE,
        scopes=[
            Scope.TASKS_WRITE.value,
            Scope.BROWSER_CONTROL.value,
            Scope.A2A_SEND.value,
            Scope.A2A_RECEIVE.value,
            Scope.ADMIN.value,
        ],
        organization_id=organization.organization_id,
        project_id=project.project_id,
    )
    headers = {"Authorization": f"Bearer {project_token}"}

    run_response = client.post(
        f"/api/cloud/projects/{project.project_id}/runs",
        json={"task_id": "task-cloud", "agent_id": "agent-1", "goal": "Do work"},
        headers=headers,
    )
    assert run_response.status_code == 200
    assert run_response.json()["project_id"] == project.project_id

    profile_response = client.post(
        f"/api/cloud/projects/{project.project_id}/profiles",
        json={"name": "cloud-profile", "agent_id": "agent-1"},
        headers=headers,
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["project_id"] == project.project_id

    capability_response = client.post(
        f"/api/cloud/projects/{project.project_id}/capabilities",
        json={
            "agent_id": "agent-1",
            "capabilities": ["web_scraping"],
            "description": "Hosted worker",
            "endpoint": "ws://agent-1",
            "latency": 12,
            "availability": True,
            "reputation": 0.8,
        },
        headers=headers,
    )
    assert capability_response.status_code == 200

    capabilities_list = client.get(f"/api/cloud/projects/{project.project_id}/capabilities", headers=headers)
    assert capabilities_list.status_code == 200
    assert capabilities_list.json()[0]["agent_id"] == "agent-1"

    capability_find = client.get(
        f"/api/cloud/projects/{project.project_id}/agents/find",
        params={"capability": "web_scraping"},
        headers=headers,
    )
    assert capability_find.status_code == 200
    assert capability_find.json()[0]["id"] == "agent-1"

    api_key_issue = client.post(
        f"/api/cloud/projects/{project.project_id}/api-keys",
        json={
            "organization_id": organization.organization_id,
            "project_id": project.project_id,
            "user_id": owner.user_id,
            "name": "Hosted SDK Key",
            "scopes": [Scope.TASKS_READ.value],
        },
        headers=headers,
    )
    assert api_key_issue.status_code == 200
    assert api_key_issue.json()["record"]["project_id"] == project.project_id

    audit_response = client.get(f"/api/cloud/projects/{project.project_id}/audit-logs", headers=headers)
    assert audit_response.status_code == 200
    actions = {entry["action"] for entry in audit_response.json()}
    assert "run.create" in actions
    assert "profile.create" in actions
    assert "capability.advertise" in actions
    assert "api_key.issue" in actions

    workers_response = client.get("/api/cloud/admin/workers", headers=headers)
    assert workers_response.status_code == 200
    assert workers_response.json()[0]["worker_id"] == "browser-worker-1"


def test_cross_project_resource_access_is_rejected() -> None:
    store = InMemoryRuntimeStateStore()
    settings = Settings(
        auth_required=True,
        jwt_secret="scope-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    registry = AgentRegistry(state_store=store)
    agent = registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
    registry.build_adapter = lambda *args, **kwargs: _StubAdapter()  # type: ignore[method-assign]
    asyncio.run(registry.save_to_store(agent))

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

    organization = asyncio.run(orchestrator.create_organization(OrganizationCreateRequest(name="Acme", slug="acme")))
    project_a = asyncio.run(orchestrator.create_project(ProjectCreateRequest(organization_id=organization.organization_id, name="A", slug="a")))
    project_b = asyncio.run(orchestrator.create_project(ProjectCreateRequest(organization_id=organization.organization_id, name="B", slug="b")))
    owner = asyncio.run(
        orchestrator.create_user(
            UserCreateRequest(
                organization_id=organization.organization_id,
                project_ids=[project_a.project_id, project_b.project_id],
                email="ops@example.com",
                display_name="Ops",
            )
        )
    )
    asyncio.run(
        orchestrator.assign_agent_ownership(
            "agent-1",
            AgentOwnershipRequest(
                organization_id=organization.organization_id,
                project_id=project_b.project_id,
                owner_user_id=owner.user_id,
            ),
        )
    )
    result = asyncio.run(orchestrator.task_runtime.execute_task(TaskRequest(task_id="task-cross", agent_id="agent-1", goal="Do work")))

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    client = TestClient(app)

    token = authenticator.issue_token(
        subject="svc-a",
        principal_type=PrincipalType.SERVICE,
        scopes=[Scope.TASKS_READ.value, Scope.TASKS_WRITE.value, Scope.BROWSER_CONTROL.value, Scope.ADMIN.value],
        organization_id=organization.organization_id,
        project_id=project_a.project_id,
    )
    headers = {"Authorization": f"Bearer {token}"}

    run_response = client.get(f"/api/runs/{result.run_id}", headers=headers)
    assert run_response.status_code == 403

    agent_response = client.get("/api/agents/agent-1", headers=headers)
    assert agent_response.status_code == 403

    profile_response = client.post(
        "/api/profiles/create",
        json={"name": "bad-profile", "agent_id": "agent-1"},
        headers=headers,
    )
    assert profile_response.status_code == 403


def test_api_key_auth_works_end_to_end_and_enforces_project_scope() -> None:
    store = InMemoryRuntimeStateStore()
    settings = Settings(
        auth_required=True,
        jwt_secret="api-key-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    registry = AgentRegistry(state_store=store)
    agent = registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
    registry.build_adapter = lambda *args, **kwargs: _StubAdapter()  # type: ignore[method-assign]
    asyncio.run(registry.save_to_store(agent))

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

    organization = asyncio.run(orchestrator.create_organization(OrganizationCreateRequest(name="Acme", slug="acme")))
    project = asyncio.run(
        orchestrator.create_project(ProjectCreateRequest(organization_id=organization.organization_id, name="Core", slug="core"))
    )
    owner = asyncio.run(
        orchestrator.create_user(
            UserCreateRequest(
                organization_id=organization.organization_id,
                project_ids=[project.project_id],
                email="owner@example.com",
                display_name="Owner",
            )
        )
    )
    asyncio.run(
        orchestrator.assign_agent_ownership(
            "agent-1",
            AgentOwnershipRequest(
                organization_id=organization.organization_id,
                project_id=project.project_id,
                owner_user_id=owner.user_id,
            ),
        )
    )
    issued = asyncio.run(
        orchestrator.create_api_key(
            APIKeyCreateRequest(
                organization_id=organization.organization_id,
                project_id=project.project_id,
                user_id=owner.user_id,
                name="Hosted SDK",
                scopes=[Scope.TASKS_WRITE.value, Scope.TASKS_READ.value],
            )
        )
    )

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    client = TestClient(app)

    ok = client.post(
        f"/api/cloud/projects/{project.project_id}/runs",
        json={"task_id": "task-1", "agent_id": "agent-1", "goal": "Do work"},
        headers={"X-API-Key": issued.api_key, "X-Synapse-Project-Id": project.project_id},
    )
    assert ok.status_code == 200

    wrong = client.post(
        "/api/cloud/projects/project-other/runs",
        json={"task_id": "task-2", "agent_id": "agent-1", "goal": "Do work"},
        headers={"X-API-Key": issued.api_key, "X-Synapse-Project-Id": "project-other"},
    )
    assert wrong.status_code == 403

    refreshed_keys = asyncio.run(orchestrator.list_api_keys(project_id=project.project_id))
    assert refreshed_keys[0].last_used_at is not None
