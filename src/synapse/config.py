from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Synapse"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    browser_headless: bool = True
    browser_channel: str | None = Field(default=None)
    plugin_packages: list[str] = Field(default_factory=lambda: ["synapse.plugins"])
    plugin_modules: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(env_prefix="SYNAPSE_", env_file=".env", extra="ignore")


settings = Settings()
