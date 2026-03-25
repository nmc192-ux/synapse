import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMED = "resumed"
    CANCELLED = "cancelled"


class RunState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    agent_id: str
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
