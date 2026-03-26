import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentRegistrationRequest, AgentWireMessage
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_event import EventType
from synapse.runtime.compression.base import CompressionProvider
from synapse.models.runtime_state import AgentRuntimeStatus
from synapse.runtime.a2a import A2AHub
from synapse.runtime.event_bus import EventBus
from synapse.runtime.registry import AgentRegistry
from synapse.config import Settings
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope
from synapse.security.signing import MessageSigner
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager


def test_a2a_heartbeat_and_stale_cleanup() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        sockets = WebSocketManager(state_store=store)
        hub = A2AHub(registry, state_store=store, sockets=sockets)
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-a",
                name="Agent A",
                organization_id="org-1",
                project_id="project-1",
            )
        )

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
                organization_id="org-1",
                project_id="project-1",
                capabilities=["research"],
                verification_key="agent-a-secret",
            )
        )
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-b",
                name="Agent B",
                organization_id="org-1",
                project_id="project-1",
                capabilities=["analysis"],
                verification_key="agent-b-secret",
            )
        )

        valid = signer.sign_wire_message(
            AgentWireMessage(
                type=A2AMessageType.SEND_MESSAGE,
                agent="agent-a",
                target_agent="agent-b",
                sender_id="agent-a",
                recipient_id="agent-b",
                organization_id="org-1",
                project_id="project-1",
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
                sender_id="agent-a",
                recipient_id="agent-b",
                organization_id="org-1",
                project_id="project-1",
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
                sender_id="agent-a",
                recipient_id="agent-b",
                organization_id="org-1",
                project_id="project-1",
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
                sender_id="agent-a",
                recipient_id="agent-b",
                organization_id="org-1",
                project_id="project-1",
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


def test_a2a_rejects_cross_project_signed_messages() -> None:
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
                organization_id="org-1",
                project_id="project-1",
                capabilities=["research"],
                verification_key="agent-a-secret",
            )
        )
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-b",
                name="Agent B",
                organization_id="org-1",
                project_id="project-2",
                capabilities=["analysis"],
                verification_key="agent-b-secret",
            )
        )

        message = signer.sign_wire_message(
            AgentWireMessage(
                type=A2AMessageType.SEND_MESSAGE,
                agent="agent-a",
                sender_id="agent-a",
                target_agent="agent-b",
                recipient_id="agent-b",
                organization_id="org-1",
                project_id="project-1",
                payload={"hello": "world"},
                nonce="nonce-cross-project",
            ),
            signing_key="agent-a-secret",
            key_id="default",
            nonce="nonce-cross-project",
        )

        with pytest.raises(ValueError, match="Cross-project A2A routing"):
            hub.from_wire_message(message)

    asyncio.run(scenario())


def test_a2a_connect_does_not_auto_register_unknown_agents() -> None:
    class _FakeWebSocket:
        async def accept(self) -> None:
            return None

    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        sockets = WebSocketManager(state_store=store)
        hub = A2AHub(registry, state_store=store, sockets=sockets)

        with pytest.raises(KeyError):
            await hub.connect("unknown-agent", _FakeWebSocket())

    asyncio.run(scenario())


class _StubA2AOrchestrator:
    def __init__(self) -> None:
        registry = AgentRegistry()
        self.a2a = A2AHub(registry, state_store=InMemoryRuntimeStateStore(), sockets=WebSocketManager())
        self.event_bus = None
        self.sockets = WebSocketManager()
        self._agents = {
            "agent-1": AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.A2A,
                name="Agent 1",
                organization_id="org-1",
                project_id="project-1",
            ),
            "agent-2": AgentDefinition(
                agent_id="agent-2",
                kind=AgentKind.A2A,
                name="Agent 2",
                organization_id="org-1",
                project_id="project-2",
            ),
        }
        for agent in self._agents.values():
            registry.register(agent)

    async def get_persisted_agent(self, agent_id: str):
        if agent_id not in self._agents:
            raise KeyError(agent_id)
        return self._agents[agent_id]


def _build_a2a_client(*, service_allowlist: dict[str, list[str]] | None = None) -> tuple[TestClient, Authenticator]:
    settings = Settings(
        auth_required=True,
        jwt_secret="a2a-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
        a2a_service_agent_allowlist=service_allowlist or {},
    )
    authenticator = Authenticator(settings)
    orchestrator = _StubA2AOrchestrator()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    return TestClient(app), authenticator


def test_a2a_websocket_rejects_operator_impersonation() -> None:
    client, authenticator = _build_a2a_client()
    token = authenticator.issue_token(
        subject="operator-1",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.A2A_RECEIVE.value],
        organization_id="org-1",
        project_id="project-1",
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/a2a/ws/agent-1?token={token}"):
            pass


def test_a2a_websocket_rejects_cross_project_service_binding() -> None:
    client, authenticator = _build_a2a_client()
    token = authenticator.issue_token(
        subject="service-1",
        principal_type=PrincipalType.SERVICE,
        scopes=[Scope.A2A_RECEIVE.value],
        organization_id="org-1",
        project_id="project-2",
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/a2a/ws/agent-1?token={token}"):
            pass


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
        bus = EventBus(sockets, compression_provider=_StubCompressionProvider())
        hub = A2AHub(
            registry,
            state_store=store,
            sockets=sockets,
            compression_provider=_StubCompressionProvider(),
            event_publisher=bus.publish,
        )
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-a",
                name="Agent A",
                organization_id="org-1",
                project_id="project-1",
                capabilities=["delegation"],
                verification_key="agent-a-secret",
            )
        )
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-b",
                name="Agent B",
                organization_id="org-1",
                project_id="project-1",
                capabilities=["delegation"],
                verification_key="agent-b-secret",
            )
        )

        await hub.register_connection("agent-b", {"transport": "websocket"})
        hub._connections["agent-b"] = _FakeWebSocket()

        async with sockets.subscribe("subscriber", organization_id="org-1", project_id="project-1") as queue:
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
