"""CLI configuration."""

from functools import cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SERVER = "http://localhost:8000"


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
    github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GH_TOKEN", "GITHUB_TOKEN"),
    )


@cache
def get_settings() -> Settings:
    """Return the process-wide CLI settings."""
    return Settings()
