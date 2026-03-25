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
    llm_provider: str | None = None
    llm_request_timeout_seconds: float = 60.0
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", validation_alias="OPENAI_BASE_URL")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-3-5-sonnet-latest", validation_alias="ANTHROPIC_MODEL")
    anthropic_base_url: str = Field(default="https://api.anthropic.com/v1", validation_alias="ANTHROPIC_BASE_URL")
    anthropic_api_version: str = Field(default="2023-06-01", validation_alias="ANTHROPIC_API_VERSION")
    local_model_endpoint: str = "http://127.0.0.1:11434/api/generate"
    local_model_name: str | None = "llama3.1"
    local_model_api_key: str | None = None

    model_config = SettingsConfigDict(env_prefix="SYNAPSE_", env_file=".env", extra="ignore")


settings = Settings()
settings = settings.model_copy(update={"agent_limits": load_agent_limits(settings.agent_limits_config_path)})
