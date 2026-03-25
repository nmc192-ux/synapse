from __future__ import annotations

from ipaddress import ip_address
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from synapse.models.browser import StructuredPageModel
from synapse.models.loop import AgentAction
from synapse.models.task import TaskRequest


INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore (all )?(previous|prior) instructions",
        r"ignore (the )?(system|developer) (prompt|instructions|message)",
        r"override (the )?(system|developer) (prompt|instructions|message)",
        r"new system prompt",
        r"reveal (your )?(system prompt|developer prompt|hidden prompt)",
        r"bypass (your )?(safety|guardrails|policy)",
        r"do not obey previous instructions",
        r"you are now .* without restrictions",
    )
]

UNSAFE_INSTRUCTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"exfiltrat(e|ion)",
        r"steal (credentials|tokens|cookies|secrets)",
        r"dump (environment|env|secrets|tokens)",
        r"disable (security|sandbox|guardrails)",
        r"execute arbitrary code",
    )
]


class SecurityFinding(BaseModel):
    category: str
    reason: str
    source: str
    excerpt: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class SecurityAlertError(RuntimeError):
    def __init__(self, finding: SecurityFinding) -> None:
        super().__init__(finding.reason)
        self.finding = finding


class AgentSafetyLayer:
    def inspect_page(self, page: StructuredPageModel, action: str) -> SecurityFinding | None:
        for snippet in self._page_snippets(page):
            if self._matches(snippet, INJECTION_PATTERNS):
                return SecurityFinding(
                    category="prompt_injection",
                    reason="Page content attempted to override system instructions.",
                    source=action,
                    excerpt=snippet[:240],
                    metadata={"url": page.url, "title": page.title},
                )
        return None

    def validate_task(self, task: TaskRequest) -> SecurityFinding | None:
        candidates = [task.goal]
        candidates.extend(self._flatten_strings(task.constraints))
        for action in task.actions:
            candidates.extend(self._action_strings(action))

        for candidate in candidates:
            if self._matches(candidate, INJECTION_PATTERNS):
                return SecurityFinding(
                    category="unsafe_instruction",
                    reason="Task instructions contain prompt-injection language.",
                    source="task.request",
                    excerpt=candidate[:240],
                    metadata={"task_id": task.task_id, "agent_id": task.agent_id},
                )
            if self._matches(candidate, UNSAFE_INSTRUCTION_PATTERNS):
                return SecurityFinding(
                    category="unsafe_instruction",
                    reason="Task instructions request unsafe behavior.",
                    source="task.request",
                    excerpt=candidate[:240],
                    metadata={"task_id": task.task_id, "agent_id": task.agent_id},
                )
        return None

    def validate_tool_call(self, tool_name: str, arguments: dict[str, object]) -> SecurityFinding | None:
        for value in self._flatten_strings(arguments):
            if self._matches(value, INJECTION_PATTERNS):
                return SecurityFinding(
                    category="tool_validation",
                    reason="Tool arguments contain prompt-injection language.",
                    source=tool_name,
                    excerpt=value[:240],
                )
            if self._matches(value, UNSAFE_INSTRUCTION_PATTERNS):
                return SecurityFinding(
                    category="tool_validation",
                    reason="Tool arguments request unsafe behavior.",
                    source=tool_name,
                    excerpt=value[:240],
                )

        invalid_payload = self._find_invalid_payload(arguments, tool_name)
        if invalid_payload is not None:
            return invalid_payload
        return None

    def _find_invalid_payload(
        self,
        arguments: dict[str, object],
        tool_name: str,
    ) -> SecurityFinding | None:
        for key, value in arguments.items():
            if not self._is_supported_value(value):
                return SecurityFinding(
                    category="tool_validation",
                    reason="Tool arguments must be JSON-serializable primitives, lists, or objects.",
                    source=tool_name,
                    excerpt=str(key),
                )

            if isinstance(value, str) and any(token in key.lower() for token in ("url", "endpoint", "uri")):
                url_issue = self._validate_outbound_url(value)
                if url_issue is not None:
                    return SecurityFinding(
                        category="tool_validation",
                        reason=url_issue,
                        source=tool_name,
                        excerpt=value[:240],
                    )

        return None

    def _validate_outbound_url(self, value: str) -> str | None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            return "Tool calls may only target http or https endpoints."
        hostname = parsed.hostname
        if not hostname:
            return "Tool call URL is missing a valid hostname."
        lowered = hostname.lower()
        if lowered in {"localhost", "metadata.google.internal"}:
            return "Tool calls to local or metadata endpoints are blocked."
        try:
            addr = ip_address(lowered)
        except ValueError:
            return None
        if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
            return "Tool calls to private network targets are blocked."
        return None

    @staticmethod
    def _matches(value: str, patterns: list[re.Pattern[str]]) -> bool:
        return any(pattern.search(value) for pattern in patterns)

    @staticmethod
    def _action_strings(action: AgentAction) -> list[str]:
        values = [action.selector, action.text, action.url, action.attribute]
        return [value for value in values if isinstance(value, str)]

    @staticmethod
    def _flatten_strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            results: list[str] = []
            for key, item in value.items():
                results.extend(AgentSafetyLayer._flatten_strings(key))
                results.extend(AgentSafetyLayer._flatten_strings(item))
            return results
        if isinstance(value, list):
            results: list[str] = []
            for item in value:
                results.extend(AgentSafetyLayer._flatten_strings(item))
            return results
        return []

    @staticmethod
    def _is_supported_value(value: object) -> bool:
        if value is None or isinstance(value, (str, int, float, bool)):
            return True
        if isinstance(value, list):
            return all(AgentSafetyLayer._is_supported_value(item) for item in value)
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and AgentSafetyLayer._is_supported_value(item)
                for key, item in value.items()
            )
        return False

    @staticmethod
    def _page_snippets(page: StructuredPageModel) -> list[str]:
        snippets = [page.title]
        snippets.extend(
            filter(
                None,
                (
                    section.heading or section.text
                    for section in page.sections
                ),
            )
        )
        snippets.extend(button.text for button in page.buttons if button.text)
        snippets.extend(link.text for link in page.links if link.text)
        snippets.extend(link.href for link in page.links if link.href)
        snippets.extend(input_.placeholder for input_ in page.inputs if input_.placeholder)
        return snippets
