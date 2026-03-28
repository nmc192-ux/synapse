from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentWireMessage
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.runtime.a2a import A2AHub
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope
from synapse.security.signing import MessageSigner
from synapse.transports.websocket_manager import WebSocketManager


class _StubBrowserService:
    def __init__(self) -> None:
        self.browser = object()
        self.sandbox = object()
        self.budget_service = SimpleNamespace(budget_manager=AgentBudgetManager())

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return SimpleNamespace(session_id=session_id)

    async def save_session_state(self, *args, **kwargs):
        return None

    async def restore_session_state(self, *args, **kwargs):
        return None


class _StubTaskManager:
    async def create_task(self, request):
        return request

    async def claim_task(self, task_id, request):
        return request

    async def update_task(self, task_id, request):
        return request

    async def list_active_tasks(self):
        return []


class _StubSafety:
    def validate_task(self, request):
        return None


class _StubMemoryManager:
    async def store(self, request):
        return request

    async def search(self, request):
        return []

    async def get_recent(self, agent_id: str, limit: int = 10):
        return []

    async def get_recent_by_type(self, agent_id: str, limit_per_type: int = 4):
        return {}


def _build_a2a_app() -> tuple[TestClient, Authenticator]:
    store = InMemoryRuntimeStateStore()
    settings = Settings(
        auth_required=True,
        jwt_secret='a2a-route-secret',
        jwt_issuer='synapse-test',
        jwt_audience='synapse-test-api',
        a2a_service_agent_allowlist={'service-1': ['*']},
    )
    authenticator = Authenticator(settings)
    registry = AgentRegistry(state_store=store)
    sockets = WebSocketManager(state_store=store)
    a2a = A2AHub(registry, state_store=store, sockets=sockets)
    orchestrator = RuntimeOrchestrator(
        browser=_StubBrowserService(),
        agents=registry,
        tools=SimpleNamespace(),
        messages=SimpleNamespace(),
        a2a=a2a,
        memory_manager=_StubMemoryManager(),
        task_manager=_StubTaskManager(),
        sockets=sockets,
        sandbox=SimpleNamespace(),
        safety=_StubSafety(),
        budget_manager=AgentBudgetManager(),
        state_store=store,
        authenticator=authenticator,
    )
    orchestrator.scheduler = None
    orchestrator.task_runtime.scheduler = None

    for agent_id, project_id in (
        ('agent-a', 'project-1'),
        ('agent-b', 'project-1'),
        ('agent-c', 'project-2'),
    ):
        asyncio.run(
            orchestrator.register_agent(
                AgentDefinition(
                    agent_id=agent_id,
                    kind=AgentKind.OPENCLAW,
                    name=agent_id,
                    organization_id='org-1',
                    project_id=project_id,
                )
            )
        )

    app = FastAPI()
    app.include_router(router, prefix='/api')
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    return TestClient(app), authenticator


def _service_token(authenticator: Authenticator, *, project_id: str = 'project-1', scopes: list[str] | None = None) -> str:
    return authenticator.issue_token(
        subject='service-1',
        principal_type=PrincipalType.SERVICE,
        scopes=scopes or [Scope.ADMIN.value, Scope.A2A_SEND.value, Scope.A2A_RECEIVE.value],
        organization_id='org-1',
        project_id=project_id,
    )


def _signed_wire_message(*, nonce: str, target_agent: str = 'agent-b', payload: dict[str, object] | None = None) -> AgentWireMessage:
    return MessageSigner().sign_wire_message(
        AgentWireMessage(
            type=A2AMessageType.SEND_MESSAGE,
            agent='agent-a',
            sender_id='agent-a',
            recipient_id=target_agent,
            target_agent=target_agent,
            organization_id='org-1',
            project_id='project-1',
            payload=payload or {'hello': 'world'},
            nonce=nonce,
        ),
        signing_key='agent-a-verification-key',
        key_id='default',
        nonce=nonce,
    )


def _signed_envelope(*, nonce: str, recipient_agent_id: str = 'agent-b') -> A2AEnvelope:
    signer = MessageSigner()
    envelope = A2AEnvelope(
        type=A2AMessageType.SEND_MESSAGE,
        organization_id='org-1',
        project_id='project-1',
        sender_agent_id='agent-a',
        recipient_agent_id=recipient_agent_id,
        payload={'hello': 'world'},
        nonce=nonce,
    )
    return signer.sign_envelope(envelope, signing_key='agent-a-verification-key')


