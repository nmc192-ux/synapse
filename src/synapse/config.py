from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

from synapse.models.agent import AgentExecutionLimits
from synapse.models.plugin import PluginExecutionMode


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
    plugin_execution_mode: PluginExecutionMode = PluginExecutionMode.TRUSTED_LOCAL
    plugin_execution_timeout_seconds: float = 10.0
    hosted_plugin_isolation_backend: str = "auto"
    hosted_plugin_allow_untrusted_external: bool = False
    hosted_plugin_network_allowlist: list[str] = Field(default_factory=list)
    hosted_plugin_memory_limit_mb: int = 256
    hosted_plugin_cpu_limit_seconds: int = 2
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
    compression_provider: str = "noop"
    redis_url: str = "redis://localhost:6379/0"
    redis_required: bool = False
    runtime_state_fallback_memory: bool = True
    browser_worker_count: int = 1
    browser_worker_heartbeat_interval_seconds: float = 15.0
    browser_worker_queue_prefix: str = "synapse:browser:worker"
    scheduler_lease_timeout_seconds: float = 60.0
    scheduler_cleanup_interval_seconds: float = 15.0
    scheduler_max_assignment_retries: int = 3
    scheduler_retry_base_delay_seconds: float = 1.0
    a2a_identity_signing_key: str = "synapse-agent-identity"
    a2a_identity_signing_key_id: str = "default"
    a2a_identity_trusted_keys: dict[str, str] = Field(default_factory=dict)
    a2a_service_agent_allowlist: dict[str, list[str]] = Field(default_factory=dict)
    auth_required: bool = True
    jwt_secret: str = "synapse-dev-secret"
    jwt_issuer: str = "synapse"
    jwt_audience: str = "synapse-api"
    jwt_expiration_seconds: int = 3600
    hosted_plugin_partner_allowlist: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(env_prefix="SYNAPSE_", env_file=".env", extra="ignore")


settings = Settings()
settings = settings.model_copy(update={"agent_limits": load_agent_limits(settings.agent_limits_config_path)})
