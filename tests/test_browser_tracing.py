import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_event import EventType
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.event_bus import EventBus
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.browser.session_manager import SessionManager
from synapse.security.auth import Authenticator
from synapse.transports.websocket_manager import WebSocketManager


class _FakeConsoleMessage:
    def type(self) -> str:
        return "warning"

    def text(self) -> str:
        return "Console warning"

    def location(self) -> dict[str, object]:
        return {"url": "https://example.com/app", "lineNumber": 3}


class _FakeRequest:
    url = "https://example.com/app.js"

    def method(self) -> str:
        return "GET"

    def resource_type(self) -> str:
        return "script"

    def failure(self) -> dict[str, object]:
        return {"errorText": "net::ERR_ABORTED"}


class _FakeFrame:
    url = "https://example.com/dashboard"

    def name(self) -> str:
        return "main"

    def parent_frame(self):
        return None


class _FakePopup:
    url = "https://accounts.example.com/login"

    async def title(self) -> str:
        return "Login"


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self._handlers: dict[str, list] = {}

    def on(self, event_name: str, handler) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    def emit(self, event_name: str, payload) -> None:
        for handler in self._handlers.get(event_name, []):
            handler(payload)

    async def title(self) -> str:
        return "Trace Page"

    async def evaluate(self, script: str, arg=None):
        return {"local_storage": {}, "session_storage": {}}

    async def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page

    async def new_page(self) -> _FakePage:
        return self.page

    async def cookies(self):
        return []

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

        return StructuredPageModel(title="Trace Page", url=page.url)


class _StubBrowserService:
    def __init__(self) -> None:
        self.browser = object()
        self.sandbox = object()
        self.budget_service = SimpleNamespace(budget_manager=AgentBudgetManager())


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


def test_session_manager_captures_browser_trace_events() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        bus = EventBus(WebSocketManager(state_store=store))
        page = _FakePage()
        context = _FakeContext(page)
        session_manager = SessionManager(
            settings=type("Settings", (), {"browser_headless": True, "browser_channel": None})(),
            state_store=store,
            event_publisher=bus.publish,
        )
        session_manager._browser = _FakeBrowser(context)

        await session_manager.create_session("session-1", _Extractor(), agent_id="agent-1", run_id="run-1")
        page.emit("console", _FakeConsoleMessage())
        page.emit("requestfailed", _FakeRequest())
        page.emit("framenavigated", _FakeFrame())
        page.emit("popup", _FakePopup())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        events = await store.get_runtime_events(run_id="run-1", limit=20)
        event_types = {event["event_type"] for event in events}
        assert EventType.BROWSER_CONSOLE_LOGGED.value in event_types
        assert EventType.BROWSER_NETWORK_FAILED.value in event_types
        assert EventType.BROWSER_NAVIGATION_TRACED.value in event_types
        assert EventType.BROWSER_POPUP_OPENED.value in event_types

    asyncio.run(scenario())


def test_run_trace_and_network_api_endpoints() -> None:
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
        run = await orchestrator.run_store.create_run(task_id="task-1", agent_id="agent-1", correlation_id="task-1")
        await store.store_runtime_event(
            "evt-console",
            {
                "event_id": "evt-console",
                "run_id": run.run_id,
                "event_type": "browser.console.logged",
                "timestamp": "2026-03-26T10:00:00+00:00",
                "phase": "act",
                "payload": {"level": "warning", "message": "Console warning", "url": "https://example.com/app"},
                "correlation_id": run.run_id,
                "severity": "warning",
                "source": "browser_session",
                "session_id": "session-1",
            },
        )
        await store.store_runtime_event(
            "evt-network",
            {
                "event_id": "evt-network",
                "run_id": run.run_id,
                "event_type": "browser.network.failed",
                "timestamp": "2026-03-26T10:00:01+00:00",
                "phase": "act",
                "payload": {
                    "url": "https://example.com/app.js",
                    "method": "GET",
                    "resource_type": "script",
                    "failure_text": "net::ERR_ABORTED",
                    "status": "failed",
                },
                "correlation_id": run.run_id,
                "severity": "warning",
                "source": "browser_session",
                "session_id": "session-1",
            },
        )
        await store.store_runtime_event(
            "evt-download",
            {
                "event_id": "evt-download",
                "run_id": run.run_id,
                "event_type": "download.completed",
                "timestamp": "2026-03-26T10:00:02+00:00",
                "phase": "act",
                "payload": {"artifact": {"suggested_filename": "report.pdf"}, "url": "https://example.com/report.pdf"},
                "correlation_id": run.run_id,
                "severity": "info",
                "source": "browser_service",
                "session_id": "session-1",
            },
        )
        return orchestrator

    orchestrator = asyncio.run(scenario())
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: Authenticator(Settings(auth_required=False))
    client = TestClient(app)

    runs_response = client.get("/api/runs")
    run_id = runs_response.json()[0]["run_id"]

    trace_response = client.get(f"/api/runs/{run_id}/trace")
    assert trace_response.status_code == 200
    trace_payload = trace_response.json()
    assert any(item["category"] == "console" for item in trace_payload)
    assert any(item["category"] == "download" for item in trace_payload)

    network_response = client.get(f"/api/runs/{run_id}/network")
    assert network_response.status_code == 200
    network_payload = network_response.json()
    assert network_payload[0]["url"] == "https://example.com/app.js"
    assert network_payload[0]["failure_text"] == "net::ERR_ABORTED"
