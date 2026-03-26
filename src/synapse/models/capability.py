from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class CapabilityAdvertisementRequest(BaseModel):
    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    description: str | None = None
    endpoint: str | None = None
    latency: float = 0.0
    availability: bool = True
    reputation: float = 0.5
    metadata: dict[str, object] = Field(default_factory=dict)


class CapabilityRecord(BaseModel):
    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    description: str | None = None
    endpoint: str | None = None
    latency: float = 0.0
    availability: bool = True
    reputation: float = 0.5
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)
