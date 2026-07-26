"""Tests for server configuration."""

import pytest
from pydantic import ValidationError

from gh_babysitter.server import config

_ENV_NAMES = (
    "WEBHOOK_SECRET",
    "GITHUB_API_URL",
    "AUTH_CACHE_TTL",
    "RECHECK_INTERVAL",
    "PING_INTERVAL",
    "QUEUE_MAXSIZE",
)


@pytest.fixture(autouse=True)
def _clear_settings_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in _ENV_NAMES:
        monkeypatch.delenv(f"GH_BABYSITTER_{name}", raising=False)
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)


def test_settings_use_defaults():
    assert config.Settings(_env_file=None).model_dump() == {
        "webhook_secret": None,
        "github_api_url": "https://api.github.com",
        "auth_cache_ttl": 300,
        "recheck_interval": 300,
        "ping_interval": 30,
        "queue_maxsize": 256,
    }


def test_settings_read_all_environment_values(monkeypatch):
    values = {
        "WEBHOOK_SECRET": "secret",
        "GITHUB_API_URL": "https://github.example/api/",
        "AUTH_CACHE_TTL": "1",
        "RECHECK_INTERVAL": "2.5",
        "PING_INTERVAL": "3",
        "QUEUE_MAXSIZE": "4",
    }
    for name, value in values.items():
        monkeypatch.setenv(f"GH_BABYSITTER_{name}", value)

    assert config.Settings(_env_file=None).model_dump() == {
        "webhook_secret": "secret",
        "github_api_url": "https://github.example/api",
        "auth_cache_ttl": 1,
        "recheck_interval": 2.5,
        "ping_interval": 3,
        "queue_maxsize": 4,
    }


def test_github_api_url_strips_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv(
        "GH_BABYSITTER_GITHUB_API_URL",
        "  https://github.example/api  ",
    )

    assert config.Settings(_env_file=None).github_api_url == "https://github.example/api"


def test_blank_github_api_url_uses_default(monkeypatch):
    monkeypatch.setenv("GH_BABYSITTER_GITHUB_API_URL", " / ")

    assert config.Settings(_env_file=None).github_api_url == "https://api.github.com"


@pytest.mark.parametrize("name", ["GITHUB_API_URL", "GH_HOST"])
def test_server_ignores_bare_github_environment_variables(monkeypatch, name):
    monkeypatch.setenv(name, "https://github.example/api/v3")

    assert config.Settings(_env_file=None).github_api_url == "https://api.github.com"


def test_settings_read_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GH_BABYSITTER_WEBHOOK_SECRET=dotenv-secret\n"
        "GH_BABYSITTER_QUEUE_MAXSIZE=12\n"
        "GH_BABYSITTER_SERVER=http://localhost:9000\n"
    )

    settings = config.Settings()

    assert settings.webhook_secret == "dotenv-secret"
    assert settings.queue_maxsize == 12


def test_process_environment_overrides_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GH_BABYSITTER_QUEUE_MAXSIZE=12\n")
    monkeypatch.setenv("GH_BABYSITTER_QUEUE_MAXSIZE", "24")

    assert config.Settings().queue_maxsize == 24


def test_invalid_environment_value_raises_validation_error(monkeypatch):
    monkeypatch.setenv("GH_BABYSITTER_AUTH_CACHE_TTL", "invalid")

    with pytest.raises(ValidationError, match="auth_cache_ttl"):
        config.Settings(_env_file=None)


def test_settings_are_frozen():
    settings = config.Settings(_env_file=None)

    with pytest.raises(ValidationError, match="Instance is frozen"):
        settings.queue_maxsize = 1


def test_get_settings_returns_cached_instance():
    config.get_settings.cache_clear()
    try:
        assert config.get_settings() is config.get_settings()
    finally:
        config.get_settings.cache_clear()
