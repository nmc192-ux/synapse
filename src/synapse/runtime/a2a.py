import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from fastapi import WebSocket

from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentPresence, AgentRegistrationRequest, AgentWireMessage
from synapse.models.agent import AgentDefinition, AgentDiscoveryEntry, AgentKind
from synapse.models.runtime_event import EventType, RuntimeEvent
from synapse.models.runtime_state import AgentRuntimeStatus, ConnectionState
from synapse.models.task import TaskRequest, TaskResult
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import RuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager


TaskExecutor = Callable[[TaskRequest], Awaitable[TaskResult]]


class A2AHub:
    def __init__(
        self,
        agents: AgentRegistry,
        state_store: RuntimeStateStore | None = None,
        sockets: WebSocketManager | None = None,
    ) -> None:
        self.agents = agents
        self._connections: dict[str, WebSocket] = {}
        self._connection_state: dict[str, ConnectionState] = {}
        self._task_executor: TaskExecutor | None = None
        self._state_store = state_store
        self._sockets = sockets

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store

    def set_sockets(self, sockets: WebSocketManager) -> None:
        self._sockets = sockets

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
        return self.agents.register(definition)

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

    async def handle_message(self, sender_agent_id: str, payload: dict[str, object]) -> A2AEnvelope | None:
        await self.heartbeat(sender_agent_id)
        envelope = A2AEnvelope.model_validate(
            {**payload, "sender_agent_id": sender_agent_id}
        )

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

        if envelope.type in {A2AMessageType.DELEGATE, A2AMessageType.REQUEST_TASK}:
            if self._task_executor is None:
                error = self._build_error(
                    sender_agent_id=sender_agent_id,
                    correlation_id=envelope.message_id,
                    message="Task executor is not configured.",
                )
                await self.send(error)
                return error

            task = TaskRequest.model_validate(envelope.payload["task"])
            delegated_task = task.model_copy(update={"agent_id": envelope.recipient_agent_id or task.agent_id})
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
        return AgentWireMessage(
            type=message_type,
            agent=envelope.sender_agent_id,
            target_agent=target_agent,
            payload=payload,
        )

    def from_wire_message(self, message: AgentWireMessage) -> A2AEnvelope:
        payload = dict(message.payload)
        message_id = str(payload.pop("message_id", uuid.uuid4()))
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
            payload=payload,
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
