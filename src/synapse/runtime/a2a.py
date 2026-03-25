import uuid
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentPresence, AgentRegistrationRequest, AgentWireMessage
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.task import TaskRequest, TaskResult
from synapse.runtime.registry import AgentRegistry


TaskExecutor = Callable[[TaskRequest], Awaitable[TaskResult]]


class A2AHub:
    def __init__(self, agents: AgentRegistry) -> None:
        self.agents = agents
        self._connections: dict[str, WebSocket] = {}
        self._task_executor: TaskExecutor | None = None

    def set_task_executor(self, executor: TaskExecutor) -> None:
        self._task_executor = executor

    def register_agent(self, request: AgentRegistrationRequest) -> AgentDefinition:
        definition = AgentDefinition(
            agent_id=request.agent_id,
            kind=AgentKind.A2A,
            name=request.name,
            description=request.description,
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

    def disconnect(self, agent_id: str) -> None:
        self._connections.pop(agent_id, None)

    def list_agents(self) -> list[AgentPresence]:
        connected_ids = set(self._connections)
        return [
            AgentPresence(agent=agent, connected=agent.agent_id in connected_ids)
            for agent in self.agents.list()
        ]

    async def handle_message(self, sender_agent_id: str, payload: dict[str, object]) -> A2AEnvelope | None:
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
