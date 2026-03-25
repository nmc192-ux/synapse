import asyncio
from datetime import datetime, timezone

from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.browser import CompactStructuredPageModel, OpenRequest
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.memory import MemoryRecord, MemoryStoreRequest, MemoryType
from synapse.models.plugin import PluginReloadRequest
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.browser_service import BrowserService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.tool_service import ToolService
from synapse.runtime.tools import ToolRegistry
from synapse.transports.websocket_manager import WebSocketManager


class _StubBrowser:
    async def create_session(self, session_id: str, agent_id: str | None = None):
        return type("Session", (), {"session_id": session_id})()

    async def open(self, session_id: str, url: str):
        page = type(
            "Page",
            (),
            {
                "title": "Example",
                "url": url,
                "sections": [],
                "buttons": [],
                "inputs": [],
                "forms": [],
                "tables": [],
                "links": [],
                "compact_spm": CompactStructuredPageModel(title="Example", url=url, page_summary="compact"),
                "model_dump": lambda self, mode="json": {
                    "title": "Example",
                    "url": url,
                    "sections": [],
                    "buttons": [],
                    "inputs": [],
                    "forms": [],
                    "tables": [],
                    "links": [],
                    "compact_spm": {"title": "Example", "url": url, "page_summary": "compact", "semantic_regions": [], "grouped_elements": [], "actionable_elements": [], "table_summaries": [], "form_summaries": []},
                },
            },
        )()
        return type(
            "State",
            (),
            {
                "session_id": session_id,
                "page": page,
                "metadata": {},
                "model_dump": lambda self, mode="json": {
                    "session_id": session_id,
                    "page": page.model_dump(),
                    "metadata": {},
                },
            },
        )()

    async def get_layout(self, session_id: str):
        return type("Page", (), {"title": "Example", "url": "https://example.com", "sections": [], "buttons": [], "inputs": [], "forms": [], "tables": [], "links": [], "model_dump": lambda self, mode="json": {"title": "Example", "url": "https://example.com", "sections": [], "buttons": [], "inputs": [], "forms": [], "tables": [], "links": []}})()

    def current_url(self, session_id: str) -> str:
        return "https://example.com"

    async def list_sessions(self, agent_id: str | None = None):
        return []


class _StubMemoryManager:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.records = [
            MemoryRecord(memory_id="m1", agent_id="agent-1", memory_type=MemoryType.SHORT_TERM, content="Remember current page heading", embedding=[1.0, 0.0], timestamp=now),
            MemoryRecord(memory_id="m2", agent_id="agent-1", memory_type=MemoryType.SHORT_TERM, content="Remember current page heading", embedding=[1.0, 0.0], timestamp=now),
            MemoryRecord(memory_id="m3", agent_id="agent-1", memory_type=MemoryType.TASK, content="Task success: extracted paper list", embedding=[0.0, 1.0], timestamp=now),
            MemoryRecord(memory_id="m4", agent_id="agent-1", memory_type=MemoryType.LONG_TERM, content="Important strategy: prefer navigation links before forms", embedding=[0.3, 0.7], timestamp=now),
        ]

    async def store(self, request):
        return request

    async def search(self, request):
        return []

    async def get_recent(self, agent_id: str, limit: int = 10):
        return self.records[:limit]

    async def get_recent_by_type(self, agent_id: str, limit_per_type: int = 4):
        grouped: dict[MemoryType, list[MemoryRecord]] = {}
        for record in self.records:
            grouped.setdefault(record.memory_type, [])
            if len(grouped[record.memory_type]) < limit_per_type:
                grouped[record.memory_type].append(record)
        return grouped


class _StubCompressionProvider(CompressionProvider):
    async def compress_text(self, text: str, context: dict | None = None) -> str:
        return text

    async def compress_json(self, data: dict, context: dict | None = None) -> dict:
        return data

    async def summarize_events(self, events: list[dict], context: dict | None = None) -> dict:
        return {"count": len(events)}

    async def summarize_memory(self, memories: list[dict], context: dict | None = None) -> dict:
        memory_type = (context or {}).get("memory_type", "unknown")
        return {
            "summary": f"{memory_type}:{len(memories)}",
            "count": len(memories),
        }


async def _capture_events(bus: EventBus, count: int = 1) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    async with bus.subscribe("test-subscriber") as queue:
        for _ in range(count):
            events.append(await queue.get())
    return events


