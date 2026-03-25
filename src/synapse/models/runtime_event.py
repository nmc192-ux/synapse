from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


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


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RuntimeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    agent_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "runtime"
    payload: dict[str, object] = Field(default_factory=dict)
    severity: EventSeverity = EventSeverity.INFO
    correlation_id: str | None = None
