import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class AgentRuntimeStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"


class AgentRuntimeRecord(BaseModel):
    agent_id: str
    kind: str
    name: str
    capabilities: list[str] = Field(default_factory=list)
    reputation: float = 0.5
    limits: dict[str, object] = Field(default_factory=dict)
    security_policy: dict[str, object] = Field(default_factory=dict)
    availability: bool = False
    status: AgentRuntimeStatus = AgentRuntimeStatus.IDLE
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    endpoint: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class BrowserSessionState(BaseModel):
    session_id: str
    agent_id: str | None = None
    current_url: str | None = None
    cookies: list[dict[str, object]] = Field(default_factory=list)
    local_storage: dict[str, str] = Field(default_factory=dict)
    session_storage: dict[str, str] = Field(default_factory=dict)
    last_active_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    page_title: str | None = None
    tabs: list[dict[str, object]] = Field(default_factory=list)
    auth_state: dict[str, object] = Field(default_factory=dict)
    downloads: list[dict[str, object]] = Field(default_factory=list)


class ConnectionState(BaseModel):
    agent_id: str
    transport: str = "websocket"
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: AgentRuntimeStatus = AgentRuntimeStatus.ACTIVE
    endpoint_metadata: dict[str, object] = Field(default_factory=dict)


class RuntimeCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    agent_id: str
    current_goal: str
    planner_state: dict[str, object] = Field(default_factory=dict)
    memory_snapshot_reference: str | None = None
    browser_session_reference: str | None = None
    last_action: dict[str, object] = Field(default_factory=dict)
    pending_actions: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeEventRecord(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    agent_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
