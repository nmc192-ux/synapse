import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    TASK = "task"
    LONG_TERM = "long_term"


class MemoryRecord(BaseModel):
    memory_id: str
    agent_id: str
    run_id: str | None = None
    memory_type: MemoryType
    content: str
    embedding: list[float] = Field(default_factory=list)
    timestamp: datetime


class MemoryStoreRequest(BaseModel):
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    run_id: str | None = None
    memory_type: MemoryType
    content: str
    embedding: list[float] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemorySearchRequest(BaseModel):
    agent_id: str
    query: str | None = None
    embedding: list[float] = Field(default_factory=list)
    memory_type: MemoryType | None = None
    limit: int = 5


class MemorySearchResult(BaseModel):
    memory: MemoryRecord
    score: float
