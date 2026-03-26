from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class EventType(str, Enum):
    SESSION_CREATED = "session.created"
    PAGE_NAVIGATED = "page.navigated"
    DATA_EXTRACTED = "data.extracted"
    SCREENSHOT_CAPTURED = "screenshot.captured"
    SECURITY_ALERT = "security.alert"
    BUDGET_UPDATED = "budget.updated"
    LOOP_OBSERVED = "loop.observed"
    LOOP_PLANNED = "loop.planned"
    PLANNER_CONTEXT_COMPRESSED = "planner.context.compressed"
    SPM_COMPRESSED = "spm.compressed"
    LOOP_ACTED = "loop.acted"
    LOOP_EVALUATED = "loop.evaluated"
    LOOP_REFLECTED = "loop.reflected"
    TOOL_CALLED = "tool.called"
    AGENT_REGISTERED = "agent.registered"
    AGENT_STATUS_UPDATED = "agent.status.updated"
    AGENT_MESSAGE = "agent.message"
    A2A_MESSAGE = "a2a.message"
    TASK_UPDATED = "task.updated"
    SESSION_SAVED = "session.saved"
    SESSION_RESTORED = "session.restored"
    CONNECTION_HEARTBEAT = "connection.heartbeat"
    CONNECTION_STALE = "connection.stale"
    CHECKPOINT_SAVED = "checkpoint.saved"
    CHECKPOINT_RESUMED = "checkpoint.resumed"
    POPUP_DISMISSED = "popup.dismissed"
    DOWNLOAD_COMPLETED = "download.completed"
    UPLOAD_COMPLETED = "upload.completed"
    NAVIGATION_ROUTE_CHANGED = "navigation.route_changed"
    BROWSER_ERROR = "browser.error"
    SESSION_EXPIRED = "session.expired"
    MEMORY_COMPRESSED = "memory.compressed"
    RUNTIME_EVENTS_COMPRESSED = "runtime.events.compressed"
    A2A_MESSAGE_COMPRESSED = "a2a.message.compressed"
    APPROVAL_REQUIRED = "approval.required"
    BROWSER_WORKER_STATUS_UPDATED = "browser.worker.status.updated"
    BROWSER_WORKER_HEARTBEAT = "browser.worker.heartbeat"
    BROWSER_TASK_DISPATCHED = "browser.task.dispatched"
    BROWSER_TASK_COMPLETED = "browser.task.completed"
    RUN_ASSIGNED = "run.assigned"
    RUN_REQUEUED = "run.requeued"
    WORKER_UNAVAILABLE = "worker.unavailable"


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RuntimeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    run_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "runtime"
    phase: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    severity: EventSeverity = EventSeverity.INFO
    correlation_id: str | None = None

    @model_validator(mode="after")
    def _populate_phase(self) -> "RuntimeEvent":
        if self.phase is None:
            self.phase = infer_event_phase(self.event_type)
        return self


class RunTimelineEntry(BaseModel):
    event_id: str
    run_id: str
    timestamp: datetime
    event_type: str
    phase: str
    payload: dict[str, object] = Field(default_factory=dict)
    correlation_id: str | None = None
    source: str = "runtime"
    severity: EventSeverity = EventSeverity.INFO
    task_id: str | None = None
    session_id: str | None = None


class RunTimeline(BaseModel):
    run_id: str
    status: str
    started_at: datetime | None = None
    updated_at: datetime | None = None
    event_count: int = 0
    phases: list[str] = Field(default_factory=list)
    entries: list[RunTimelineEntry] = Field(default_factory=list)


class RunReplayView(BaseModel):
    run_id: str
    phase_transitions: list[dict[str, object]] = Field(default_factory=list)
    browser_actions: list[dict[str, object]] = Field(default_factory=list)
    planner_outputs: list[dict[str, object]] = Field(default_factory=list)
    evaluation_results: list[dict[str, object]] = Field(default_factory=list)
    checkpoints: list[dict[str, object]] = Field(default_factory=list)
    budget_updates: list[dict[str, object]] = Field(default_factory=list)
    timeline: list[RunTimelineEntry] = Field(default_factory=list)


def infer_event_phase(event_type: EventType | str) -> str:
    value = event_type.value if isinstance(event_type, EventType) else str(event_type)
    if value.startswith("loop.observed"):
        return "observe"
    if value in {EventType.LOOP_PLANNED.value, EventType.PLANNER_CONTEXT_COMPRESSED.value, EventType.SPM_COMPRESSED.value}:
        return "plan"
    if value in {
        EventType.LOOP_ACTED.value,
        EventType.PAGE_NAVIGATED.value,
        EventType.DATA_EXTRACTED.value,
        EventType.SCREENSHOT_CAPTURED.value,
        EventType.TOOL_CALLED.value,
        EventType.DOWNLOAD_COMPLETED.value,
        EventType.UPLOAD_COMPLETED.value,
        EventType.POPUP_DISMISSED.value,
        EventType.NAVIGATION_ROUTE_CHANGED.value,
    }:
        return "act"
    if value in {EventType.LOOP_EVALUATED.value, EventType.BUDGET_UPDATED.value, EventType.BROWSER_ERROR.value}:
        return "evaluate"
    if value in {EventType.LOOP_REFLECTED.value, EventType.MEMORY_COMPRESSED.value}:
        return "reflect"
    if value.startswith("checkpoint."):
        return "checkpoint"
    if value.startswith("task."):
        return "task"
    if value.startswith("a2a."):
        return "a2a"
    if value.startswith("session."):
        return "session"
    if value.startswith("agent."):
        return "agent"
    return "runtime"
