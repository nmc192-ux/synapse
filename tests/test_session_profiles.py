import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.runtime.event_bus import EventBus
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.task_runtime import TaskRuntime
from synapse.runtime.session_profiles import SessionProfileCreateRequest, SessionProfileManager
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.security.auth import Authenticator
from synapse.transports.websocket_manager import WebSocketManager


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self._evaluated = []

    async def goto(self, url: str) -> None:
        self.url = url

    async def wait_for_load_state(self, state: str) -> None:
        return None

    async def evaluate(self, script: str, arg=None):
        self._evaluated.append(arg)
        if "collect(window.localStorage)" in script:
            return {"local_storage": {}, "session_storage": {}}
        return None

    async def title(self) -> str:
        return "Profile Page"


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.added_cookies = []

    async def new_page(self) -> _FakePage:
        return self.page

    async def cookies(self):
        return []

    async def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    async def close(self) -> None:
        return None


class _FakeBrowser:
    def __init__(self, context: _FakeContext) -> None:
        self.context = context

    async def new_context(self) -> _FakeContext:
        return self.context


class _Extractor:
    async def snapshot_page(self, page: _FakePage):
        from synapse.models.browser import StructuredPageModel

        return StructuredPageModel(title="Profile Page", url=page.url)


class _StubBrowserService:
    def __init__(self) -> None:
        self.browser = object()
        self.sandbox = object()
        self.budget_service = type("BudgetService", (), {"budget_manager": AgentBudgetManager()})()

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return type("Session", (), {"session_id": session_id})()

    async def save_session_state(
        self,
        session_id: str,
        agent_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        return None

    async def restore_session_state(
        self,
        session_id: str,
        agent_id: str | None = None,
        checkpoint_id: str | None = None,
        run_id: str | None = None,
    ):
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


def test_session_profile_manager_crud_and_expiration_event() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        manager = SessionProfileManager(store, event_publisher=bus.publish)

        await store.store_run(
            "run-1",
            {"run_id": "run-1", "task_id": "task-1", "agent_id": "agent-1", "status": "running", "metadata": {}},
        )

        profile = await manager.create_profile(
            SessionProfileCreateRequest(
                name="Research Login",
                agent_id="agent-1",
                cookies=[{"name": "sid", "value": "abc", "domain": "example.com"}],
                storage_by_origin={
                    "https://example.com": {
                        "local_storage": {"token": "abc"},
                        "session_storage": {"tab": "1"},
                    }
                },
                auth_state_by_domain={"example.com": {"authenticated": True}},
            )
        )
        loaded = await manager.load_profile(profile.profile_id, run_id="run-1")
        assert loaded.profile_id == profile.profile_id

        run_payload = await store.get_run("run-1")
        assert run_payload["metadata"]["session_profile_id"] == profile.profile_id
        assert len(await manager.list_profiles(agent_id="agent-1")) == 1

        expired = await manager.create_profile(
            SessionProfileCreateRequest(
                name="Expired",
                agent_id="agent-1",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
        async with bus.subscribe("profile-test") as queue:
            try:
                await manager.load_profile(expired.profile_id, run_id="run-1")
            except ValueError:
                pass
            event = await queue.get()
            assert event.event_type.value == "session.profile.expired"
            assert event.payload["profile_id"] == expired.profile_id

        await manager.delete_profile(profile.profile_id)
        assert len(await manager.list_profiles(agent_id="agent-1")) == 1

    asyncio.run(scenario())


def test_session_manager_applies_attached_profile_to_new_session() -> None:
    async def scenario() -> None:
        from synapse.runtime.browser.session_manager import SessionManager

        store = InMemoryRuntimeStateStore()
        manager = SessionProfileManager(store)
        profile = await manager.create_profile(
            SessionProfileCreateRequest(
                name="Attached",
                agent_id="agent-1",
                cookies=[{"name": "sid", "value": "abc", "domain": "example.com"}],
                storage_by_origin={
                    "https://example.com": {
                        "local_storage": {"token": "abc"},
                        "session_storage": {"mode": "test"},
                    }
                },
                auth_state_by_domain={"example.com": {"authenticated": True}},
            )
        )
        await store.store_run(
            "run-1",
            {
                "run_id": "run-1",
                "task_id": "task-1",
                "agent_id": "agent-1",
                "status": "running",
                "metadata": {"session_profile_id": profile.profile_id},
            },
        )

        page = _FakePage()
        context = _FakeContext(page)
        session_manager = SessionManager(
            settings=type("Settings", (), {"browser_headless": True, "browser_channel": None})(),
            state_store=store,
            profile_manager=manager,
        )
        session_manager._browser = _FakeBrowser(context)

        await session_manager.create_session("session-1", _Extractor(), agent_id="agent-1", run_id="run-1")
        assert context.added_cookies[0]["name"] == "sid"
        assert page.url == "https://example.com"
        restore_args = next(arg for arg in page._evaluated if isinstance(arg, dict) and "localStorageData" in arg)
        assert restore_args["localStorageData"]["token"] == "abc"
        assert restore_args["sessionStorageData"]["mode"] == "test"

    asyncio.run(scenario())


def test_profile_api_endpoints() -> None:
    async def scenario() -> RuntimeOrchestrator:
        store = InMemoryRuntimeStateStore()
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        agents = AgentRegistry(state_store=store)
        agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
        await agents.save_to_store(agent)
        orchestrator = RuntimeOrchestrator(
            browser=_StubBrowserService(),
            agents=agents,
            tools=type("Tools", (), {})(),
            messages=type("Messages", (), {})(),
            a2a=type("A2A", (), {})(),
            memory_manager=_StubMemoryManager(),
            task_manager=_StubTaskManager(),
            sockets=sockets,
            sandbox=type("Sandbox", (), {"set_state_store": lambda self, store: None})(),
            safety=_StubSafety(),
            budget_manager=AgentBudgetManager(),
            state_store=store,
            llm=None,
        )
        orchestrator.event_bus = bus
        orchestrator.session_profiles.set_state_store(store)
        orchestrator.session_profiles.set_event_publisher(bus.publish)
        return orchestrator

    orchestrator = asyncio.run(scenario())
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: Authenticator(Settings(auth_required=False))
    client = TestClient(app)

    create_response = client.post(
        "/api/profiles/create",
        json={
            "name": "API Profile",
            "agent_id": "agent-1",
            "cookies": [{"name": "sid", "value": "abc"}],
            "auth_state_by_domain": {"example.com": {"authenticated": True}},
        },
    )
    assert create_response.status_code == 200
    profile_id = create_response.json()["profile_id"]

    list_response = client.get("/api/profiles")
    assert list_response.status_code == 200
    assert any(item["profile_id"] == profile_id for item in list_response.json())

    delete_response = client.delete(f"/api/profiles/{profile_id}")
    assert delete_response.status_code == 204
