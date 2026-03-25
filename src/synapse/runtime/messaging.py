from synapse.models.message import AgentMessage


class AgentMessageBus:
    def __init__(self) -> None:
        self._messages: list[AgentMessage] = []

    def publish(self, message: AgentMessage) -> AgentMessage:
        self._messages.append(message)
        return message

    def list_messages(self) -> list[AgentMessage]:
        return list(self._messages)
