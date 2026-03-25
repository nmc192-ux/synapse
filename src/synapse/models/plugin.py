from pydantic import BaseModel, Field


class ToolDescriptor(BaseModel):
    name: str
    description: str = ""
    plugin: str | None = None


class PluginDescriptor(BaseModel):
    name: str
    module: str
    tools: list[str] = Field(default_factory=list)


class PluginReloadRequest(BaseModel):
    modules: list[str] = Field(default_factory=list)
