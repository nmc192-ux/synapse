from __future__ import annotations

from synapse.models.agent import AgentBudgetUsage
from synapse.models.runtime_event import EventSeverity, EventType
from synapse.runtime.budget import AgentBudget, AgentBudgetLimitExceeded, AgentBudgetManager
from synapse.runtime.event_bus import EventBus
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore


class BudgetService:
    def __init__(
        self,
        budget_manager: AgentBudgetManager,
        agents: AgentRegistry,
        events: EventBus,
        run_store: RunStore | None = None,
    ) -> None:
        self.budget_manager = budget_manager
        self.agents = agents
        self.events = events
        self.run_store = run_store
        self._run_budgets: dict[str, AgentBudget] = {}

    def get_usage(self, agent_id: str) -> AgentBudgetUsage:
        self.agents.get(agent_id)
        return self.budget_manager.get_usage(agent_id)

    async def get_run_budget(self, run_id: str) -> AgentBudgetUsage:
        if run_id in self._run_budgets:
            return self._run_budgets[run_id].get_usage()
        if self.run_store is not None:
            persisted = await self.run_store.get_budget(run_id)
            if persisted is not None:
                return persisted
        raise KeyError(f"Run budget not found: {run_id}")

    def ensure_budget(self, agent_id: str) -> AgentBudgetUsage:
        agent = self.agents.get(agent_id)
        self.budget_manager.get_or_create(agent)
        return self.budget_manager.get_usage(agent_id)

    async def ensure_run_budget(self, agent_id: str, run_id: str) -> AgentBudgetUsage:
        return await self._get_or_create_run_budget(agent_id, run_id)

    async def increment_step(self, agent_id: str, *, run_id: str | None = None, task_id: str | None = None, session_id: str | None = None, correlation_id: str | None = None) -> AgentBudgetUsage:
        return await self._increment(agent_id, "step", run_id=run_id, task_id=task_id, session_id=session_id, correlation_id=correlation_id)

    async def increment_page(self, agent_id: str, *, run_id: str | None = None, task_id: str | None = None, session_id: str | None = None, correlation_id: str | None = None) -> AgentBudgetUsage:
        return await self._increment(agent_id, "page", run_id=run_id, task_id=task_id, session_id=session_id, correlation_id=correlation_id)

    async def increment_tool_call(self, agent_id: str, *, run_id: str | None = None, task_id: str | None = None, session_id: str | None = None, correlation_id: str | None = None) -> AgentBudgetUsage:
        return await self._increment(agent_id, "tool", run_id=run_id, task_id=task_id, session_id=session_id, correlation_id=correlation_id)

    async def increment_tokens(self, agent_id: str, amount: int, *, run_id: str | None = None, task_id: str | None = None, session_id: str | None = None, correlation_id: str | None = None) -> AgentBudgetUsage:
        return await self._increment(
            agent_id,
            "tokens",
            amount=amount,
            run_id=run_id,
            task_id=task_id,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    async def increment_memory_write(self, agent_id: str, *, run_id: str | None = None, task_id: str | None = None, session_id: str | None = None, correlation_id: str | None = None) -> AgentBudgetUsage:
        return await self._increment(agent_id, "memory", run_id=run_id, task_id=task_id, session_id=session_id, correlation_id=correlation_id)

    async def publish_usage(
        self,
        agent_id: str,
        *,
        run_id: str | None = None,
        warning: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentBudgetUsage:
        usage = await self._current_usage(agent_id, run_id=run_id)
        await self._publish_update(
            agent_id,
            usage,
            run_id=run_id,
            warning=warning,
            task_id=task_id,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        return usage

    async def check_limits(
        self,
        agent_id: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        agent = self.agents.get(agent_id)
        try:
            if run_id is None:
                usage, warnings = self.budget_manager.check_limits(agent)
            else:
                budget = await self._get_or_create_run_budget_state(agent_id, run_id)
                usage, warnings = self._check_run_limits(budget)
        except AgentBudgetLimitExceeded as exc:
            usage = await self._current_usage(agent_id, run_id=run_id)
            if agent.execution_policy.save_checkpoint_on_limit or agent.execution_policy.pause_on_hard_limit:
                self.budget_manager.save_checkpoint(
                    agent_id,
                    {"usage": usage.model_dump(mode="json"), "run_id": run_id, "task_id": task_id, "session_id": session_id},
                    reason=str(exc),
                )
            await self._publish_update(
                agent_id,
                usage,
                run_id=run_id,
                warning=str(exc),
                task_id=task_id,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            raise

        if agent.execution_policy.stop_on_soft_limit and any("exceeded" in warning for warning in warnings):
            raise AgentBudgetLimitExceeded("Agent terminated: soft budget limit exceeded.")

        for warning in warnings:
            await self._publish_update(
                agent_id,
                usage,
                run_id=run_id,
                warning=warning,
                task_id=task_id,
                session_id=session_id,
                correlation_id=correlation_id,
            )

    async def save_agent_checkpoint(
        self,
        agent_id: str,
        state: dict[str, object],
        reason: str | None = None,
    ):
        self.agents.get(agent_id)
        checkpoint = self.budget_manager.save_checkpoint(agent_id, state, reason)
        await self.publish_usage(agent_id, warning=reason)
        return checkpoint

    async def _increment(
        self,
        agent_id: str,
        metric: str,
        *,
        amount: int = 0,
        run_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentBudgetUsage:
        agent = self.agents.get(agent_id)
        if run_id is None:
            if metric == "step":
                usage = self.budget_manager.increment_step(agent)
            elif metric == "page":
                usage = self.budget_manager.increment_page(agent)
            elif metric == "tool":
                usage = self.budget_manager.increment_tool_call(agent)
            elif metric == "tokens":
                usage = self.budget_manager.increment_tokens(agent, amount)
            else:
                usage = self.budget_manager.increment_memory_write(agent)
        else:
            budget = await self._get_or_create_run_budget_state(agent_id, run_id)
            if metric == "step":
                budget.increment_step()
            elif metric == "page":
                budget.increment_page()
            elif metric == "tool":
                budget.increment_tool_call()
            elif metric == "tokens":
                budget.increment_tokens(amount)
            else:
                budget.increment_memory_write()
            usage = budget.get_usage()
            await self._persist_run_budget(run_id, usage)

        await self._publish_update(
            agent_id,
            usage,
            run_id=run_id,
            task_id=task_id,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        await self.check_limits(
            agent_id,
            run_id=run_id,
            task_id=task_id,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        return usage

    async def _current_usage(self, agent_id: str, *, run_id: str | None = None) -> AgentBudgetUsage:
        if run_id is None:
            return self.budget_manager.get_usage(agent_id)
        return await self.get_run_budget(run_id)

    async def _get_or_create_run_budget(self, agent_id: str, run_id: str) -> AgentBudgetUsage:
        budget = await self._get_or_create_run_budget_state(agent_id, run_id)
        usage = budget.get_usage()
        await self._persist_run_budget(run_id, usage)
        return usage

    async def _get_or_create_run_budget_state(self, agent_id: str, run_id: str) -> AgentBudget:
        budget = self._run_budgets.get(run_id)
        if budget is not None:
            return budget

        agent = self.agents.get(agent_id)
        budget = AgentBudget(limits=self.budget_manager.resolve_limits(agent))
        persisted = await self.run_store.get_budget(run_id) if self.run_store is not None else None
        if persisted is not None:
            budget.steps_used = persisted.steps_used
            budget.pages_opened = persisted.pages_opened
            budget.tool_calls = persisted.tool_calls
            budget.tokens_used = persisted.tokens_used
            budget.memory_writes = persisted.memory_writes
            budget.llm_cost_estimate = persisted.llm_cost_estimate
            budget.tool_cost_estimate = persisted.tool_cost_estimate
            budget.warnings = list(persisted.warnings)
        self._run_budgets[run_id] = budget
        return budget

    async def _persist_run_budget(self, run_id: str, usage: AgentBudgetUsage) -> None:
        if self.run_store is not None:
            await self.run_store.update_budget(run_id, usage)

    def _check_run_limits(self, budget: AgentBudget) -> tuple[AgentBudgetUsage, list[str]]:
        usage = budget.get_usage()
        limits = budget.limits
        if usage.steps_used >= limits.max_steps:
            raise AgentBudgetLimitExceeded("Agent terminated: step limit exceeded.")
        if usage.runtime_seconds >= limits.max_runtime_seconds:
            raise AgentBudgetLimitExceeded("Agent terminated: runtime limit exceeded.")

        warnings: list[str] = []
        warnings.extend(self.budget_manager._soft_warning("max_pages", usage.pages_opened, limits.max_pages, budget))
        warnings.extend(self.budget_manager._soft_warning("max_tool_calls", usage.tool_calls, limits.max_tool_calls, budget))
        warnings.extend(self.budget_manager._soft_warning("max_tokens", usage.tokens_used, limits.max_tokens, budget))
        warnings.extend(self.budget_manager._soft_warning("max_memory_writes", usage.memory_writes, limits.max_memory_writes, budget))
        return budget.get_usage(), warnings

    async def _publish_update(
        self,
        agent_id: str,
        usage: AgentBudgetUsage,
        *,
        run_id: str | None = None,
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
            run_id=run_id,
            agent_id=agent_id,
            task_id=task_id,
            session_id=session_id,
            source="budget_service",
            payload=payload,
            severity=EventSeverity.WARNING if warning else EventSeverity.INFO,
            correlation_id=correlation_id,
        )
