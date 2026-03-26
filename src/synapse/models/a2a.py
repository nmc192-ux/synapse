import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from synapse.models.agent import AgentDefinition, AgentExecutionLimits, AgentExecutionPolicy, AgentSecurityPolicy
from synapse.models.task import TaskRequest, TaskResult


class A2AMessageType(str, Enum):
    REGISTER_AGENT = "REGISTER_AGENT"
    DISCOVER_AGENTS = "DISCOVER_AGENTS"
    SEND_MESSAGE = "SEND_MESSAGE"
    TASK_REQUEST = "TASK_REQUEST"
    REQUEST_TASK = "TASK_REQUEST"
    TASK_ACCEPT = "TASK_ACCEPT"
    TASK_RESULT = "TASK_RESULT"
    TASK_REJECT = "TASK_REJECT"
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
    key_id: str | None = None
    nonce: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature: str | None = None
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
    organization_id: str | None = None
    project_id: str | None = None
    owner_user_id: str | None = None
    name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    reputation: float = 0.5
    latency: float = 0.0
    security: AgentSecurityPolicy = Field(default_factory=AgentSecurityPolicy)
    limits: AgentExecutionLimits | None = None
    execution_policy: AgentExecutionPolicy = Field(default_factory=AgentExecutionPolicy)
    metadata: dict[str, str] = Field(default_factory=dict)
    verification_key: str | None = None
    key_id: str = "default"
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentWireMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: A2AMessageType
    agent: str
    target_agent: str | None = None
    sender_id: str | None = None
    recipient_id: str | None = None
    key_id: str | None = None
    nonce: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class AgentIdentityRecord(BaseModel):
    agent_id: str
    verification_key: str
    key_id: str
    reputation: float = 0.5
    capabilities: list[str] = Field(default_factory=list)
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature: str | None = None


class AgentDelegateRequest(BaseModel):
    agent: str
    target_agent: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
