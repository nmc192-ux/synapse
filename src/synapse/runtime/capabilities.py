from __future__ import annotations

from datetime import datetime, timezone

from synapse.models.agent import AgentDefinition, AgentDiscoveryEntry, AgentKind
from synapse.models.capability import CapabilityAdvertisementRequest, CapabilityRecord
from synapse.models.runtime_state import AgentRuntimeStatus
from synapse.runtime.registry import AgentRegistry


class CapabilityRegistry:
    def __init__(self, agents: AgentRegistry) -> None:
        self.agents = agents

    async def advertise(self, request: CapabilityAdvertisementRequest) -> CapabilityRecord:
        try:
            definition = self.agents.get(request.agent_id).model_copy(deep=True)
        except KeyError:
            definition = AgentDefinition(
                agent_id=request.agent_id,
                kind=AgentKind.CUSTOM,
                name=request.agent_id,
            )

        definition.description = request.description or definition.description
        definition.endpoint = request.endpoint or definition.endpoint
        definition.capability_tags = list(dict.fromkeys(request.capabilities))
        definition.reputation = request.reputation
        definition.latency = request.latency
        metadata = dict(definition.metadata)
        metadata.update(request.metadata)
        if request.description is not None:
            metadata["capability_description"] = request.description
        definition.metadata = {str(key): str(value) for key, value in metadata.items()}

        agent = self.agents.register(definition)
        await self.agents.update_agent_status(
            agent.agent_id,
            AgentRuntimeStatus.ACTIVE if request.availability else AgentRuntimeStatus.OFFLINE,
        )
        await self.agents.save_to_store(agent)
        return self._to_record(agent)

    async def list_capabilities(self) -> list[CapabilityRecord]:
        rows = await self.agents.list_persisted_agents()
        records: list[CapabilityRecord] = []
        for row in rows:
            agent_payload = row.get("agent")
            if not isinstance(agent_payload, dict):
                continue
            records.append(self._to_record(AgentDefinition.model_validate(agent_payload)))
        return sorted(records, key=lambda record: (-record.reputation, record.latency, not record.availability, record.agent_id))

    async def find(self, capability: str) -> list[AgentDiscoveryEntry]:
        available_agent_ids = {
            record.agent_id
            for record in await self.list_capabilities()
            if record.availability
        }
        return self.agents.find(capability, available_agent_ids=available_agent_ids)

    def _to_record(self, agent: AgentDefinition) -> CapabilityRecord:
        status = self.agents.get_agent_status(agent.agent_id)
        description = agent.description or agent.metadata.get("capability_description")
        return CapabilityRecord(
            agent_id=agent.agent_id,
            capabilities=agent.capability_tags,
            description=str(description) if description is not None else None,
            endpoint=agent.endpoint,
            latency=agent.latency,
            availability=bool(status["availability"]),
            reputation=agent.reputation,
            updated_at=self._normalize_timestamp(status["last_seen_at"]),
            metadata=dict(agent.metadata),
        )

    @staticmethod
    def _normalize_timestamp(value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return datetime.now(timezone.utc)
