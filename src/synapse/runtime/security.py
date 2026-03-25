from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from time import monotonic
from urllib.parse import urlparse

from synapse.models.agent import AgentDefinition
from synapse.runtime.registry import AgentRegistry


class SandboxPermissionError(PermissionError):
    pass


class SandboxRateLimitError(RuntimeError):
    pass


class AgentSecuritySandbox:
    def __init__(
        self,
        agents: AgentRegistry,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.agents = agents
        self._clock = clock or monotonic
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def authorize_domain(self, agent_id: str | None, url: str) -> None:
        agent = self._require_agent(agent_id)
        policy = agent.security
        hostname = (urlparse(url).hostname or "").lower()
        if not hostname:
            raise SandboxPermissionError("Navigation target is missing a valid hostname.")
        if not policy.block_unsafe_actions:
            return
        if not policy.allowed_domains:
            raise SandboxPermissionError(f"Agent '{agent.agent_id}' has no allowed domains configured.")
        if not any(self._domain_matches(hostname, allowed) for allowed in policy.allowed_domains):
            raise SandboxPermissionError(
                f"Domain '{hostname}' is not allowed for agent '{agent.agent_id}'."
            )

    def authorize_tool(self, agent_id: str | None, tool_name: str) -> None:
        agent = self._require_agent(agent_id)
        policy = agent.security
        if not policy.block_unsafe_actions:
            return
        if not policy.allowed_tools:
            raise SandboxPermissionError(f"Agent '{agent.agent_id}' has no allowed tools configured.")
        if tool_name not in policy.allowed_tools:
            raise SandboxPermissionError(
                f"Tool '{tool_name}' is not allowed for agent '{agent.agent_id}'."
            )

    def consume_browser_action(self, agent_id: str | None) -> None:
        agent = self._require_agent(agent_id)
        self._consume(agent.agent_id, "browser", agent.security.rate_limits.browser_actions_per_minute)

    def consume_tool_call(self, agent_id: str | None) -> None:
        agent = self._require_agent(agent_id)
        self._consume(agent.agent_id, "tool", agent.security.rate_limits.tool_calls_per_minute)

    def _consume(self, agent_id: str, bucket: str, limit: int) -> None:
        if limit <= 0:
            raise SandboxRateLimitError(
                f"Rate limit for '{bucket}' actions is disabled for agent '{agent_id}'."
            )
        now = self._clock()
        key = (agent_id, bucket)
        events = self._events[key]
        while events and now - events[0] >= 60:
            events.popleft()
        if len(events) >= limit:
            raise SandboxRateLimitError(
                f"Agent '{agent_id}' exceeded the {bucket} rate limit of {limit} actions per minute."
            )
        events.append(now)

    def _require_agent(self, agent_id: str | None) -> AgentDefinition:
        if not agent_id:
            raise SandboxPermissionError("agent_id is required for sandboxed agent actions.")
        return self.agents.get(agent_id)

    @staticmethod
    def _domain_matches(hostname: str, allowed_domain: str) -> bool:
        normalized = allowed_domain.lower().strip()
        return hostname == normalized or hostname.endswith(f".{normalized}")
