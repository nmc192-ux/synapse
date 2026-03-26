from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.test_a2a_heartbeat import _build_a2a_client
from synapse.models.a2a import A2AMessageType, AgentWireMessage
from synapse.models.agent import AgentKind
from synapse.runtime.a2a import A2AHub
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.security.policies import PrincipalType, Scope
from synapse.security.signing import MessageSigner
from synapse.transports.websocket_manager import WebSocketManager
from synapse.models.a2a import AgentRegistrationRequest


def test_operator_cannot_impersonate_agent_over_a2a_websocket() -> None:
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


def test_cross_project_service_binding_is_denied_for_a2a_websocket() -> None:
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


def test_invalid_signature_and_replayed_nonce_are_denied() -> None:
    signer = MessageSigner()
    store = InMemoryRuntimeStateStore()
    registry = AgentRegistry(state_store=store)
    hub = A2AHub(registry, state_store=store, sockets=WebSocketManager(state_store=store))
    hub.register_agent(
        AgentRegistrationRequest(
            agent_id="agent-a",
            name="Agent A",
            kind=AgentKind.A2A,
            organization_id="org-1",
            project_id="project-1",
            verification_key="agent-a-secret",
        )
    )
    hub.register_agent(
        AgentRegistrationRequest(
            agent_id="agent-b",
            name="Agent B",
            kind=AgentKind.A2A,
            organization_id="org-1",
            project_id="project-1",
            verification_key="agent-b-secret",
        )
    )

    invalid = signer.sign_wire_message(
        AgentWireMessage(
            type=A2AMessageType.SEND_MESSAGE,
            agent="agent-a",
            sender_id="agent-a",
            recipient_id="agent-b",
            target_agent="agent-b",
            organization_id="org-1",
            project_id="project-1",
            payload={"step": "tampered"},
            nonce="nonce-invalid",
        ),
        signing_key="agent-a-secret",
        key_id="default",
        nonce="nonce-invalid",
    ).model_copy(update={"signature": "bad-signature"})

    with pytest.raises(ValueError, match="signature"):
        hub.from_wire_message(invalid)

    replay = signer.sign_wire_message(
        AgentWireMessage(
            type=A2AMessageType.SEND_MESSAGE,
            agent="agent-a",
            sender_id="agent-a",
            recipient_id="agent-b",
            target_agent="agent-b",
            organization_id="org-1",
            project_id="project-1",
            payload={"step": "ok"},
            nonce="nonce-replay",
        ),
        signing_key="agent-a-secret",
        key_id="default",
        nonce="nonce-replay",
    )
    hub.from_wire_message(replay)

    with pytest.raises(ValueError, match="nonce"):
        hub.from_wire_message(replay)
