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
    LOOP_ACTED = "loop.acted"
    LOOP_EVALUATED = "loop.evaluated"
    LOOP_REFLECTED = "loop.reflected"
    TOOL_CALLED = "tool.called"
    AGENT_REGISTERED = "agent.registered"
    AGENT_MESSAGE = "agent.message"
    A2A_MESSAGE = "a2a.message"
    TASK_UPDATED = "task.updated"


class RuntimeEvent(BaseModel):
    event_type: EventType
    session_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
