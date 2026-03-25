from __future__ import annotations

from synapse.models.agent import AgentBudgetUsage
from synapse.models.runtime_event import EventSeverity, EventType
from synapse.runtime.budget import AgentBudgetLimitExceeded, AgentBudgetManager
from synapse.runtime.event_bus import EventBus
from synapse.runtime.registry import AgentRegistry


class BudgetService:
    def __init__(
        self,
        budget_manager: AgentBudgetManager,
        agents: AgentRegistry,
        events: EventBus,
    ) -> None:
        self.budget_manager = budget_manager
        self.agents = agents
        self.events = events

    def get_usage(self, agent_id: str) -> AgentBudgetUsage:
        self.agents.get(agent_id)
        return self.budget_manager.get_usage(agent_id)

    def ensure_budget(self, agent_id: str) -> AgentBudgetUsage:
        agent = self.agents.get(agent_id)
        self.budget_manager.get_or_create(agent)
        return self.budget_manager.get_usage(agent_id)

    async def increment_page(self, agent_id: str) -> AgentBudgetUsage:
        usage = self.budget_manager.increment_page(self.agents.get(agent_id))
        await self._publish_update(agent_id, usage)
        await self.check_limits(agent_id)
        return usage

    async def increment_tool_call(self, agent_id: str) -> AgentBudgetUsage:
        usage = self.budget_manager.increment_tool_call(self.agents.get(agent_id))
        await self._publish_update(agent_id, usage)
        await self.check_limits(agent_id)
        return usage

    async def increment_memory_write(self, agent_id: str) -> AgentBudgetUsage:
        usage = self.budget_manager.increment_memory_write(self.agents.get(agent_id))
        await self._publish_update(agent_id, usage)
        await self.check_limits(agent_id)
        return usage

    async def publish_usage(self, agent_id: str, warning: str | None = None) -> AgentBudgetUsage:
        usage = self.budget_manager.get_usage(agent_id)
        await self._publish_update(agent_id, usage, warning=warning)
        return usage

    async def check_limits(self, agent_id: str) -> None:
        agent = self.agents.get(agent_id)
        try:
            usage, warnings = self.budget_manager.check_limits(agent)
        except AgentBudgetLimitExceeded as exc:
            if agent.execution_policy.save_checkpoint_on_limit or agent.execution_policy.pause_on_hard_limit:
                self.budget_manager.save_checkpoint(
                    agent_id,
                    {"usage": self.budget_manager.get_usage(agent_id).model_dump(mode="json")},
                    reason=str(exc),
                )
            await self._publish_update(agent_id, self.budget_manager.get_usage(agent_id), warning=str(exc))
            raise

        if agent.execution_policy.stop_on_soft_limit and any("exceeded" in warning for warning in warnings):
            raise AgentBudgetLimitExceeded("Agent terminated: soft budget limit exceeded.")

        for warning in warnings:
            await self._publish_update(agent_id, usage, warning=warning)

    async def save_agent_checkpoint(
        self,
        agent_id: str,
        state: dict[str, object],
        reason: str | None = None,
    ):
        self.agents.get(agent_id)
        checkpoint = self.budget_manager.save_checkpoint(agent_id, state, reason)
        await self._publish_update(agent_id, self.budget_manager.get_usage(agent_id), warning=reason)
        return checkpoint

    async def _publish_update(
        self,
        agent_id: str,
        usage: AgentBudgetUsage,
        warning: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"usage": usage.model_dump(mode="json")}
        if warning is not None:
            payload["warning"] = warning
        await self.events.emit(
            EventType.BUDGET_UPDATED,
            agent_id=agent_id,
            task_id=task_id,
            session_id=session_id,
            source="budget_service",
            payload=payload,
            severity=EventSeverity.WARNING if warning else EventSeverity.INFO,
            correlation_id=correlation_id,
        )