def test_signed_a2a_route_delivers_to_connected_recipient() -> None:
    client, authenticator = _build_a2a_app()
    token = _service_token(authenticator)
    headers = {'Authorization': f'Bearer {token}', 'X-Synapse-Project-Id': 'project-1'}

    with client.websocket_connect(f'/api/a2a/ws/agent-b?token={token}') as websocket:
        response = client.post('/api/agents/message', json=_signed_wire_message(nonce='nonce-ok').model_dump(mode='json'), headers=headers)
        assert response.status_code == 200
        delivered = websocket.receive_json()
        assert delivered['agent'] == 'agent-a'
        assert delivered['target_agent'] == 'agent-b'
        assert delivered['payload']['hello'] == 'world'


def test_missing_signature_is_rejected_on_signed_wire_route() -> None:
    client, authenticator = _build_a2a_app()
    token = _service_token(authenticator)
    headers = {'Authorization': f'Bearer {token}', 'X-Synapse-Project-Id': 'project-1'}
    unsigned = AgentWireMessage(
        type=A2AMessageType.SEND_MESSAGE,
        agent='agent-a',
        sender_id='agent-a',
        recipient_id='agent-b',
        target_agent='agent-b',
        organization_id='org-1',
        project_id='project-1',
        payload={'hello': 'world'},
    )
    response = client.post('/api/agents/message', json=unsigned.model_dump(mode='json'), headers=headers)
    assert response.status_code == 403
    assert 'signature' in response.text.lower()


def test_invalid_signature_is_rejected_on_signed_wire_route() -> None:
    client, authenticator = _build_a2a_app()
    token = _service_token(authenticator)
    headers = {'Authorization': f'Bearer {token}', 'X-Synapse-Project-Id': 'project-1'}
    invalid = _signed_wire_message(nonce='nonce-invalid').model_copy(update={'signature': 'bad-signature'})
    response = client.post('/api/agents/message', json=invalid.model_dump(mode='json'), headers=headers)
    assert response.status_code == 403
    assert 'signature' in response.text.lower()


def test_replayed_nonce_is_rejected_on_signed_wire_route() -> None:
    client, authenticator = _build_a2a_app()
    token = _service_token(authenticator)
    headers = {'Authorization': f'Bearer {token}', 'X-Synapse-Project-Id': 'project-1'}
    with client.websocket_connect(f'/api/a2a/ws/agent-b?token={token}') as websocket:
        message = _signed_wire_message(nonce='nonce-replay')
        first = client.post('/api/agents/message', json=message.model_dump(mode='json'), headers=headers)
        assert first.status_code == 200
        websocket.receive_json()
        second = client.post('/api/agents/message', json=message.model_dump(mode='json'), headers=headers)
        assert second.status_code == 403
        assert 'nonce' in second.text.lower()


def test_cross_project_a2a_is_denied() -> None:
    client, authenticator = _build_a2a_app()
    token = _service_token(authenticator)
    headers = {'Authorization': f'Bearer {token}', 'X-Synapse-Project-Id': 'project-1'}
    response = client.post('/api/agents/message', json=_signed_wire_message(nonce='nonce-cross', target_agent='agent-c').model_dump(mode='json'), headers=headers)
    assert response.status_code == 403


def test_disconnected_recipient_returns_clean_route_error() -> None:
    client, authenticator = _build_a2a_app()
    token = _service_token(authenticator)
    headers = {'Authorization': f'Bearer {token}', 'X-Synapse-Project-Id': 'project-1'}
    response = client.post('/api/agents/message', json=_signed_wire_message(nonce='nonce-offline').model_dump(mode='json'), headers=headers)
    assert response.status_code == 409
    assert 'not connected' in response.text.lower()


def test_envelope_route_returns_error_envelope_instead_of_500_for_disconnected_recipient() -> None:
    client, authenticator = _build_a2a_app()
    token = _service_token(authenticator)
    headers = {'Authorization': f'Bearer {token}', 'X-Synapse-Project-Id': 'project-1'}
    response = client.post('/api/a2a/messages', json=_signed_envelope(nonce='nonce-envelope').model_dump(mode='json'), headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload['type'] == 'ERROR'
    assert payload['sender_agent_id'] == 'synapse'
    assert 'not connected' in payload['payload']['message'].lower()
