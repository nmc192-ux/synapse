import asyncio
from datetime import datetime, timezone

from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_state import AgentRuntimeStatus
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore


def test_registry_persistence_and_status_updates() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        agent = registry.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent One",
                capability_tags=["web_scraping"],
            )
        )
        await registry.save_to_store(agent)

        restored = AgentRegistry(state_store=store)
        await restored.load_from_store()
        loaded = restored.get("agent-1")
        assert loaded.name == "Agent One"

        await restored.update_agent_status("agent-1", AgentRuntimeStatus.ACTIVE)
        await restored.update_agent_last_seen("agent-1", datetime.now(timezone.utc))
        status = restored.get_agent_status("agent-1")
        assert status["status"] == AgentRuntimeStatus.ACTIVE.value
        assert status["availability"] is True

    asyncio.run(scenario())
