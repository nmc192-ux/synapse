from enum import Enum

from pydantic import BaseModel, Field, HttpUrl

from synapse.models.browser import BrowserState, ExtractionResult, ScreenshotResult
from synapse.models.loop import AgentAction


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NavigationRequest(BaseModel):
    session_id: str
    url: HttpUrl


class ExtractionRequest(BaseModel):
    session_id: str
    selector: str = Field(..., description="CSS selector to extract from the current page.")
    attribute: str | None = Field(default=None, description="Optional attribute to read.")


class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)


class TaskRequest(BaseModel):
    task_id: str
    agent_id: str
    goal: str
    session_id: str | None = None
    start_url: HttpUrl | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    actions: list[AgentAction] = Field(default_factory=list)
    constraints: dict[str, object] = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    message: str
    artifacts: dict[str, object] = Field(default_factory=dict)


BrowserArtifact = BrowserState | ExtractionResult | ScreenshotResult
