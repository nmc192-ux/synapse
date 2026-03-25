from enum import Enum

from pydantic import BaseModel, Field


class LoopPhase(str, Enum):
    OBSERVE = "observe"
    PLAN = "plan"
    ACT = "act"
    REFLECT = "reflect"


class AgentActionType(str, Enum):
    OPEN = "open"
    CLICK = "click"
    TYPE = "type"
    EXTRACT = "extract"
    SCREENSHOT = "screenshot"


class AgentAction(BaseModel):
    action_id: str
    type: AgentActionType
    selector: str | None = None
    text: str | None = None
    url: str | None = None
    attribute: str | None = None
    status: str = "pending"
    result: dict[str, object] = Field(default_factory=dict)


class LoopObservation(BaseModel):
    task_id: str
    phase: LoopPhase = LoopPhase.OBSERVE
    event_count: int = 0
    last_event_type: str | None = None
    last_event_payload: dict[str, object] = Field(default_factory=dict)


class LoopPlan(BaseModel):
    task_id: str
    phase: LoopPhase = LoopPhase.PLAN
    actions: list[AgentAction] = Field(default_factory=list)


class LoopReflection(BaseModel):
    task_id: str
    phase: LoopPhase = LoopPhase.REFLECT
    completed_actions: int = 0
    remaining_actions: int = 0
    notes: str = ""
