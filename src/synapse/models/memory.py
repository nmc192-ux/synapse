import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    TASK = "task"
    LONG_TERM = "long_term"


class MemoryScope(str, Enum):
    RUN = "run"
    TASK = "task"
    AGENT = "agent"
    LONG_TERM = "long_term"


class MemoryRecord(BaseModel):
    memory_id: str
    agent_id: str
    run_id: str | None = None
    task_id: str | None = None
    memory_type: MemoryType
    memory_scope: MemoryScope | None = None
    content: str
    embedding: list[float] = Field(default_factory=list)
    timestamp: datetime


class MemoryStoreRequest(BaseModel):
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    run_id: str | None = None
    task_id: str | None = None
    memory_type: MemoryType
    memory_scope: MemoryScope | None = None
    content: str
    embedding: list[float] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemorySearchRequest(BaseModel):
    agent_id: str
    run_id: str | None = None
    task_id: str | None = None
    query: str | None = None
    embedding: list[float] = Field(default_factory=list)
    memory_type: MemoryType | None = None
    memory_scope: MemoryScope | None = None
    limit: int = 5


class MemorySearchResult(BaseModel):
    memory: MemoryRecord
    score: float
