from __future__ import annotations

from synapse.adapters.a2a import A2AAdapter
from synapse.adapters.base import AgentAdapter
from synapse.adapters.claude_code import ClaudeCodeAdapter
from synapse.adapters.codex import CodexAdapter
from synapse.adapters.custom import CustomAgentAdapter
from synapse.adapters.openclaw import OpenClawAdapter
from synapse.models.agent import AgentDefinition, AgentDiscoveryEntry, AgentKind
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer
from synapse.transports.websocket_manager import WebSocketManager


class AgentRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition) -> AgentDefinition:
        self._definitions[definition.agent_id] = definition
        return definition

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._definitions[agent_id]
        except KeyError as exc:
            raise KeyError(f"Agent not found: {agent_id}") from exc

    def list(self) -> list[AgentDefinition]:
        return list(self._definitions.values())

    def find(
        self,
        capability: str,
        available_agent_ids: set[str] | None = None,
    ) -> list[AgentDiscoveryEntry]:
        normalized_capability = capability.lower()
        entries: list[AgentDiscoveryEntry] = []
        available_agent_ids = available_agent_ids or set()

        for definition in self._definitions.values():
            tags = [tag.lower() for tag in definition.capability_tags]
            if normalized_capability not in tags:
                continue

            availability = definition.agent_id in available_agent_ids
            score = (
                definition.reputation * 100
                + (25 if availability else 0)
                - definition.latency
            )
            entries.append(
                AgentDiscoveryEntry(
                    id=definition.agent_id,
                    capabilities=definition.capability_tags,
                    endpoint=definition.endpoint,
                    reputation=definition.reputation,
                    latency=definition.latency,
                    availability=availability,
                    score=score,
                )
            )

        return sorted(entries, key=lambda entry: (-entry.score, -entry.reputation, entry.latency, entry.id))

    def build_adapter(
        self,
        agent_id: str,
        browser: BrowserRuntime,
        sockets: WebSocketManager,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        memory_manager: AgentMemoryManager,
        budget_manager: AgentBudgetManager,
    ) -> AgentAdapter:
        definition = self.get(agent_id)
        adapter_map: dict[AgentKind, type[AgentAdapter]] = {
            AgentKind.OPENCLAW: OpenClawAdapter,
            AgentKind.CLAUDE_CODE: ClaudeCodeAdapter,
            AgentKind.CODEX: CodexAdapter,
            AgentKind.A2A: A2AAdapter,
            AgentKind.CUSTOM: CustomAgentAdapter,
        }
        adapter_cls = adapter_map[definition.kind]
        return adapter_cls(
            definition,
            browser=browser,
            sockets=sockets,
            sandbox=sandbox,
            safety=safety,
            memory_manager=memory_manager,
            budget_manager=budget_manager,
        )
