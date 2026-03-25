import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender_agent_id: str
    recipient_agent_id: str
    content: str
    metadata: dict[str, object] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
