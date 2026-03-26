import asyncio
from datetime import datetime, timedelta, timezone

from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentRegistrationRequest, AgentWireMessage
from synapse.models.runtime_event import EventType
from synapse.runtime.compression.base import CompressionProvider
from synapse.models.runtime_state import AgentRuntimeStatus
from synapse.runtime.a2a import A2AHub
from synapse.runtime.registry import AgentRegistry
from synapse.security.signing import MessageSigner
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


def test_a2a_valid_signature_invalid_signature_replay_and_expiry() -> None:
    signer = MessageSigner()

    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        sockets = WebSocketManager(state_store=store)
        hub = A2AHub(registry, state_store=store, sockets=sockets)
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-a",
                name="Agent A",
                capabilities=["research"],
                verification_key="agent-a-secret",
            )
        )

        valid = signer.sign_wire_message(
            AgentWireMessage(
                type=A2AMessageType.SEND_MESSAGE,
                agent="agent-a",
                target_agent="agent-b",
                payload={"hello": "world"},
                nonce="nonce-1",
            ),
            signing_key="agent-a-secret",
            key_id="default",
            nonce="nonce-1",
        )
        envelope = hub.from_wire_message(valid)
        assert envelope.sender_agent_id == "agent-a"

        invalid = signer.sign_wire_message(
            AgentWireMessage(
                type=A2AMessageType.SEND_MESSAGE,
                agent="agent-a",
                target_agent="agent-b",
                payload={"hello": "tampered"},
                nonce="nonce-invalid",
            ),
            signing_key="agent-a-secret",
            key_id="default",
            nonce="nonce-invalid",
        ).model_copy(update={"signature": "bad-signature"})
        try:
            hub.from_wire_message(invalid)
        except ValueError as exc:
            assert "signature" in str(exc).lower()
        else:
            raise AssertionError("Expected invalid signature to be rejected.")

        replayed = signer.sign_wire_message(
            AgentWireMessage(
                type=A2AMessageType.SEND_MESSAGE,
                agent="agent-a",
                target_agent="agent-b",
                payload={"hello": "again"},
                nonce="nonce-replay",
            ),
            signing_key="agent-a-secret",
            key_id="default",
            nonce="nonce-replay",
        )
        hub.from_wire_message(replayed)
        try:
            hub.from_wire_message(replayed)
        except ValueError as exc:
            assert "nonce" in str(exc).lower()
        else:
            raise AssertionError("Expected replayed nonce to be rejected.")

        expired = signer.sign_wire_message(
            AgentWireMessage(
                type=A2AMessageType.SEND_MESSAGE,
                agent="agent-a",
                target_agent="agent-b",
                payload={"stale": True},
                nonce="nonce-expired",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
            ),
            signing_key="agent-a-secret",
            key_id="default",
            nonce="nonce-expired",
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        try:
            hub.from_wire_message(expired)
        except ValueError as exc:
            assert "expired" in str(exc).lower()
        else:
            raise AssertionError("Expected expired message to be rejected.")

    asyncio.run(scenario())


def test_a2a_emits_compact_message_summary() -> None:
    class _StubCompressionProvider(CompressionProvider):
        async def compress_text(self, text: str, context: dict | None = None) -> str:
            return text[:32]

        async def compress_json(self, data: dict, context: dict | None = None) -> dict:
            return {"keys": sorted(data.keys()), "count": len(data)}

        async def summarize_events(self, events: list[dict], context: dict | None = None) -> dict:
            return {"count": len(events)}

        async def summarize_memory(self, memories: list[dict], context: dict | None = None) -> dict:
            return {"count": len(memories)}

    class _FakeWebSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, payload: dict[str, object]) -> None:
            self.messages.append(payload)

    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        sockets = WebSocketManager(state_store=store, compression_provider=_StubCompressionProvider())
        hub = A2AHub(
            registry,
            state_store=store,
            sockets=sockets,
            compression_provider=_StubCompressionProvider(),
        )
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-a",
                name="Agent A",
                capabilities=["delegation"],
                verification_key="agent-a-secret",
            )
        )

        await hub.register_connection("agent-b", {"transport": "websocket"})
        hub._connections["agent-b"] = _FakeWebSocket()

        async with sockets.subscribe("subscriber") as queue:
            await hub.send(
                A2AEnvelope(
                    type=A2AMessageType.REQUEST_TASK,
                    sender_agent_id="agent-a",
                    recipient_agent_id="agent-b",
                    payload={"task": {"task_id": "task-1", "goal": "Collect results", "agent_id": "agent-b"}},
                )
            )
            seen_types: list[EventType] = []
            for _ in range(3):
                seen_types.append((await queue.get()).event_type)
            assert EventType.A2A_MESSAGE_COMPRESSED in seen_types

    asyncio.run(scenario())