def test_tool_service_invokes_and_emits_event() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry()
        registry.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent 1",
                security={"allowed_domains": ["example.com"], "allowed_tools": ["demo.tool"]},
            )
        )
        bus = EventBus(WebSocketManager(state_store=store))
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        tools = ToolRegistry()

        async def handler(arguments: dict[str, object]) -> dict[str, object]:
            return {"ok": arguments.get("value")}

        tools.register("demo.tool", handler, plugin_name=None)
        service = ToolService(tools, AgentSecuritySandbox(registry), AgentSafetyLayer(), bus, budget)

        async with bus.subscribe("subscriber") as queue:
            result = await service.call_tool("demo.tool", {"value": 1}, agent_id="agent-1")
            event = await queue.get()
            assert result == {"ok": 1}
            assert event.event_type == EventType.BUDGET_UPDATED
            event = await queue.get()
            assert event.event_type == EventType.TOOL_CALLED

    asyncio.run(scenario())


def test_memory_service_applies_budget_tracking() -> None:
    async def scenario() -> None:
        registry = AgentRegistry()
        registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent 1"))
        bus = EventBus(WebSocketManager(state_store=InMemoryRuntimeStateStore()))
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        service = MemoryService(_StubMemoryManager(), budget)

        async with bus.subscribe("subscriber") as queue:
            record = await service.store(
                MemoryStoreRequest(agent_id="agent-1", memory_type=MemoryType.SHORT_TERM, content="hello", embedding=[0.1])
            )
            event = await queue.get()
            assert event.event_type == EventType.BUDGET_UPDATED
            assert record.content == "hello"

    asyncio.run(scenario())


def test_memory_service_compresses_planner_context() -> None:
    async def scenario() -> None:
        registry = AgentRegistry()
        registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent 1"))
        bus = EventBus(WebSocketManager(state_store=InMemoryRuntimeStateStore()))
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        service = MemoryService(
            _StubMemoryManager(),
            budget,
            state_store=InMemoryRuntimeStateStore(),
            events=bus,
            compression_provider=_StubCompressionProvider(),
        )

        async with bus.subscribe("subscriber") as queue:
            payload = await service.get_planner_memory_context("agent-1", task_id="task-1", limit_per_type=4)
            event = await queue.get()
            assert event.event_type == EventType.MEMORY_COMPRESSED
            assert payload["retrieved_memory_count"] == 4
            assert payload["compressed_memory_count"] == 3
            assert payload["memory_compression_ratio"] == 0.75
            assert "short_term" in payload["memory_summary"]
            assert "task" in payload["memory_summary"]
            assert "long_term" in payload["memory_summary"]
            assert len(payload["memories"]) == 3

    asyncio.run(scenario())


def test_browser_service_open_delegates_and_emits_navigation() -> None:
    async def scenario() -> None:
        registry = AgentRegistry()
        registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent 1", security={"allowed_domains": ["example.com"], "allowed_tools": []}))
        bus = EventBus(WebSocketManager(state_store=InMemoryRuntimeStateStore()))
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        browser_service = BrowserService(_StubBrowser(), AgentSecuritySandbox(registry), AgentSafetyLayer(), bus, budget)

        async with bus.subscribe("subscriber") as queue:
            state = await browser_service.open(OpenRequest(session_id="session-1", agent_id="agent-1", url="https://example.com"))
            first = await queue.get()
            second = await queue.get()
            third = await queue.get()
            assert state.session_id == "session-1"
            assert first.event_type == EventType.BUDGET_UPDATED
            assert second.event_type == EventType.PAGE_NAVIGATED
            assert third.event_type == EventType.SPM_COMPRESSED

    asyncio.run(scenario())


def test_event_bus_emits_compressed_summary_for_repetitive_events() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        sockets = WebSocketManager(state_store=store, compression_provider=_StubCompressionProvider())
        bus = EventBus(sockets, compression_provider=_StubCompressionProvider())

        async with bus.subscribe("subscriber") as queue:
            await bus.emit(EventType.BUDGET_UPDATED, agent_id="agent-1", payload={"steps_used": 1})
            await bus.emit(EventType.BUDGET_UPDATED, agent_id="agent-1", payload={"steps_used": 2})
            await bus.emit(EventType.BUDGET_UPDATED, agent_id="agent-1", payload={"steps_used": 3})
            events = [await queue.get(), await queue.get(), await queue.get(), await queue.get()]
            assert events[-1].event_type == EventType.RUNTIME_EVENTS_COMPRESSED
            assert events[-1].payload["event_count"] == 3

        history = await sockets.get_compact_event_history(agent_id="agent-1")
        assert history["count"] >= 4
        assert history["summary"]["count"] >= 4

    asyncio.run(scenario())
