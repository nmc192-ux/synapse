import asyncio
import os
from pathlib import Path

from synapse.config import settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.plugin import PluginExecutionMode, PluginTrustLevel
from synapse.models.runtime_event import EventSeverity, EventType
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.plugin_isolation import (
    HostedPluginIsolationBackend,
    HostedPluginIsolationUnavailableError,
    PluginExecutionRequest,
    PluginExecutionResult,
    ProcessGuardIsolationBackend,
)
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.tool_service import ToolService
from synapse.runtime.tools import ToolRegistry
from synapse.transports.websocket_manager import WebSocketManager


class _StubRealIsolationBackend:
    isolation_strategy = "sandbox_exec"

    def __init__(self) -> None:
        self._delegate = ProcessGuardIsolationBackend()

    def is_available(self) -> bool:
        return True

    def supports_untrusted_plugins(self) -> bool:
        return True

    async def execute(self, request: PluginExecutionRequest) -> PluginExecutionResult:
        result = await self._delegate.execute(request)
        result.isolation_strategy = self.isolation_strategy
        return result


def test_hosted_plugin_is_allowed_with_real_isolation_backend() -> None:
    async def scenario() -> None:
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            isolation_backend=_StubRealIsolationBackend(),
        )
        tools.load_module("synapse.testing.isolated_plugin")

        result = await tools.call("isolated.echo", {"value": "ok"})

        assert result == {"echo": "ok", "mode": "isolated"}
        descriptor = tools.describe("isolated.echo")
        assert descriptor.execution_mode == PluginExecutionMode.ISOLATED_HOSTED
        assert descriptor.isolation_strategy == "sandbox_exec"

    asyncio.run(scenario())


def test_hosted_plugin_timeout_is_enforced() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=0.1,
            isolation_backend=_StubRealIsolationBackend(),
            state_store=store,
        )
        tools.load_module("synapse.testing.isolated_plugin")

        try:
            await tools.call("isolated.echo", {"value": "ok", "sleep": 0.5}, run_id="run-timeout", project_id="project-1")
        except TimeoutError as exc:
            assert "timed out" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("expected timeout")

        audit_logs = await store.list_audit_logs(project_id="project-1", limit=10)
        metadata = audit_logs[-1]["metadata"]
        assert metadata["timeout"] is True

    asyncio.run(scenario())


def test_hosted_plugin_filters_environment_and_captures_audit_logs() -> None:
    async def scenario() -> None:
        os.environ["SECRET_TOKEN"] = "should-not-leak"
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            isolation_backend=_StubRealIsolationBackend(),
        )
        tools.load_module("synapse.testing.isolated_plugin")

        result = await tools.call(
            "isolated.echo",
            {"env_key": "SECRET_TOKEN", "print_stdout": True, "print_stderr": True},
        )

        assert result == {"value": None}
        audit = tools.list_plugin_audit_logs(limit=1)[0]
        assert audit["status"] == "ok"
        assert audit["isolation_mode"] == "sandbox_exec"
        assert "plugin stdout message" in str(audit["stdout"])
        assert "plugin stderr message" in str(audit["stderr"])

    asyncio.run(scenario())


def test_hosted_plugin_blocks_network_and_repo_filesystem_access() -> None:
    async def scenario() -> None:
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            isolation_backend=_StubRealIsolationBackend(),
        )
        tools.load_module("synapse.testing.isolated_plugin")
        repo_file = Path(__file__).resolve().parents[1] / "README.md"

        try:
            await tools.call("isolated.echo", {"network": True})
        except RuntimeError as exc:
            assert "cannot open network connections" in str(exc)
        else:
            raise AssertionError("expected blocked network access")

        try:
            await tools.call("isolated.echo", {"read_path": str(repo_file)})
        except RuntimeError as exc:
            assert "cannot read" in str(exc).lower()
        else:
            raise AssertionError("expected blocked filesystem read")

    asyncio.run(scenario())


