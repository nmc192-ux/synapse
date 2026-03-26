import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentIdentityRecord, AgentPresence, AgentRegistrationRequest, AgentWireMessage
from synapse.models.agent import AgentDefinition, AgentDiscoveryEntry, AgentKind
from synapse.models.runtime_event import EventType, RuntimeEvent
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.compression.noop import NoOpCompressionProvider
from synapse.models.runtime_state import AgentRuntimeStatus, ConnectionState
from synapse.models.task import TaskRequest, TaskResult
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.security import AgentSecuritySandbox, SandboxApprovalRequiredError
from synapse.runtime.state_store import RuntimeStateStore
from synapse.security.identity import AgentIdentityManager
from synapse.security.signing import MessageExpiredError, MessageReplayError, MessageSigner, SignatureValidationError
from synapse.transports.websocket_manager import WebSocketManager


TaskExecutor = Callable[[TaskRequest], Awaitable[TaskResult]]


class A2AHub:
    def __init__(
        self,
        agents: AgentRegistry,
        state_store: RuntimeStateStore | None = None,
        sockets: WebSocketManager | None = None,
        compression_provider: CompressionProvider | None = None,
        sandbox: AgentSecuritySandbox | None = None,
    ) -> None:
        self.agents = agents
        self._connections: dict[str, WebSocket] = {}
        self._connection_state: dict[str, ConnectionState] = {}
        self._task_executor: TaskExecutor | None = None
        self._state_store = state_store
        self._sockets = sockets
        self._compression_provider = compression_provider or NoOpCompressionProvider()
        self._sandbox = sandbox
        self._signer = MessageSigner()
        self._identity_manager = AgentIdentityManager("synapse-agent-identity")
        self._seen_nonces: dict[str, set[str]] = {}

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store

    def set_sockets(self, sockets: WebSocketManager) -> None:
        self._sockets = sockets

    def set_sandbox(self, sandbox: AgentSecuritySandbox | None) -> None:
        self._sandbox = sandbox

    def set_compression_provider(self, compression_provider: CompressionProvider | None) -> None:
        self._compression_provider = compression_provider or NoOpCompressionProvider()

    def set_task_executor(self, executor: TaskExecutor) -> None:
        self._task_executor = executor

    def register_agent(self, request: AgentRegistrationRequest) -> AgentDefinition:
        definition = AgentDefinition(
            agent_id=request.agent_id,
            kind=AgentKind.A2A,
            name=request.name,
            description=request.description,
            endpoint=request.endpoint,
            capability_tags=request.capabilities,
            reputation=request.reputation,
            latency=request.latency,
            security=request.security,
            limits=request.limits,
            execution_policy=request.execution_policy,
            metadata=request.metadata,
        )
        agent = self.agents.register(definition)
        identity = self._identity_manager.issue_identity(
            agent_id=request.agent_id,
            verification_key=request.verification_key or f"{request.agent_id}-verification-key",
            key_id=request.key_id,
            reputation=request.reputation,
            capabilities=request.capabilities,
            issued_at=request.issued_at,
        )
        self.agents.set_identity(identity)
        return agent

    async def connect(self, agent_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[agent_id] = websocket
        if agent_id not in {agent.agent_id for agent in self.agents.list()}:
            self.register_agent(
                AgentRegistrationRequest(
                    agent_id=agent_id,
                    name=agent_id,
                    description="Auto-registered A2A agent connection.",
                )
            )
        await self.agents.save_to_store(self.agents.get(agent_id))
        await self.register_connection(agent_id, {"transport": "websocket"})
        await self.heartbeat(agent_id)
        await self.cleanup_stale_connections()

    async def disconnect(self, agent_id: str) -> None:
        self._connections.pop(agent_id, None)
        await self.mark_disconnected(agent_id)

    def list_agents(self) -> list[AgentPresence]:
        connected_ids = set(self._connections)
        return [
            AgentPresence(agent=agent, connected=agent.agent_id in connected_ids)
            for agent in self.agents.list()
        ]

    def find_agents(self, capability: str) -> list[AgentDiscoveryEntry]:
        return self.agents.find(capability, available_agent_ids=set(self._connections))

    async def delegate_task(
        self,
        sender_agent_id: str,
        recipient_agent_id: str,
        task: TaskRequest,
        *,
        parent_run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> TaskResult:
        if self._task_executor is None:
            raise RuntimeError("Task executor is not configured.")
        try:
            await self._authorize_delegation(sender_agent_id, recipient_agent_id, task.run_id)
        except Exception as exc:
            reject_envelope = A2AEnvelope(
                type=A2AMessageType.TASK_REJECT,
                sender_agent_id=recipient_agent_id,
                recipient_agent_id=sender_agent_id,
                correlation_id=correlation_id or task.run_id or task.task_id,
                payload={"task_id": task.task_id, "run_id": task.run_id, "reason": str(exc)},
            )
            await self._broadcast_delegation_event(EventType.TASK_DELEGATION_REJECTED, reject_envelope, task)
            raise
        request_envelope = A2AEnvelope(
            type=A2AMessageType.TASK_REQUEST,
            sender_agent_id=sender_agent_id,
            recipient_agent_id=recipient_agent_id,
            correlation_id=correlation_id or task.run_id or task.task_id,
            payload={"task": task.model_dump(mode="json"), "parent_run_id": parent_run_id},
        )
        await self._broadcast_delegation_event(EventType.TASK_DELEGATION_REQUESTED, request_envelope, task)

        accept_envelope = A2AEnvelope(
            type=A2AMessageType.TASK_ACCEPT,
            sender_agent_id=recipient_agent_id,
            recipient_agent_id=sender_agent_id,
            correlation_id=request_envelope.message_id,
            payload={"task_id": task.task_id, "run_id": task.run_id, "parent_run_id": parent_run_id},
        )
        await self._broadcast_delegation_event(EventType.TASK_DELEGATION_ACCEPTED, accept_envelope, task)

        try:
            task_result = await self._task_executor(task)
        except Exception as exc:
            reject_envelope = A2AEnvelope(
                type=A2AMessageType.TASK_REJECT,
                sender_agent_id=recipient_agent_id,
                recipient_agent_id=sender_agent_id,
                correlation_id=request_envelope.message_id,
                payload={"task_id": task.task_id, "run_id": task.run_id, "reason": str(exc)},
            )
            await self._broadcast_delegation_event(EventType.TASK_DELEGATION_REJECTED, reject_envelope, task)
            raise
        result_envelope = A2AEnvelope(
            type=A2AMessageType.TASK_RESULT,
            sender_agent_id=recipient_agent_id,
            recipient_agent_id=sender_agent_id,
            correlation_id=request_envelope.message_id,
            payload={"task": task_result.model_dump(mode="json"), "parent_run_id": parent_run_id},
        )
        await self._broadcast_delegation_event(EventType.TASK_DELEGATION_COMPLETED, result_envelope, task)
        return task_result

    async def handle_message(self, sender_agent_id: str, payload: dict[str, object]) -> A2AEnvelope | None:
        await self.heartbeat(sender_agent_id)
        envelope = A2AEnvelope.model_validate(
            {**payload, "sender_agent_id": sender_agent_id}
        )
        self._verify_envelope(envelope)

        if envelope.type in {A2AMessageType.DISCOVER, A2AMessageType.DISCOVER_AGENTS}:
            response = A2AEnvelope(
                type=A2AMessageType.DISCOVER_RESPONSE,
                sender_agent_id="synapse",
                recipient_agent_id=sender_agent_id,
                correlation_id=envelope.message_id,
                payload={
                    "agents": [
                        presence.model_dump(mode="json")
                        for presence in self.list_agents()
                    ]
                },
            )
            await self.send(response)
            return response

        if envelope.type in {
            A2AMessageType.REQUEST,
            A2AMessageType.RESPONSE,
            A2AMessageType.SEND_MESSAGE,
        }:
            return await self._send_or_error(envelope, sender_agent_id)

        if envelope.type in {A2AMessageType.DELEGATE, A2AMessageType.REQUEST_TASK, A2AMessageType.TASK_REQUEST}:
            if self._task_executor is None:
                error = self._build_error(
                    sender_agent_id=sender_agent_id,
                    correlation_id=envelope.message_id,
                    message="Task executor is not configured.",
                )
                await self.send(error)
                return error

            task = TaskRequest.model_validate(envelope.payload["task"])
            try:
                await self._authorize_delegation(sender_agent_id, envelope.recipient_agent_id, task.run_id)
            except SandboxApprovalRequiredError as exc:
                await self._emit_approval_required(sender_agent_id, task.run_id, exc, envelope)
                raise ValueError(exc.reason) from exc
            delegated_task = task.model_copy(update={"agent_id": envelope.recipient_agent_id or task.agent_id})
            accept = A2AEnvelope(
                type=A2AMessageType.TASK_ACCEPT,
                sender_agent_id=envelope.recipient_agent_id or delegated_task.agent_id,
                recipient_agent_id=sender_agent_id,
                correlation_id=envelope.message_id,
                payload={"task_id": delegated_task.task_id, "run_id": delegated_task.run_id},
            )
            await self._send_or_error(accept, sender_agent_id)
            task_result = await self._task_executor(delegated_task)
            response = A2AEnvelope(
                type=A2AMessageType.TASK_RESULT,
                sender_agent_id=envelope.recipient_agent_id or delegated_task.agent_id,
                recipient_agent_id=sender_agent_id,
                correlation_id=envelope.message_id,
                payload={"task": task_result.model_dump(mode="json")},
            )
            return await self._send_or_error(response, sender_agent_id)

        error = self._build_error(
            sender_agent_id=sender_agent_id,
            correlation_id=envelope.message_id,
            message=f"Unsupported A2A message type: {envelope.type}",
        )
        await self.send(error)
        return error

    async def send(self, envelope: A2AEnvelope) -> None:
        if envelope.recipient_agent_id is None:
            return

        websocket = self._connections.get(envelope.recipient_agent_id)
        if websocket is None:
            raise KeyError(f"Agent is not connected: {envelope.recipient_agent_id}")
        await websocket.send_json(self.to_wire_message(envelope).model_dump(mode="json"))
        await self.heartbeat(envelope.recipient_agent_id)
        await self._emit_compact_message(envelope)

    def to_wire_message(self, envelope: A2AEnvelope) -> AgentWireMessage:
        target_agent = envelope.recipient_agent_id
        if envelope.type in {A2AMessageType.REQUEST, A2AMessageType.RESPONSE, A2AMessageType.SEND_MESSAGE}:
            message_type = A2AMessageType.SEND_MESSAGE
        elif envelope.type in {A2AMessageType.DELEGATE, A2AMessageType.REQUEST_TASK}:
            message_type = A2AMessageType.REQUEST_TASK
        elif envelope.type == A2AMessageType.DISCOVER_RESPONSE:
            message_type = A2AMessageType.DISCOVER_RESPONSE
        elif envelope.type == A2AMessageType.TASK_RESULT:
            message_type = A2AMessageType.TASK_RESULT
        elif envelope.type in {A2AMessageType.ERROR, A2AMessageType.ERROR_LEGACY}:
            message_type = A2AMessageType.ERROR
        else:
            message_type = envelope.type

        payload = {**envelope.payload, "message_id": envelope.message_id}
        if envelope.correlation_id:
            payload["correlation_id"] = envelope.correlation_id
        identity = self.agents.get_identity(envelope.sender_agent_id)
        wire_message = AgentWireMessage(
            message_id=envelope.message_id,
            type=message_type,
            agent=envelope.sender_agent_id,
            sender_id=envelope.sender_agent_id,
            target_agent=target_agent,
            recipient_id=target_agent,
            key_id=identity.key_id,
            nonce=envelope.nonce or str(uuid.uuid4()),
            timestamp=envelope.timestamp,
            payload=payload,
        )
        return self._signer.sign_wire_message(wire_message, signing_key=identity.verification_key, key_id=identity.key_id)

    def from_wire_message(self, message: AgentWireMessage) -> A2AEnvelope:
        self._verify_wire_message(message)
        payload = dict(message.payload)
        message_id = str(payload.pop("message_id", message.message_id))
        correlation_id = payload.pop("correlation_id", None)
        if message.type == A2AMessageType.SEND_MESSAGE:
            envelope_type = A2AMessageType.REQUEST
        elif message.type == A2AMessageType.REQUEST_TASK:
            envelope_type = A2AMessageType.DELEGATE
        elif message.type == A2AMessageType.DISCOVER_AGENTS:
            envelope_type = A2AMessageType.DISCOVER
        else:
            envelope_type = message.type

        return A2AEnvelope(
            message_id=message_id,
            type=envelope_type,
            sender_agent_id=message.agent,
            recipient_agent_id=message.target_agent,
            correlation_id=correlation_id,
            key_id=message.key_id,
            nonce=message.nonce,
            timestamp=message.timestamp,
            signature=message.signature,
            payload=payload,
        )

    def sign_wire_message(self, message: AgentWireMessage) -> AgentWireMessage:
        identity = self.agents.get_identity(message.agent)
        return self._signer.sign_wire_message(
            message,
            signing_key=identity.verification_key,
            key_id=identity.key_id,
            nonce=message.nonce or str(uuid.uuid4()),
        )

    def _build_error(
        self,
        sender_agent_id: str,
        correlation_id: str | None,
        message: str,
    ) -> A2AEnvelope:
        return A2AEnvelope(
            type=A2AMessageType.ERROR,
            sender_agent_id="synapse",
            recipient_agent_id=sender_agent_id,
            correlation_id=correlation_id,
            payload={"message": message},
        )

    async def _send_or_error(self, envelope: A2AEnvelope, sender_agent_id: str) -> A2AEnvelope:
        try:
            await self.send(envelope)
            return envelope
        except KeyError as exc:
            error = self._build_error(
                sender_agent_id=sender_agent_id,
                correlation_id=envelope.message_id,
                message=str(exc),
            )
            await self.send(error)
        return error

    async def _emit_compact_message(self, envelope: A2AEnvelope) -> None:
        if self._sockets is None:
            return
        compact_payload = await self._build_compact_payload(envelope)
        await self._sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.A2A_MESSAGE_COMPRESSED,
                    run_id=self._run_id_from_payload(envelope.payload),
                    agent_id=envelope.sender_agent_id,
                task_id=self._task_id_from_payload(envelope.payload),
                source="a2a_runtime",
                payload={
                    "message_id": envelope.message_id,
                    "type": envelope.type.value,
                    "sender_agent_id": envelope.sender_agent_id,
                    "recipient_agent_id": envelope.recipient_agent_id,
                    "key_id": envelope.key_id,
                    "nonce": envelope.nonce,
                    "compact_payload": compact_payload,
                    "correlation_id": envelope.correlation_id,
                },
                correlation_id=envelope.correlation_id or envelope.message_id,
            )
        )

    async def _emit_approval_required(
        self,
        agent_id: str,
        run_id: str | None,
        exc: SandboxApprovalRequiredError,
        envelope: A2AEnvelope,
    ) -> None:
        if self._sockets is None:
            return
        await self._sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.APPROVAL_REQUIRED,
                run_id=run_id,
                agent_id=agent_id,
                source="a2a_runtime",
                payload={
                    "action": exc.action,
                    "reason": exc.reason,
                    "recipient_agent_id": envelope.recipient_agent_id,
                    "message_id": envelope.message_id,
                    **exc.metadata,
                },
                correlation_id=envelope.correlation_id or envelope.message_id,
            )
        )

    async def _build_compact_payload(self, envelope: A2AEnvelope) -> dict[str, Any]:
        payload = dict(envelope.payload)
        if envelope.type in {A2AMessageType.DELEGATE, A2AMessageType.REQUEST_TASK, A2AMessageType.TASK_REQUEST}:
            task = payload.get("task")
            if isinstance(task, dict):
                return {
                    "mode": "delegation-summary",
                    "task_id": task.get("task_id"),
                    "goal": str(task.get("goal", ""))[:160],
                    "agent_id": task.get("agent_id"),
                    "action_count": len(task.get("actions", []) or []),
                }
        if envelope.type in {A2AMessageType.TASK_RESULT, A2AMessageType.TASK_RESULT_LEGACY}:
            task = payload.get("task")
            if isinstance(task, dict):
                return {
                    "mode": "task-result-summary",
                    "task_id": task.get("task_id"),
                    "status": task.get("status"),
                    "success": task.get("success"),
                }
        if envelope.type == A2AMessageType.TASK_ACCEPT:
            return {
                "mode": "task-accept-summary",
                "task_id": payload.get("task_id"),
                "run_id": payload.get("run_id"),
            }
        if envelope.type == A2AMessageType.TASK_REJECT:
            return {
                "mode": "task-reject-summary",
                "task_id": payload.get("task_id"),
                "reason": payload.get("reason"),
            }
        if envelope.type in {A2AMessageType.DISCOVER_RESPONSE, A2AMessageType.DISCOVER, A2AMessageType.DISCOVER_AGENTS}:
            return {
                "mode": "status-summary",
                "agent_count": len(payload.get("agents", []) or []),
                "message_type": envelope.type.value,
            }
        if self._requires_exact_fidelity(envelope.type):
            return {
                "mode": "exact-fidelity-preserved",
                "keys": sorted(payload.keys()),
            }
        return await self._compression_provider.compress_json(
            payload,
            context={
                "message_type": envelope.type.value,
                "sender_agent_id": envelope.sender_agent_id,
                "recipient_agent_id": envelope.recipient_agent_id,
                "channel": "a2a",
            },
        )

    @staticmethod
    def _requires_exact_fidelity(message_type: A2AMessageType) -> bool:
        return message_type in {
            A2AMessageType.REQUEST,
            A2AMessageType.RESPONSE,
            A2AMessageType.DELEGATE,
            A2AMessageType.REQUEST_TASK,
            A2AMessageType.TASK_REQUEST,
            A2AMessageType.TASK_ACCEPT,
            A2AMessageType.TASK_RESULT,
            A2AMessageType.TASK_RESULT_LEGACY,
            A2AMessageType.TASK_REJECT,
        }

    async def _broadcast_delegation_event(
        self,
        event_type: EventType,
        envelope: A2AEnvelope,
        task: TaskRequest,
    ) -> None:
        if self._sockets is None:
            return
        await self._sockets.broadcast(
            RuntimeEvent(
                event_type=event_type,
                run_id=task.run_id,
                agent_id=envelope.sender_agent_id,
                task_id=task.task_id,
                source="a2a_runtime",
                payload={
                    "message_id": envelope.message_id,
                    "sender_agent_id": envelope.sender_agent_id,
                    "recipient_agent_id": envelope.recipient_agent_id,
                    "task_id": task.task_id,
                    "run_id": task.run_id,
                    "parent_run_id": task.parent_run_id,
                },
                correlation_id=envelope.correlation_id or envelope.message_id,
            )
        )

    @staticmethod
    def _task_id_from_payload(payload: dict[str, object]) -> str | None:
        task = payload.get("task")
        if isinstance(task, dict):
            task_id = task.get("task_id")
            return str(task_id) if task_id is not None else None
        return None

    @staticmethod
    def _run_id_from_payload(payload: dict[str, object]) -> str | None:
        task = payload.get("task")
        if isinstance(task, dict):
            run_id = task.get("run_id")
            return str(run_id) if run_id is not None else None
        run_id = payload.get("run_id")
        return str(run_id) if run_id is not None else None

    def _verify_wire_message(self, message: AgentWireMessage) -> None:
        identity = self.agents.get_identity(message.agent)
        seen = self._seen_nonces.setdefault(message.agent, set())
        self._signer.verify_wire_message(
            message,
            verification_key=identity.verification_key,
            max_age_seconds=300,
            seen_nonces=seen,
        )

    def _verify_envelope(self, envelope: A2AEnvelope) -> None:
        if envelope.signature is None:
            return
        identity = self.agents.get_identity(envelope.sender_agent_id)
        seen = self._seen_nonces.setdefault(envelope.sender_agent_id, set())
        self._signer._verify_signature(
            envelope.signature,
            self._signer._envelope_payload(envelope),
            identity.verification_key,
            timestamp=envelope.timestamp,
            nonce=envelope.nonce,
            max_age_seconds=300,
            seen_nonces=seen,
        )

    async def _authorize_delegation(
        self,
        sender_agent_id: str,
        recipient_agent_id: str | None,
        run_id: str | None,
    ) -> None:
        if self._sandbox is None:
            return
        if run_id is not None and self._state_store is not None:
            payload = await self._state_store.get_run(run_id)
            if isinstance(payload, dict):
                metadata = payload.get("metadata")
                if isinstance(metadata, dict):
                    override = metadata.get("security_policy") or metadata.get("execution_policy")
                    if isinstance(override, dict):
                        self._sandbox.set_run_policy(run_id, override)
        self._sandbox.authorize_delegation(sender_agent_id, recipient_agent_id, run_id=run_id)

    async def register_connection(self, agent_id: str, metadata: dict[str, object]) -> ConnectionState:
        if agent_id not in {agent.agent_id for agent in self.agents.list()}:
            self.register_agent(
                AgentRegistrationRequest(
                    agent_id=agent_id,
                    name=agent_id,
                    description="Auto-registered A2A agent connection.",
                )
            )
            await self.agents.save_to_store(self.agents.get(agent_id))

        now = datetime.now(timezone.utc)
        connection = ConnectionState(
            agent_id=agent_id,
            transport=str(metadata.get("transport", "websocket")),
            connected_at=now,
            last_heartbeat=now,
            status=AgentRuntimeStatus.ACTIVE,
            endpoint_metadata=metadata,
        )
        self._connection_state[agent_id] = connection
        await self.agents.update_agent_status(agent_id, AgentRuntimeStatus.ACTIVE)
        if self._state_store is not None:
            await self._state_store.store_connection(agent_id, connection.model_dump(mode="json"))
        return connection

    async def heartbeat(self, agent_id: str) -> None:
        now = datetime.now(timezone.utc)
        connection = self._connection_state.get(agent_id)
        if connection is None:
            connection = await self.register_connection(agent_id, {"transport": "websocket"})
        else:
            connection.last_heartbeat = now
            connection.status = AgentRuntimeStatus.ACTIVE
            if self._state_store is not None:
                await self._state_store.store_connection(agent_id, connection.model_dump(mode="json"))
        await self.agents.update_agent_last_seen(agent_id, now)
        await self.agents.update_agent_status(agent_id, AgentRuntimeStatus.ACTIVE)
        if self._sockets is not None:
            await self._sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.AGENT_STATUS_UPDATED,
                    agent_id=agent_id,
                    source="a2a_runtime",
                    payload={"agent_id": agent_id, "status": AgentRuntimeStatus.ACTIVE.value},
                )
            )
        if self._sockets is not None:
            await self._sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.CONNECTION_HEARTBEAT,
                    agent_id=agent_id,
                    source="a2a_runtime",
                    payload={"agent_id": agent_id, "last_heartbeat": now.isoformat()},
                )
            )

    async def mark_disconnected(self, agent_id: str) -> None:
        connection = self._connection_state.get(agent_id)
        if connection is not None:
            connection.status = AgentRuntimeStatus.OFFLINE
            connection.last_heartbeat = datetime.now(timezone.utc)
            if self._state_store is not None:
                await self._state_store.store_connection(agent_id, connection.model_dump(mode="json"))
        if self._state_store is not None:
            await self._state_store.delete_connection(agent_id)
        await self.agents.update_agent_status(agent_id, AgentRuntimeStatus.OFFLINE)
        if self._sockets is not None:
            await self._sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.AGENT_STATUS_UPDATED,
                    agent_id=agent_id,
                    source="a2a_runtime",
                    payload={"agent_id": agent_id, "status": AgentRuntimeStatus.OFFLINE.value},
                )
            )

    async def cleanup_stale_connections(self, ttl_seconds: int = 60) -> list[str]:
        now = datetime.now(timezone.utc)
        stale: list[str] = []
        for agent_id, connection in list(self._connection_state.items()):
            age = (now - connection.last_heartbeat).total_seconds()
            if age > ttl_seconds:
                stale.append(agent_id)
                self._connections.pop(agent_id, None)
                connection.status = AgentRuntimeStatus.OFFLINE
                if self._state_store is not None:
                    await self._state_store.store_connection(agent_id, connection.model_dump(mode="json"))
                await self.agents.update_agent_status(agent_id, AgentRuntimeStatus.OFFLINE)
                if self._sockets is not None:
                    await self._sockets.broadcast(
                        RuntimeEvent(
                            event_type=EventType.AGENT_STATUS_UPDATED,
                            agent_id=agent_id,
                            source="a2a_runtime",
                            payload={"agent_id": agent_id, "status": AgentRuntimeStatus.OFFLINE.value},
                        )
                    )
                if self._sockets is not None:
                    await self._sockets.broadcast(
                        RuntimeEvent(
                            event_type=EventType.CONNECTION_STALE,
                            agent_id=agent_id,
                            source="a2a_runtime",
                            payload={"agent_id": agent_id, "ttl_seconds": ttl_seconds},
                        )
                    )
        return stale

    async def list_persisted_connections(self) -> list[ConnectionState]:
        if self._state_store is None:
            return [state.model_copy() for state in self._connection_state.values()]
        rows = await self._state_store.list_connections()
        return [ConnectionState.model_validate(row) for row in rows]

    async def get_persisted_connection(self, agent_id: str) -> ConnectionState | None:
        if self._state_store is None:
            connection = self._connection_state.get(agent_id)
            return connection.model_copy() if connection is not None else None
        row = await self._state_store.get_connection(agent_id)
        if row is None:
            return None
        return ConnectionState.model_validate(row)
