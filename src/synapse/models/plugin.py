from enum import Enum

from pydantic import BaseModel, Field


class PluginExecutionMode(str, Enum):
    TRUSTED_LOCAL = "trusted_local"
    ISOLATED_HOSTED = "isolated_hosted"


class PluginTrustLevel(str, Enum):
    TRUSTED_INTERNAL = "trusted_internal"
    TRUSTED_PARTNER = "trusted_partner"
    UNTRUSTED_EXTERNAL = "untrusted_external"


class ToolDescriptor(BaseModel):
    name: str
    description: str = ""
    plugin: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    execution_mode: PluginExecutionMode = PluginExecutionMode.TRUSTED_LOCAL
    isolation_strategy: str = "in_process"
    trust_level: PluginTrustLevel = PluginTrustLevel.TRUSTED_INTERNAL


class PluginDescriptor(BaseModel):
    name: str
    module: str
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    tools: list[str] = Field(default_factory=list)
    execution_mode: PluginExecutionMode = PluginExecutionMode.TRUSTED_LOCAL
    isolation_strategy: str = "in_process"
    timeout_seconds: float = 10.0
    trust_level: PluginTrustLevel = PluginTrustLevel.TRUSTED_INTERNAL


class PluginReloadRequest(BaseModel):
    modules: list[str] = Field(default_factory=list)
