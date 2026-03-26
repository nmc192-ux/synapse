import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_OPERATOR = "waiting_for_operator"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMED = "resumed"
    CANCELLED = "cancelled"


class RunState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    agent_id: str
    project_id: str | None = None
    status: RunStatus = RunStatus.PENDING
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    checkpoint_id: str | None = None
    current_step: int = 0
    current_phase: str | None = None
    correlation_id: str | None = None
    parent_run_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class RunGraphNode(BaseModel):
    run_id: str
    task_id: str
    agent_id: str
    project_id: str | None = None
    status: RunStatus
    parent_run_id: str | None = None
    current_phase: str | None = None
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    delegation_state: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class RunGraphEdge(BaseModel):
    edge_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_run_id: str
    target_run_id: str
    edge_type: str = "delegation"
    status: str = "linked"
    delegated_to_agent_id: str | None = None
    required_capability: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)


class RunGraph(BaseModel):
    root_run_id: str
    nodes: list[RunGraphNode] = Field(default_factory=list)
    edges: list[RunGraphEdge] = Field(default_factory=list)
    total_runs: int = 0
    completed_runs: int = 0
    failed_runs: int = 0
    active_runs: int = 0
