from __future__ import annotations

from dataclasses import dataclass, field
import logging
from time import monotonic

from synapse.config import settings
from synapse.models.agent import AgentBudgetUsage, AgentCheckpoint, AgentDefinition, AgentExecutionLimits


class AgentBudgetLimitExceeded(RuntimeError):
    pass


logger = logging.getLogger(__name__)


@dataclass
class AgentBudget:
    limits: AgentExecutionLimits
    steps_used: int = 0
    pages_opened: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    memory_writes: int = 0
    start_time: float = field(default_factory=monotonic)
    llm_cost_estimate: float = 0.0
    tool_cost_estimate: float = 0.0
    warnings: list[str] = field(default_factory=list)
    _warning_markers: set[str] = field(default_factory=set)

    def increment_step(self) -> None:
        self.steps_used += 1

    def increment_page(self) -> None:
        self.pages_opened += 1

    def increment_tool_call(self) -> None:
        self.tool_calls += 1
        self.tool_cost_estimate = round(self.tool_calls * 0.001, 6)

    def increment_tokens(self, amount: int) -> None:
        self.tokens_used += max(0, amount)
        self.llm_cost_estimate = round(self.tokens_used * 0.000002, 6)

    def increment_memory_write(self) -> None:
        self.memory_writes += 1

    def runtime_seconds(self) -> int:
        return int(max(0, monotonic() - self.start_time))

    def get_usage(self) -> AgentBudgetUsage:
        return AgentBudgetUsage(
            steps_used=self.steps_used,
            pages_opened=self.pages_opened,
            tool_calls=self.tool_calls,
            tokens_used=self.tokens_used,
            memory_writes=self.memory_writes,
            runtime_seconds=self.runtime_seconds(),
            llm_cost_estimate=self.llm_cost_estimate,
            tool_cost_estimate=self.tool_cost_estimate,
            limits=self.limits,
            warnings=list(self.warnings),
        )


class AgentBudgetManager:
    def __init__(self, default_limits: AgentExecutionLimits | None = None) -> None:
        self.default_limits = default_limits or settings.agent_limits
        self._budgets: dict[str, AgentBudget] = {}
        self._checkpoints: dict[str, AgentCheckpoint] = {}

    def get_or_create(self, agent: AgentDefinition) -> AgentBudget:
        budget = self._budgets.get(agent.agent_id)
        if budget is None:
            budget = AgentBudget(limits=self.resolve_limits(agent))
            self._budgets[agent.agent_id] = budget
        return budget

    def resolve_limits(self, agent: AgentDefinition) -> AgentExecutionLimits:
        return self.default_limits.merged(agent.limits)

    def get_usage(self, agent_id: str) -> AgentBudgetUsage:
        budget = self._budgets.get(agent_id)
        if budget is None:
            return AgentBudget(limits=self.default_limits).get_usage()
        return budget.get_usage()

    def increment_step(self, agent: AgentDefinition) -> AgentBudgetUsage:
        budget = self.get_or_create(agent)
        budget.increment_step()
        return budget.get_usage()

    def increment_page(self, agent: AgentDefinition) -> AgentBudgetUsage:
        budget = self.get_or_create(agent)
        budget.increment_page()
        return budget.get_usage()

    def increment_tool_call(self, agent: AgentDefinition) -> AgentBudgetUsage:
        budget = self.get_or_create(agent)
        budget.increment_tool_call()
        return budget.get_usage()

    def increment_tokens(self, agent: AgentDefinition, amount: int) -> AgentBudgetUsage:
        budget = self.get_or_create(agent)
        budget.increment_tokens(amount)
        return budget.get_usage()

    def increment_memory_write(self, agent: AgentDefinition) -> AgentBudgetUsage:
        budget = self.get_or_create(agent)
        budget.increment_memory_write()
        return budget.get_usage()

    def check_limits(self, agent: AgentDefinition) -> tuple[AgentBudgetUsage, list[str]]:
        budget = self.get_or_create(agent)
        usage = budget.get_usage()
        limits = budget.limits

        if usage.steps_used >= limits.max_steps:
            logger.warning("Agent terminated: step limit exceeded.")
            raise AgentBudgetLimitExceeded("Agent terminated: step limit exceeded.")
        if usage.runtime_seconds >= limits.max_runtime_seconds:
            logger.warning("Agent terminated: runtime limit exceeded.")
            raise AgentBudgetLimitExceeded("Agent terminated: runtime limit exceeded.")

        warnings: list[str] = []
        warnings.extend(self._soft_warning("max_pages", usage.pages_opened, limits.max_pages, budget))
        warnings.extend(self._soft_warning("max_tool_calls", usage.tool_calls, limits.max_tool_calls, budget))
        warnings.extend(self._soft_warning("max_tokens", usage.tokens_used, limits.max_tokens, budget))
        warnings.extend(self._soft_warning("max_memory_writes", usage.memory_writes, limits.max_memory_writes, budget))

        usage = budget.get_usage()
        return usage, warnings

    def save_checkpoint(
        self,
        agent_id: str,
        state: dict[str, object],
        reason: str | None = None,
    ) -> AgentCheckpoint:
        checkpoint = AgentCheckpoint(agent_id=agent_id, state=state, reason=reason)
        self._checkpoints[agent_id] = checkpoint
        return checkpoint

    def get_checkpoint(self, agent_id: str) -> AgentCheckpoint | None:
        return self._checkpoints.get(agent_id)

    @staticmethod
    def _soft_warning(metric: str, value: int, limit: int, budget: AgentBudget) -> list[str]:
        warnings: list[str] = []
        threshold_marker = f"{metric}:80"
        exceeded_marker = f"{metric}:100"

        if limit > 0 and value >= int(limit * 0.8) and threshold_marker not in budget._warning_markers:
            warning = f"Agent budget warning: 80% of {metric} reached."
            budget._warning_markers.add(threshold_marker)
            budget.warnings.append(warning)
            logger.warning(warning)
            warnings.append(warning)

        if limit > 0 and value > limit and exceeded_marker not in budget._warning_markers:
            warning = f"Agent budget warning: {metric} exceeded."
            budget._warning_markers.add(exceeded_marker)
            budget.warnings.append(warning)
            logger.warning(warning)
            warnings.append(warning)

        return warnings
