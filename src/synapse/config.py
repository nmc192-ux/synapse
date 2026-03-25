from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

from synapse.models.agent import AgentExecutionLimits


def load_agent_limits(path: str) -> AgentExecutionLimits:
    payload = yaml.safe_load(Path(path).read_text())
    return AgentExecutionLimits.model_validate(payload["agent_limits"])


class Settings(BaseSettings):
    app_name: str = "Synapse"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    browser_headless: bool = True
    browser_channel: str | None = Field(default=None)
    postgres_dsn: str = "postgresql://postgres:postgres@localhost:5432/synapse"
    plugin_packages: list[str] = Field(default_factory=lambda: ["synapse.plugins"])
    plugin_modules: list[str] = Field(default_factory=list)
    agent_limits_config_path: str = "config/agent_limits.yaml"
    agent_limits: AgentExecutionLimits = Field(default_factory=AgentExecutionLimits)

    model_config = SettingsConfigDict(env_prefix="SYNAPSE_", env_file=".env", extra="ignore")


settings = Settings()
settings = settings.model_copy(update={"agent_limits": load_agent_limits(settings.agent_limits_config_path)})
