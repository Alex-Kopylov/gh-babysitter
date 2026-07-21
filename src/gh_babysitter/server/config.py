"""Server configuration."""

from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from ``GH_BABYSITTER_*`` variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="GH_BABYSITTER_",
        extra="ignore",
        frozen=True,
    )

    webhook_secret: str | None = None
    github_api_url: str = "https://api.github.com"
    auth_cache_ttl: int = 300
    recheck_interval: float = 300
    ping_interval: int = 30
    queue_maxsize: int = 256


@cache
def get_settings() -> Settings:
    """Return the process-wide server settings."""
    return Settings()
