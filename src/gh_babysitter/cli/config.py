"""CLI configuration."""

from functools import cache

from pydantic import AliasChoices, Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from gh_babysitter.server.config import DEFAULT_GITHUB_API_URL, OptionalGitHubApiUrl

DEFAULT_SERVER = "http://localhost:8000"


def _api_url_from_host(host: str | None) -> str | None:
    host = (host or "").strip().rstrip("/").rpartition("://")[2].lower()
    if not host:
        return None
    if host in {"github.com", "api.github.com"}:
        return DEFAULT_GITHUB_API_URL
    if host.endswith(".ghe.com"):
        return f"https://api.{host}"
    return f"https://{host}/api/v3"


class Settings(BaseSettings):
    """Runtime settings loaded from CLI environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_by_name=True,
    )

    server: str = Field(
        default=DEFAULT_SERVER,
        validation_alias="GH_BABYSITTER_SERVER",
    )
    github_api_url: OptionalGitHubApiUrl = Field(
        default=None,
        validation_alias=AliasChoices(
            "GH_BABYSITTER_GITHUB_API_URL",
            "GITHUB_API_URL",
        ),
    )
    gh_host: str | None = Field(default=None, validation_alias="GH_HOST")
    github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GH_TOKEN", "GITHUB_TOKEN"),
    )
    server_timeout: float = Field(
        default=10,
        validation_alias="GH_BABYSITTER_SERVER_TIMEOUT",
    )
    github_timeout: float = Field(
        default=10,
        validation_alias="GH_BABYSITTER_GITHUB_TIMEOUT",
    )

    @computed_field
    @property
    def api_url(self) -> str:
        """GitHub API base URL, derived from ``GH_HOST`` when not set directly."""
        return self.github_api_url or _api_url_from_host(self.gh_host) or DEFAULT_GITHUB_API_URL


@cache
def get_settings() -> Settings:
    """Return the process-wide CLI settings."""
    return Settings()
