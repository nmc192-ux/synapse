import uuid
from enum import Enum

from pydantic import BaseModel, Field

from synapse.models.agent import AgentDefinition
from synapse.models.task import TaskRequest, TaskResult


class A2AMessageType(str, Enum):
    REGISTER_AGENT = "REGISTER_AGENT"
    DISCOVER_AGENTS = "DISCOVER_AGENTS"
    SEND_MESSAGE = "SEND_MESSAGE"
    REQUEST_TASK = "REQUEST_TASK"
    TASK_RESULT = "TASK_RESULT"
    DISCOVER_RESPONSE = "DISCOVER_RESPONSE"
    ERROR = "ERROR"
    DISCOVER = "discover"
    REQUEST = "request"
    RESPONSE = "response"
    DELEGATE = "delegate"
    TASK_RESULT_LEGACY = "task_result"
    ERROR_LEGACY = "error"


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


class AgentRegistrationRequest(BaseModel):
    agent_id: str
    name: str
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentWireMessage(BaseModel):
    type: A2AMessageType
    agent: str
    target_agent: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class AgentDelegateRequest(BaseModel):
    agent: str
    target_agent: str
    payload: dict[str, object] = Field(default_factory=dict)