def test_hosted_plugin_audit_logs_are_persisted_durably() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            state_store=store,
            isolation_backend=_StubRealIsolationBackend(),
        )
        tools.load_module("synapse.testing.isolated_plugin")

        await tools.call("isolated.echo", {"value": "ok"}, run_id="run-1", project_id="project-1")

        audit_logs = await store.list_audit_logs(project_id="project-1", limit=10)
        plugin_logs = [entry for entry in audit_logs if entry.get("action") == "plugin.execution"]
        assert plugin_logs
        metadata = plugin_logs[-1]["metadata"]
        assert metadata["plugin_name"] == "isolated_plugin"
        assert metadata["run_id"] == "run-1"
        assert metadata["project_id"] == "project-1"
        assert metadata["trust_level"] == PluginTrustLevel.TRUSTED_INTERNAL.value
        assert metadata["mode"] == PluginExecutionMode.ISOLATED_HOSTED.value
        assert metadata["isolation_mode"] == "sandbox_exec"
        assert metadata["timeout"] is False
        assert metadata["stdout_ref"]
        assert metadata["stderr_ref"]

    asyncio.run(scenario())


def test_hosted_plugins_are_rejected_when_isolation_backend_unavailable() -> None:
    async def scenario() -> None:
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            isolation_backend=HostedPluginIsolationBackend(backend_name="none"),
        )
        tools.load_module("synapse.testing.isolated_plugin")

        try:
            await tools.call("isolated.echo", {"value": "ok"})
        except HostedPluginIsolationUnavailableError as exc:
            assert "backend unavailable" in str(exc).lower()
        else:
            raise AssertionError("expected hosted isolation rejection")

    asyncio.run(scenario())


def test_hosted_untrusted_plugins_are_denied_by_policy() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            state_store=store,
            isolation_backend=_StubRealIsolationBackend(),
        )
        plugin = tools.register_plugin(
            name="external-tooling",
            module="external.plugin",
            capabilities=["echo"],
            endpoint="external",
        )
        assert plugin.trust_level == PluginTrustLevel.UNTRUSTED_EXTERNAL
        tools.register("external.echo", lambda arguments: asyncio.sleep(0, result={"echo": arguments}), plugin_name="external-tooling")

        try:
            await tools.call("external.echo", {"value": "blocked"}, run_id="run-1", project_id="project-1")
        except PermissionError as exc:
            assert "denied" in str(exc).lower()
        else:
            raise AssertionError("expected hosted policy denial")

        audit_logs = await store.list_audit_logs(project_id="project-1", limit=10)
        assert any(
            entry.get("action") == "plugin.execution"
            and "hosted_policy_denied" in entry.get("metadata", {}).get("policy_violations", [])
            for entry in audit_logs
        )

    asyncio.run(scenario())


def test_hosted_untrusted_plugins_can_run_when_real_backend_and_policy_enable_them() -> None:
    async def scenario() -> None:
        original = settings.hosted_plugin_allow_untrusted_external
        settings.hosted_plugin_allow_untrusted_external = True
        try:
            tools = ToolRegistry(
                execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
                execution_timeout_seconds=1.0,
                isolation_backend=_StubRealIsolationBackend(),
            )
            tools.load_module("synapse.testing.isolated_plugin")
            plugin = tools.list_plugins()[0]
            plugin.trust_level = PluginTrustLevel.UNTRUSTED_EXTERNAL
            tools.describe("isolated.echo").trust_level = PluginTrustLevel.UNTRUSTED_EXTERNAL
            result = await tools.call("isolated.echo", {"value": "ok"})
            assert result == {"echo": "ok", "mode": "isolated"}
        finally:
            settings.hosted_plugin_allow_untrusted_external = original

    asyncio.run(scenario())


def test_local_trusted_and_untrusted_plugins_remain_callable() -> None:
    async def scenario() -> None:
        tools = ToolRegistry(execution_mode=PluginExecutionMode.TRUSTED_LOCAL)
        tools.register_plugin(
            name="external-tooling",
            module="external.plugin",
            capabilities=["echo"],
            endpoint="external",
        )
        tools.register("external.echo", lambda arguments: asyncio.sleep(0, result={"echo": arguments}), plugin_name="external-tooling")

        result = await tools.call("external.echo", {"value": "ok"})
        assert result == {"echo": {"value": "ok"}}

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
                organization_id="org-1",
                project_id="project-1",
                security={"allowed_tools": ["isolated.echo"]},
            )
        )
        bus = EventBus(WebSocketManager(state_store=store))
        bus.set_context_resolver(lambda event: _plugin_event_context())
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            isolation_backend=_StubRealIsolationBackend(),
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


async def _plugin_event_context() -> dict[str, object]:
    return {"organization_id": "org-1", "project_id": "project-1"}
