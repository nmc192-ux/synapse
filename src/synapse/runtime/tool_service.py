from __future__ import annotations

from synapse.models.runtime_event import EventSeverity, EventType
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer, SecurityAlertError, SecurityFinding
from synapse.runtime.tools import ToolRegistry


class ToolService:
    def __init__(
        self,
        tools: ToolRegistry,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        events: EventBus,
        budget_service: BudgetService,
    ) -> None:
        self.tools = tools
        self.sandbox = sandbox
        self.safety = safety
        self.events = events
        self.budget_service = budget_service

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        agent_id: str | None,
    ) -> dict[str, object]:
        await self._enforce_tool_safety(agent_id, tool_name, arguments)
        self.sandbox.authorize_tool(agent_id, tool_name)
        self.sandbox.consume_tool_call(agent_id)
        if agent_id:
            await self.budget_service.increment_tool_call(agent_id)
        result = await self.tools.call(tool_name, arguments)
        await self.events.emit(
            EventType.TOOL_CALLED,
            agent_id=agent_id,
            source="tool_service",
            payload={"tool_name": tool_name, "arguments": arguments, "result": result},
        )
        return result

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
