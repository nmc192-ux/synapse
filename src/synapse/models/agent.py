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


class AgentRateLimits(BaseModel):
    browser_actions_per_minute: int = 30
    tool_calls_per_minute: int = 15


class AgentSecurityPolicy(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    rate_limits: AgentRateLimits = Field(default_factory=AgentRateLimits)
    block_unsafe_actions: bool = True


class AgentDefinition(BaseModel):
    agent_id: str = Field(..., description="Runtime identifier for the agent instance.")
    kind: AgentKind
    name: str
    description: str | None = None
    endpoint: str | None = Field(default=None, description="Optional upstream endpoint.")
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    capability_tags: list[str] = Field(default_factory=list)
    reputation: float = 0.5
    latency: float = 0.0
    security: AgentSecurityPolicy = Field(default_factory=AgentSecurityPolicy)
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentDiscoveryEntry(BaseModel):
    id: str
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    reputation: float
    latency: float
    availability: bool
    score: float
