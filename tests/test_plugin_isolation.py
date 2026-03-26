import asyncio

from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.plugin import PluginExecutionMode
from synapse.models.runtime_event import EventSeverity, EventType
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.tool_service import ToolService
from synapse.runtime.tools import ToolRegistry
from synapse.transports.websocket_manager import WebSocketManager


def test_isolated_plugin_executes_in_subprocess() -> None:
    async def scenario() -> None:
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
        )
        tools.load_module("synapse.testing.isolated_plugin")

        result = await tools.call("isolated.echo", {"value": "ok"})

        assert result == {"echo": "ok", "mode": "isolated"}
        descriptor = tools.describe("isolated.echo")
        assert descriptor.execution_mode == PluginExecutionMode.ISOLATED_HOSTED
        assert descriptor.isolation_strategy == "subprocess"

    asyncio.run(scenario())


def test_isolated_plugin_timeout_is_enforced() -> None:
    async def scenario() -> None:
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=0.1,
        )
        tools.load_module("synapse.testing.isolated_plugin")

        try:
            await tools.call("isolated.echo", {"value": "ok", "sleep": 0.5})
        except TimeoutError as exc:
            assert "timed out" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("expected timeout")

    asyncio.run(scenario())


def test_tool_service_emits_plugin_failure_telemetry() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry()
        registry.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent 1",
                security={"allowed_tools": ["isolated.echo"]},
            )
        )
        bus = EventBus(WebSocketManager(state_store=store))
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
        )
        tools.load_module("synapse.testing.isolated_plugin")
        service = ToolService(tools, AgentSecuritySandbox(registry), AgentSafetyLayer(), bus, budget)

        async with bus.subscribe("subscriber") as queue:
            try:
                await service.call_tool("isolated.echo", {"fail": True}, agent_id="agent-1")
            except RuntimeError as exc:
                assert "isolated failure" in str(exc)
            else:  # pragma: no cover - defensive
                raise AssertionError("expected plugin failure")

            event_types = [await queue.get(), await queue.get(), await queue.get()]
            assert event_types[0].event_type == EventType.BUDGET_UPDATED
            assert event_types[1].event_type == EventType.PLUGIN_EXECUTION_STARTED
            assert event_types[2].event_type == EventType.PLUGIN_EXECUTION_FAILED
            assert event_types[2].severity == EventSeverity.ERROR

    asyncio.run(scenario())
