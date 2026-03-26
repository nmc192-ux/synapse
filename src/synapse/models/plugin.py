from enum import Enum

from pydantic import BaseModel, Field


class PluginExecutionMode(str, Enum):
    TRUSTED_LOCAL = "trusted_local"
    ISOLATED_HOSTED = "isolated_hosted"


class ToolDescriptor(BaseModel):
    name: str
    description: str = ""
    plugin: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    execution_mode: PluginExecutionMode = PluginExecutionMode.TRUSTED_LOCAL
    isolation_strategy: str = "in_process"


class PluginDescriptor(BaseModel):
    name: str
    module: str
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    tools: list[str] = Field(default_factory=list)
    execution_mode: PluginExecutionMode = PluginExecutionMode.TRUSTED_LOCAL
    isolation_strategy: str = "in_process"
    timeout_seconds: float = 10.0


class PluginReloadRequest(BaseModel):
    modules: list[str] = Field(default_factory=list)
