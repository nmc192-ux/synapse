import asyncio
from datetime import datetime, timezone

from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.browser import CompactStructuredPageModel, OpenRequest
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.memory import MemoryRecord, MemoryScope, MemoryStoreRequest, MemoryType
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
    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
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
            MemoryRecord(memory_id="m1", agent_id="agent-1", run_id="run-1", task_id="task-1", memory_type=MemoryType.SHORT_TERM, memory_scope=MemoryScope.RUN, content="Remember current page heading", embedding=[1.0, 0.0], timestamp=now),
            MemoryRecord(memory_id="m2", agent_id="agent-1", run_id="run-2", task_id="task-2", memory_type=MemoryType.SHORT_TERM, memory_scope=MemoryScope.RUN, content="Other run memory", embedding=[1.0, 0.0], timestamp=now),
            MemoryRecord(memory_id="m3", agent_id="agent-1", run_id="run-1", task_id="task-1", memory_type=MemoryType.TASK, memory_scope=MemoryScope.TASK, content="Task success: extracted paper list", embedding=[0.0, 1.0], timestamp=now),
            MemoryRecord(memory_id="m4", agent_id="agent-1", memory_type=MemoryType.LONG_TERM, memory_scope=MemoryScope.LONG_TERM, content="Important strategy: prefer navigation links before forms", embedding=[0.3, 0.7], timestamp=now),
        ]

    async def store(self, request):
        record = MemoryRecord.model_validate(request.model_dump(mode="json"))
        self.records.insert(0, record)
        return record

    async def search(self, request):
        return []

    async def get_recent(self, agent_id: str, limit: int = 10, *, run_id=None, task_id=None, memory_scope=None):
        records = [record for record in self.records if record.agent_id == agent_id]
        if run_id is not None:
            records = [record for record in records if record.run_id == run_id]
        if task_id is not None:
            records = [record for record in records if record.task_id == task_id]
        if memory_scope is not None:
            records = [record for record in records if record.memory_scope == memory_scope]
        return records[:limit]

    async def get_recent_by_type(self, agent_id: str, limit_per_type: int = 4, *, run_id=None, task_id=None, scopes=None):
        grouped: dict[MemoryType, list[MemoryRecord]] = {}
        for record in self.records:
            if record.agent_id != agent_id:
                continue
            if run_id is not None and record.run_id != run_id:
                continue
            if task_id is not None and record.task_id != task_id:
                continue
            if scopes is not None and record.memory_scope not in scopes:
                continue
            grouped.setdefault(record.memory_type, [])
            if len(grouped[record.memory_type]) < limit_per_type:
                grouped[record.memory_type].append(record)
        return grouped

    async def get_run_memory(self, run_id: str, limit: int = 100):
        return [record for record in self.records if record.run_id == run_id][:limit]


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


class _StubExecutionPlane:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        worker_id: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "run_id": run_id,
                "session_id": session_id,
                "worker_id": worker_id,
            }
        )
        return {"worker": worker_id, "ok": True}


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


def test_tool_service_routes_assigned_run_tools_to_execution_plane() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        await store.store_run(
            "run-1",
            {
                "run_id": "run-1",
                "task_id": "task-1",
                "agent_id": "agent-1",
                "status": "running",
                "metadata": {"assigned_worker_id": "worker-1"},
            },
        )
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
            return {"local": True}

        tools.register("demo.tool", handler, plugin_name=None)
        execution_plane = _StubExecutionPlane()
        service = ToolService(
            tools,
            AgentSecuritySandbox(registry),
            AgentSafetyLayer(),
            bus,
            budget,
            state_store=store,
            execution_plane=execution_plane,
        )
        result = await service.call_tool("demo.tool", {"value": 1}, agent_id="agent-1", run_id="run-1")
        assert result["worker"] == "worker-1"
        assert execution_plane.calls[0]["tool_name"] == "demo.tool"

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
                MemoryStoreRequest(agent_id="agent-1", run_id="run-1", task_id="task-1", memory_type=MemoryType.SHORT_TERM, content="hello", embedding=[0.1])
            )
            event = await queue.get()
            assert event.event_type == EventType.BUDGET_UPDATED
            assert record.content == "hello"
            assert record.memory_scope == MemoryScope.RUN

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
            payload = await service.get_planner_memory_context("agent-1", run_id="run-1", task_id="task-1", limit_per_type=4)
            event = await queue.get()
            assert event.event_type == EventType.MEMORY_COMPRESSED
            assert payload["retrieved_memory_count"] == 3
            assert payload["compressed_memory_count"] == 3
            assert payload["memory_compression_ratio"] == 1.0
            assert "short_term" in payload["memory_summary"]
            assert "task" in payload["memory_summary"]
            assert "long_term" in payload["memory_summary"]
            assert len(payload["memories"]) == 3

    asyncio.run(scenario())


def test_budget_service_isolates_concurrent_runs_for_same_agent() -> None:
    async def scenario() -> None:
        registry = AgentRegistry()
        registry.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent 1"))
        bus = EventBus(WebSocketManager(state_store=InMemoryRuntimeStateStore()))
        budget = BudgetService(AgentBudgetManager(), registry, bus)

        await budget.ensure_run_budget("agent-1", "run-1")
        await budget.ensure_run_budget("agent-1", "run-2")
        await budget.increment_step("agent-1", run_id="run-1")
        await budget.increment_step("agent-1", run_id="run-1")
        await budget.increment_step("agent-1", run_id="run-2")

        run_one = await budget.get_run_budget("run-1")
        run_two = await budget.get_run_budget("run-2")

        assert run_one.steps_used == 2
        assert run_two.steps_used == 1

    asyncio.run(scenario())


def test_memory_service_get_run_memory_isolates_same_agent_runs() -> None:
    async def scenario() -> None:
        service = MemoryService(_StubMemoryManager())
        run_one = await service.get_run_memory("run-1")
        run_two = await service.get_run_memory("run-2")

        assert all(record.run_id == "run-1" for record in run_one)
        assert all(record.run_id == "run-2" for record in run_two)
        assert {record.memory_id for record in run_one} == {"m1", "m3"}
        assert {record.memory_id for record in run_two} == {"m2"}

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
