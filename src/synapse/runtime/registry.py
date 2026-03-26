from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from synapse.models.agent import AgentDefinition, AgentDiscoveryEntry, AgentKind
from synapse.models.a2a import AgentIdentityRecord
from synapse.models.runtime_state import AgentRuntimeRecord, AgentRuntimeStatus
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.llm import LLMProvider
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.state_store import RuntimeStateStore
from synapse.security.identity import AgentIdentityManager
from synapse.transports.websocket_manager import WebSocketManager

if TYPE_CHECKING:
    from synapse.adapters.base import AgentAdapter
    from synapse.runtime.browser import BrowserRuntime
    from synapse.runtime.budget_service import BudgetService
    from synapse.runtime.memory_service import MemoryService


class AgentRegistry:
    def __init__(self, state_store: RuntimeStateStore | None = None) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._identities: dict[str, AgentIdentityRecord] = {}
        self._status: dict[str, AgentRuntimeStatus] = {}
        self._last_seen_at: dict[str, datetime] = {}
        self._state_store = state_store
        self._identity_manager = AgentIdentityManager("synapse-agent-identity")

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store

    def register(self, definition: AgentDefinition) -> AgentDefinition:
        self._definitions[definition.agent_id] = definition
        self._status.setdefault(definition.agent_id, AgentRuntimeStatus.IDLE)
        self._last_seen_at[definition.agent_id] = datetime.now(timezone.utc)
        self._identities.setdefault(
            definition.agent_id,
            self._identity_manager.issue_identity(
                agent_id=definition.agent_id,
                verification_key=f"{definition.agent_id}-verification-key",
                key_id="default",
                reputation=definition.reputation,
                capabilities=definition.capability_tags,
                issued_at=datetime.now(timezone.utc),
            ),
        )
        return definition

    async def load_from_store(self) -> None:
        if self._state_store is None:
            return
        records = await self._state_store.list_agents()
        for record in records:
            agent_payload = record.get("agent")
            if not isinstance(agent_payload, dict):
                continue
            definition = AgentDefinition.model_validate(agent_payload)
            self._definitions[definition.agent_id] = definition
            identity_payload = record.get("identity")
            if isinstance(identity_payload, dict):
                self._identities[definition.agent_id] = AgentIdentityRecord.model_validate(identity_payload)
            status_value = record.get("status", AgentRuntimeStatus.IDLE.value)
            self._status[definition.agent_id] = AgentRuntimeStatus(status_value)
            last_seen = record.get("last_seen_at")
            if isinstance(last_seen, str):
                self._last_seen_at[definition.agent_id] = datetime.fromisoformat(last_seen)
            else:
                self._last_seen_at[definition.agent_id] = datetime.now(timezone.utc)

    async def save_to_store(self, agent: AgentDefinition) -> None:
        if self._state_store is None:
            return
        status = self._status.get(agent.agent_id, AgentRuntimeStatus.IDLE)
        last_seen = self._last_seen_at.get(agent.agent_id, datetime.now(timezone.utc))
        record = AgentRuntimeRecord(
            agent_id=agent.agent_id,
            kind=agent.kind.value,
            name=agent.name,
            capabilities=agent.capability_tags,
            reputation=agent.reputation,
            limits=(agent.limits.model_dump(mode="json") if agent.limits is not None else {}),
            security_policy=agent.security.model_dump(mode="json"),
            availability=status != AgentRuntimeStatus.OFFLINE,
            status=status,
            last_seen_at=last_seen,
            endpoint=agent.endpoint,
            metadata=agent.metadata,
        )
        await self._state_store.register_agent(
            {
                "agent_id": agent.agent_id,
                "agent": agent.model_dump(mode="json"),
                "status": status.value,
                "last_seen_at": last_seen.isoformat(),
                "runtime": record.model_dump(mode="json"),
                "identity": self._identities[agent.agent_id].model_dump(mode="json"),
            }
        )

    async def update_agent_status(self, agent_id: str, status: AgentRuntimeStatus) -> None:
        agent = self.get(agent_id)
        self._status[agent_id] = status
        await self.update_agent_last_seen(agent_id, datetime.now(timezone.utc))
        await self.save_to_store(agent)

    async def update_agent_last_seen(self, agent_id: str, timestamp: datetime) -> None:
        self.get(agent_id)
        self._last_seen_at[agent_id] = timestamp

    def get_agent_status(self, agent_id: str) -> dict[str, object]:
        agent = self.get(agent_id)
        status = self._status.get(agent_id, AgentRuntimeStatus.IDLE)
        last_seen = self._last_seen_at.get(agent_id, datetime.now(timezone.utc))
        return {
            "agent_id": agent_id,
            "status": status.value,
            "availability": status != AgentRuntimeStatus.OFFLINE,
            "last_seen_at": last_seen,
        }

    async def get_persisted_agent(self, agent_id: str) -> dict[str, object] | None:
        if self._state_store is None:
            agent = self._definitions.get(agent_id)
            if agent is None:
                return None
            status_payload = self.get_agent_status(agent_id)
            return {
                "agent_id": agent.agent_id,
                "agent": agent.model_dump(mode="json"),
                "status": status_payload["status"],
                "last_seen_at": status_payload["last_seen_at"].isoformat(),
            }
        return await self._state_store.get_agent(agent_id)

    async def list_persisted_agents(self) -> list[dict[str, object]]:
        if self._state_store is None:
            rows: list[dict[str, object]] = []
            for agent in self._definitions.values():
                status_payload = self.get_agent_status(agent.agent_id)
                rows.append(
                    {
                        "agent_id": agent.agent_id,
                        "agent": agent.model_dump(mode="json"),
                        "status": status_payload["status"],
                        "last_seen_at": status_payload["last_seen_at"].isoformat(),
                    }
                )
            return rows
        return await self._state_store.list_agents()

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._definitions[agent_id]
        except KeyError as exc:
            raise KeyError(f"Agent not found: {agent_id}") from exc

    def list(self) -> list[AgentDefinition]:
        return list(self._definitions.values())

    def set_identity(self, identity: AgentIdentityRecord) -> AgentIdentityRecord:
        self._identity_manager.verify_identity(identity)
        self._identities[identity.agent_id] = identity
        return identity

    def get_identity(self, agent_id: str) -> AgentIdentityRecord:
        try:
            return self._identities[agent_id]
        except KeyError as exc:
            raise KeyError(f"Agent identity not found: {agent_id}") from exc

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
                - definition.latency
                + (1 if availability else 0)
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

        return sorted(entries, key=lambda entry: (-entry.reputation, entry.latency, not entry.availability, entry.id))

    def build_adapter(
        self,
        agent_id: str,
        browser: "BrowserRuntime",
        sockets: WebSocketManager,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        memory_service: MemoryService,
        budget_service: BudgetService,
        llm: LLMProvider | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> AgentAdapter:
        from synapse.adapters.a2a import A2AAdapter
        from synapse.adapters.claude_code import ClaudeCodeAdapter
        from synapse.adapters.codex import CodexAdapter
        from synapse.adapters.custom import CustomAgentAdapter
        from synapse.adapters.openclaw import OpenClawAdapter

        definition = self.get(agent_id)
        adapter_map: dict[AgentKind, type["AgentAdapter"]] = {
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
            memory_service=memory_service,
            budget_service=budget_service,
            llm=llm,
            compression_provider=compression_provider,
        )
