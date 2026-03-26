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
    blocked_domains: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    uploads_allowed: bool = True
    downloads_allowed: bool = True
    screenshot_allowed: bool = True
    dangerous_action_requires_approval: bool = False
    max_cross_domain_jumps: int = 10
    rate_limits: AgentRateLimits = Field(default_factory=AgentRateLimits)
    block_unsafe_actions: bool = True

    def merged(
        self,
        override: "AgentSecurityPolicy | dict[str, object] | None",
    ) -> "AgentSecurityPolicy":
        if override is None:
            return self.model_copy(deep=True)
        if isinstance(override, AgentSecurityPolicy):
            data = override.model_dump(exclude_unset=False)
        else:
            data = dict(override)
        if "rate_limits" in data and isinstance(data["rate_limits"], dict):
            data["rate_limits"] = self.rate_limits.model_copy(update=data["rate_limits"])
        return self.model_copy(update={key: value for key, value in data.items() if value is not None})


class AgentExecutionLimits(BaseModel):
    max_steps: int = 60
    max_pages: int = 25
    max_tool_calls: int = 40
    max_runtime_seconds: int = 180
    max_tokens: int = 40000
    max_memory_writes: int = 100

    def merged(self, override: "AgentExecutionLimits | dict[str, int] | None") -> "AgentExecutionLimits":
        if override is None:
            return self.model_copy()
        if isinstance(override, AgentExecutionLimits):
            data = override.model_dump(exclude_unset=False)
        else:
            data = dict(override)
        return self.model_copy(update={key: value for key, value in data.items() if value is not None})


class AgentExecutionPolicy(BaseModel):
    stop_on_soft_limit: bool = False
    pause_on_hard_limit: bool = False
    save_checkpoint_on_limit: bool = False


class AgentBudgetUsage(BaseModel):
    steps_used: int = 0
    pages_opened: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    memory_writes: int = 0
    runtime_seconds: int = 0
    llm_cost_estimate: float = 0.0
    tool_cost_estimate: float = 0.0
    limits: AgentExecutionLimits = Field(default_factory=AgentExecutionLimits)
    warnings: list[str] = Field(default_factory=list)


class AgentCheckpoint(BaseModel):
    agent_id: str
    state: dict[str, object] = Field(default_factory=dict)
    reason: str | None = None


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
    limits: AgentExecutionLimits | None = None
    execution_policy: AgentExecutionPolicy = Field(default_factory=AgentExecutionPolicy)
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentDiscoveryEntry(BaseModel):
    id: str
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    reputation: float
    latency: float
    availability: bool
    score: float
