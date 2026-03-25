import asyncio
from datetime import datetime, timedelta, timezone

from synapse.models.runtime_state import AgentRuntimeStatus
from synapse.runtime.a2a import A2AHub
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager


def test_a2a_heartbeat_and_stale_cleanup() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        sockets = WebSocketManager(state_store=store)
        hub = A2AHub(registry, state_store=store, sockets=sockets)

        connection = await hub.register_connection("agent-a", {"transport": "websocket"})
        assert connection.status == AgentRuntimeStatus.ACTIVE

        stale_connection = hub._connection_state["agent-a"]
        stale_connection.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=120)
        stale = await hub.cleanup_stale_connections(ttl_seconds=60)
        assert stale == ["agent-a"]

    asyncio.run(scenario())
