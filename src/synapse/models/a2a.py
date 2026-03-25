import uuid
from enum import Enum

from pydantic import BaseModel, Field

from synapse.models.agent import AgentDefinition
from synapse.models.task import TaskRequest, TaskResult


class A2AMessageType(str, Enum):
    DISCOVER = "discover"
    DISCOVER_RESPONSE = "discover_response"
    REQUEST = "request"
    RESPONSE = "response"
    DELEGATE = "delegate"
    TASK_RESULT = "task_result"
    ERROR = "error"


class A2AEnvelope(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: A2AMessageType
    sender_agent_id: str
    recipient_agent_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class AgentPresence(BaseModel):
    agent: AgentDefinition
    connected: bool = False


class DiscoveryPayload(BaseModel):
    agents: list[AgentPresence] = Field(default_factory=list)


class DelegatePayload(BaseModel):
    task: TaskRequest


class TaskResultPayload(BaseModel):
    task: TaskResult
