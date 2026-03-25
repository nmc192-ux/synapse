from enum import Enum

from pydantic import BaseModel, Field


class AgentKind(str, Enum):
    OPENCLAW = "openclaw"
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    A2A = "a2a"
    CUSTOM = "custom"


class AgentCapabilities(BaseModel):
    navigate: bool = True
    extract: bool = True
    call_tools: bool = True
    communicate: bool = True
    execute_tasks: bool = True


class AgentDefinition(BaseModel):
    agent_id: str = Field(..., description="Runtime identifier for the agent instance.")
    kind: AgentKind
    name: str
    description: str | None = None
    endpoint: str | None = Field(default=None, description="Optional upstream endpoint.")
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    metadata: dict[str, str] = Field(default_factory=dict)
