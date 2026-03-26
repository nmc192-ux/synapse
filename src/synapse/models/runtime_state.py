import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import AliasChoices, BaseModel, Field, model_validator


class AgentRuntimeStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"


class WorkerRuntimeStatus(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    FAILED = "failed"


class WorkerHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class AgentRuntimeRecord(BaseModel):
    agent_id: str
    organization_id: str | None = None
    project_id: str | None = None
    owner_user_id: str | None = None
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
    run_id: str | None = None
    project_id: str | None = None
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


class BrowserWorkerState(BaseModel):
    worker_id: str
    queue_name: str
    status: WorkerRuntimeStatus = WorkerRuntimeStatus.STARTING
    health_status: WorkerHealthStatus = WorkerHealthStatus.HEALTHY
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    active_sessions: int = 0
    current_request_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    current_runs: list[str] = Field(default_factory=list)
    controller_id: str | None = None
    owned_sessions: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class RunLeaseStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"


class RunLeaseRecord(BaseModel):
    lease_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    worker_id: str
    token: int = 0
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(validation_alias=AliasChoices("expires_at", "lease_expiration"))
    status: RunLeaseStatus = RunLeaseStatus.ACTIVE
    attempts: int = 1
    next_retry_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "acquired_at" not in payload and "lease_acquired_at" in payload:
            payload["acquired_at"] = payload["lease_acquired_at"]
        if "expires_at" not in payload and "lease_expiration" in payload:
            payload["expires_at"] = payload["lease_expiration"]
        payload.pop("lease_acquired_at", None)
        payload.pop("lease_expiration", None)
        return payload


class BrowserTaskRequestRecord(BaseModel):
    action_id: str
    request_id: str | None = None
    run_id: str | None = None
    worker_id: str
    action: str
    session_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    fencing_token: int | None = None
    status: str = "queued"
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _sync_request_id(self) -> "BrowserTaskRequestRecord":
        if self.request_id is None:
            self.request_id = self.action_id
        return self


class BrowserTaskResultRecord(BaseModel):
    action_id: str
    request_id: str | None = None
    run_id: str | None = None
    worker_id: str
    action: str
    session_id: str | None = None
    success: bool = True
    payload: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    fencing_token: int | None = None
    status: str = "completed"
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _sync_request_id(self) -> "BrowserTaskResultRecord":
        if self.request_id is None:
            self.request_id = self.action_id
        return self


class BrowserSessionOwnershipRecord(BaseModel):
    session_id: str
    worker_id: str
    controller_id: str | None = None
    run_id: str | None = None
    project_id: str | None = None
    current_url: str | None = None
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    agent_id: str
    run_id: str | None = None
    project_id: str | None = None
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
    organization_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OperatorInterventionState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INPUT_PROVIDED = "input_provided"
    EXPIRED = "expired"


class OperatorInterventionRecord(BaseModel):
    intervention_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    project_id: str | None = None
    organization_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    checkpoint_id: str | None = None
    reason: str
    state: OperatorInterventionState = OperatorInterventionState.PENDING
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


class BrowserTraceEntry(BaseModel):
    event_id: str
    run_id: str
    session_id: str | None = None
    timestamp: datetime
    event_type: str
    category: str
    level: str = "info"
    message: str | None = None
    url: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class BrowserNetworkEntry(BaseModel):
    event_id: str
    run_id: str
    session_id: str | None = None
    timestamp: datetime
    url: str
    method: str | None = None
    resource_type: str | None = None
    failure_text: str | None = None
    status: str = "failed"
    metadata: dict[str, object] = Field(default_factory=dict)
