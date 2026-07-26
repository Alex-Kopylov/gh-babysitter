"""Server configuration."""

from functools import cache
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_GITHUB_API_URL = "https://api.github.com"


def _normalize_api_url(value: str | None) -> str | None:
    return (value or "").strip().rstrip("/") or None


def _normalize_or_default(value: str | None) -> str:
    return _normalize_api_url(value) or DEFAULT_GITHUB_API_URL


GitHubApiUrl = Annotated[str, BeforeValidator(_normalize_or_default)]
OptionalGitHubApiUrl = Annotated[str | None, BeforeValidator(_normalize_api_url)]


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
    github_api_url: GitHubApiUrl = DEFAULT_GITHUB_API_URL
    auth_cache_ttl: int = 300
    recheck_interval: float = 300
    ping_interval: int = 30
    queue_maxsize: int = 256


@cache
def get_settings() -> Settings:
    """Return the process-wide server settings."""
    return Settings()
