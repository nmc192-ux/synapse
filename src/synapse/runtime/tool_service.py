from __future__ import annotations

from time import perf_counter

from synapse.models.runtime_event import EventSeverity, EventType
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.security import AgentSecuritySandbox, SandboxApprovalRequiredError
from synapse.runtime.safety import AgentSafetyLayer, SecurityAlertError, SecurityFinding
from synapse.runtime.state_store import RuntimeStateStore
from synapse.runtime.tools import ToolRegistry


class ToolService:
    def __init__(
        self,
        tools: ToolRegistry,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        events: EventBus,
        budget_service: BudgetService,
        state_store: RuntimeStateStore | None = None,
        execution_plane=None,
    ) -> None:
        self.tools = tools
        self.sandbox = sandbox
        self.safety = safety
        self.events = events
        self.budget_service = budget_service
        self.state_store = state_store
        self.execution_plane = execution_plane

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    def set_execution_plane(self, execution_plane) -> None:
        self.execution_plane = execution_plane

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        agent_id: str | None,
        *,
        run_id: str | None = None,
    ) -> dict[str, object]:
        effective_run_id = run_id or self._run_id_from_arguments(arguments)
        await self._hydrate_run_policy(effective_run_id)
        tool_descriptor = self.tools.describe(tool_name)
        is_plugin_tool = tool_descriptor.plugin is not None
        try:
            await self._enforce_tool_safety(agent_id, tool_name, arguments)
            self.sandbox.authorize_tool(agent_id, tool_name, run_id=effective_run_id)
            external_url = self.safety.find_external_request_url(arguments)
            if external_url is not None:
                self.sandbox.authorize_external_request(
                    agent_id,
                    external_url,
                    run_id=effective_run_id,
                    tool_name=tool_name,
                )
            self.sandbox.consume_tool_call(agent_id)
            if agent_id:
                await self.budget_service.increment_tool_call(agent_id, run_id=effective_run_id)
            start = perf_counter()
            if is_plugin_tool:
                await self.events.emit(
                    EventType.PLUGIN_EXECUTION_STARTED,
                    run_id=effective_run_id,
                    agent_id=agent_id,
                    source="tool_service",
                    payload=self._plugin_telemetry_payload(tool_name, tool_descriptor, arguments),
                )
            assigned_worker_id = await self._assigned_worker_id(effective_run_id)
            if assigned_worker_id is not None and self.execution_plane is not None:
                result = await self.execution_plane.call_tool(
                    tool_name,
                    arguments,
                    run_id=effective_run_id,
                    worker_id=assigned_worker_id,
                )
            else:
                result = await self.tools.call(tool_name, arguments)
            duration_ms = round((perf_counter() - start) * 1000, 2)
            await self.events.emit(
                EventType.TOOL_CALLED,
                run_id=effective_run_id,
                agent_id=agent_id,
                source="tool_service",
                payload={"tool_name": tool_name, "arguments": arguments, "result": result},
            )
            if is_plugin_tool:
                await self.events.emit(
                    EventType.PLUGIN_EXECUTION_COMPLETED,
                    run_id=effective_run_id,
                    agent_id=agent_id,
                    source="tool_service",
                    payload={
                        **self._plugin_telemetry_payload(tool_name, tool_descriptor, arguments),
                        "duration_ms": duration_ms,
                        "result_keys": sorted(result.keys()),
                    },
                )
            return result
        except SandboxApprovalRequiredError as exc:
            await self.events.emit(
                EventType.APPROVAL_REQUIRED,
                run_id=effective_run_id,
                agent_id=agent_id,
                source="tool_service",
                payload={
                    "action": exc.action,
                    "reason": exc.reason,
                    "tool_name": tool_name,
                    **exc.metadata,
                },
                severity=EventSeverity.WARNING,
            )
            raise
        except Exception as exc:
            if is_plugin_tool:
                await self.events.emit(
                    EventType.PLUGIN_EXECUTION_FAILED,
                    run_id=effective_run_id,
                    agent_id=agent_id,
                    source="tool_service",
                    payload={
                        **self._plugin_telemetry_payload(tool_name, tool_descriptor, arguments),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    severity=EventSeverity.ERROR,
                )
            raise

    def list_tools(self) -> list[ToolDescriptor]:
        return self.tools.list_tools()

    def list_plugins(self) -> list[PluginDescriptor]:
        return self.tools.list_plugins()

    def reload_plugins(self, request: PluginReloadRequest) -> list[PluginDescriptor]:
        self.tools.load_plugins(module_names=request.modules)
        return self.tools.list_plugins()

    async def _enforce_tool_safety(
        self,
        agent_id: str | None,
        tool_name: str,
        arguments: dict[str, object],
    ) -> None:
        finding = self.safety.validate_tool_call(tool_name, arguments)
        if finding is not None:
            await self._raise_security_alert(agent_id, finding)

    async def _raise_security_alert(self, agent_id: str | None, finding: SecurityFinding) -> None:
        await self.events.emit(
            EventType.SECURITY_ALERT,
            agent_id=agent_id,
            source="tool_service",
            payload=finding.model_dump(mode="json"),
            severity=EventSeverity.ERROR,
        )
        raise SecurityAlertError(finding)

    async def _hydrate_run_policy(self, run_id: str | None) -> None:
        if run_id is None or self.state_store is None:
            return
        payload = await self.state_store.get_run(run_id)
        if payload is None:
            return
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return
        for key in ("security_policy", "execution_policy"):
            override = metadata.get(key)
            if isinstance(override, dict):
                self.sandbox.set_run_policy(run_id, override)
                return

    async def _assigned_worker_id(self, run_id: str | None) -> str | None:
        if run_id is None or self.state_store is None:
            return None
        payload = await self.state_store.get_run(run_id)
        if payload is None:
            return None
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return None
        worker_id = metadata.get("assigned_worker_id")
        return str(worker_id) if isinstance(worker_id, str) and worker_id else None

    @staticmethod
    def _run_id_from_arguments(arguments: dict[str, object]) -> str | None:
        run_id = arguments.get("run_id")
        return str(run_id) if isinstance(run_id, str) and run_id else None

    @staticmethod
    def _plugin_telemetry_payload(
        tool_name: str,
        tool_descriptor: ToolDescriptor,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        return {
            "tool_name": tool_name,
            "plugin_name": tool_descriptor.plugin,
            "execution_mode": tool_descriptor.execution_mode.value,
            "isolation_strategy": tool_descriptor.isolation_strategy,
            "argument_keys": sorted(arguments.keys()),
        }
