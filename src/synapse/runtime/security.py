from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from synapse.models.agent import AgentDefinition, AgentSecurityPolicy
from synapse.runtime.state_store import RuntimeStateStore

if TYPE_CHECKING:
    from synapse.runtime.registry import AgentRegistry


class SandboxPermissionError(PermissionError):
    pass


class SandboxApprovalRequiredError(SandboxPermissionError):
    def __init__(self, reason: str, *, action: str, metadata: dict[str, object] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.action = action
        self.metadata = metadata or {}


class SandboxRateLimitError(RuntimeError):
    pass


class AgentSecuritySandbox:
    def __init__(
        self,
        agents: AgentRegistry,
        state_store: RuntimeStateStore | None = None,
        default_policy: AgentSecurityPolicy | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.agents = agents
        self.state_store = state_store
        self.default_policy = default_policy or AgentSecurityPolicy()
        self._clock = clock or monotonic
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._cross_domain_jumps: dict[str, int] = defaultdict(int)
        self._run_policy_overrides: dict[str, AgentSecurityPolicy | dict[str, object]] = {}

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    def set_run_policy(
        self,
        run_id: str,
        policy: AgentSecurityPolicy | dict[str, object] | None,
    ) -> None:
        if policy is None:
            self._run_policy_overrides.pop(run_id, None)
            return
        self._run_policy_overrides[run_id] = policy

    def authorize_navigation(
        self,
        agent_id: str | None,
        url: str,
        *,
        run_id: str | None = None,
        current_url: str | None = None,
    ) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id, run_id=run_id)
        hostname = self._require_hostname(url, action="navigation")
        self._authorize_domain(hostname, agent, policy, url=url)
        if self._is_cross_domain_jump(current_url, url):
            jumps = self._cross_domain_jumps[self._policy_subject(agent.agent_id, run_id)] + 1
            if jumps > policy.max_cross_domain_jumps:
                raise SandboxPermissionError(
                    f"Cross-domain navigation limit exceeded for agent '{agent.agent_id}'."
                )
            if policy.dangerous_action_requires_approval:
                raise SandboxApprovalRequiredError(
                    "Cross-domain navigation requires explicit approval.",
                    action="navigation",
                    metadata={
                        "url": url,
                        "current_url": current_url,
                        "hostname": hostname,
                        "max_cross_domain_jumps": policy.max_cross_domain_jumps,
                    },
                )

    def authorize_domain(self, agent_id: str | None, url: str) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id)
        hostname = self._require_hostname(url, action="navigation")
        self._authorize_domain(hostname, agent, policy, url=url)

    def authorize_tool(self, agent_id: str | None, tool_name: str, *, run_id: str | None = None) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id, run_id=run_id)
        if not policy.block_unsafe_actions:
            return
        if tool_name in policy.blocked_tools:
            raise SandboxPermissionError(
                f"Tool '{tool_name}' is blocked for agent '{agent.agent_id}'."
            )
        if not policy.allowed_tools:
            raise SandboxPermissionError(f"Agent '{agent.agent_id}' has no allowed tools configured.")
        if tool_name not in policy.allowed_tools:
            raise SandboxPermissionError(
                f"Tool '{tool_name}' is not allowed for agent '{agent.agent_id}'."
            )

    def authorize_upload(self, agent_id: str | None, *, run_id: str | None = None) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id, run_id=run_id)
        if not policy.uploads_allowed:
            raise SandboxPermissionError(f"File uploads are blocked for agent '{agent.agent_id}'.")
        if policy.dangerous_action_requires_approval:
            raise SandboxApprovalRequiredError(
                "File upload requires explicit approval.",
                action="upload",
            )

    def authorize_download(self, agent_id: str | None, *, run_id: str | None = None) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id, run_id=run_id)
        if not policy.downloads_allowed:
            raise SandboxPermissionError(f"File downloads are blocked for agent '{agent.agent_id}'.")
        if policy.dangerous_action_requires_approval:
            raise SandboxApprovalRequiredError(
                "File download requires explicit approval.",
                action="download",
            )

    def authorize_screenshot(self, agent_id: str | None, *, run_id: str | None = None) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id, run_id=run_id)
        if not policy.screenshot_allowed:
            raise SandboxPermissionError(f"Screenshots are blocked for agent '{agent.agent_id}'.")

    def authorize_external_request(
        self,
        agent_id: str | None,
        url: str,
        *,
        run_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id, run_id=run_id)
        hostname = self._require_hostname(url, action="external request")
        self._authorize_domain(hostname, agent, policy, url=url)
        if policy.dangerous_action_requires_approval:
            raise SandboxApprovalRequiredError(
                "External API access requires explicit approval.",
                action="external_request",
                metadata={"url": url, "tool_name": tool_name, "hostname": hostname},
            )

    def authorize_delegation(
        self,
        agent_id: str | None,
        recipient_agent_id: str | None,
        *,
        run_id: str | None = None,
    ) -> None:
        agent = self._require_agent(agent_id)
        policy = self.resolve_policy(agent.agent_id, run_id=run_id)
        if policy.dangerous_action_requires_approval:
            raise SandboxApprovalRequiredError(
                "Agent delegation requires explicit approval.",
                action="delegate",
                metadata={"recipient_agent_id": recipient_agent_id},
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

    def resolve_policy(self, agent_id: str, *, run_id: str | None = None) -> AgentSecurityPolicy:
        agent = self.agents.get(agent_id)
        policy = self.default_policy.merged(agent.security)
        run_override = self._run_policy_override(run_id)
        return policy.merged(run_override)

    def record_navigation(
        self,
        agent_id: str | None,
        *,
        run_id: str | None = None,
        previous_url: str | None = None,
        current_url: str | None = None,
    ) -> None:
        if not agent_id or not self._is_cross_domain_jump(previous_url, current_url):
            return
        self._cross_domain_jumps[self._policy_subject(agent_id, run_id)] += 1

    def _run_policy_override(self, run_id: str | None) -> AgentSecurityPolicy | dict[str, object] | None:
        if run_id is None:
            return None
        return self._run_policy_overrides.get(run_id)

    def _authorize_domain(
        self,
        hostname: str,
        agent: AgentDefinition,
        policy: AgentSecurityPolicy,
        *,
        url: str,
    ) -> None:
        if not policy.block_unsafe_actions:
            return
        if any(self._domain_matches(hostname, blocked) for blocked in policy.blocked_domains):
            raise SandboxPermissionError(
                f"Domain '{hostname}' is blocked for agent '{agent.agent_id}'."
            )
        if not policy.allowed_domains:
            raise SandboxPermissionError(f"Agent '{agent.agent_id}' has no allowed domains configured.")
        if not any(self._domain_matches(hostname, allowed) for allowed in policy.allowed_domains):
            raise SandboxPermissionError(
                f"Domain '{hostname}' is not allowed for agent '{agent.agent_id}'."
            )

    @staticmethod
    def _require_hostname(url: str, *, action: str) -> str:
        hostname = (urlparse(url).hostname or "").lower()
        if not hostname:
            raise SandboxPermissionError(f"{action.capitalize()} target is missing a valid hostname.")
        return hostname

    @staticmethod
    def _is_cross_domain_jump(previous_url: str | None, current_url: str | None) -> bool:
        if not previous_url or not current_url:
            return False
        previous_host = (urlparse(previous_url).hostname or "").lower()
        current_host = (urlparse(current_url).hostname or "").lower()
        return bool(previous_host and current_host and previous_host != current_host)

    @staticmethod
    def _policy_subject(agent_id: str, run_id: str | None) -> str:
        return run_id or agent_id

    @staticmethod
    def _domain_matches(hostname: str, allowed_domain: str) -> bool:
        normalized = allowed_domain.lower().strip()
        return hostname == normalized or hostname.endswith(f".{normalized}")
