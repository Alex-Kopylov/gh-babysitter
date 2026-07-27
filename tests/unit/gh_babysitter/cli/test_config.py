"""Tests for CLI configuration."""

from importlib import import_module

import pytest
from pydantic import SecretStr, ValidationError


@pytest.fixture(autouse=True)
def _clear_settings_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in (
        "GH_BABYSITTER_GITHUB_API_URL",
        "GH_BABYSITTER_GITHUB_TIMEOUT",
        "GH_BABYSITTER_INSECURE",
        "GH_BABYSITTER_SERVER",
        "GH_BABYSITTER_SERVER_TIMEOUT",
        "GITHUB_API_URL",
        "GITHUB_TOKEN",
        "GH_HOST",
        "GH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def _config_module():
    return import_module("gh_babysitter.cli.config")


def test_settings_use_defaults():
    settings = _config_module().Settings(_env_file=None)

    assert settings.api_url == "https://api.github.com"
    assert settings.server == "http://localhost:8000"
    assert settings.github_token is None
    assert settings.insecure is False
    assert settings.server_timeout == 10
    assert settings.github_timeout == 10


def test_settings_allow_explicit_insecure_transport(monkeypatch):
    monkeypatch.setenv("GH_BABYSITTER_INSECURE", "1")

    assert _config_module().Settings(_env_file=None).insecure is True


def test_settings_read_timeouts_from_environment(monkeypatch):
    monkeypatch.setenv("GH_BABYSITTER_SERVER_TIMEOUT", "2.5")
    monkeypatch.setenv("GH_BABYSITTER_GITHUB_TIMEOUT", "30")

    settings = _config_module().Settings(_env_file=None)

    assert settings.server_timeout == 2.5
    assert settings.github_timeout == 30


def test_invalid_timeout_raises_validation_error(monkeypatch):
    monkeypatch.setenv("GH_BABYSITTER_SERVER_TIMEOUT", "soon")

    with pytest.raises(ValidationError, match="GH_BABYSITTER_SERVER_TIMEOUT"):
        _config_module().Settings(_env_file=None)


def test_settings_use_gh_babysitter_github_api_url(monkeypatch):
    monkeypatch.setenv(
        "GH_BABYSITTER_GITHUB_API_URL",
        "https://github.acme.com/api/v3",
    )

    assert _config_module().Settings(_env_file=None).api_url == "https://github.acme.com/api/v3"


def test_settings_use_github_api_url(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://github.acme.com/api/v3")

    assert _config_module().Settings(_env_file=None).api_url == "https://github.acme.com/api/v3"


def test_settings_prefer_gh_babysitter_github_api_url(monkeypatch):
    monkeypatch.setenv(
        "GH_BABYSITTER_GITHUB_API_URL",
        "https://primary.example/api/v3",
    )
    monkeypatch.setenv("GITHUB_API_URL", "https://fallback.example/api/v3")

    assert _config_module().Settings(_env_file=None).api_url == "https://primary.example/api/v3"


def test_settings_prefer_full_api_url_to_gh_host(monkeypatch):
    monkeypatch.setenv(
        "GH_BABYSITTER_GITHUB_API_URL",
        "https://primary.example/api/v3",
    )
    monkeypatch.setenv("GITHUB_API_URL", "https://fallback.example/api/v3")
    monkeypatch.setenv("GH_HOST", "github.acme.com")

    assert _config_module().Settings(_env_file=None).api_url == "https://primary.example/api/v3"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("github.acme.com", "https://github.acme.com/api/v3"),
        ("github.com", "https://api.github.com"),
        ("api.github.com", "https://api.github.com"),
        ("acme.ghe.com", "https://api.acme.ghe.com"),
        ("https://github.acme.com/", "https://github.acme.com/api/v3"),
        ("GITHUB.ACME.COM", "https://github.acme.com/api/v3"),
    ],
)
def test_settings_derive_api_url_from_gh_host(monkeypatch, host, expected):
    monkeypatch.setenv("GH_HOST", host)

    assert _config_module().Settings(_env_file=None).api_url == expected


def test_blank_github_api_url_falls_through_to_gh_host(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "")
    monkeypatch.setenv("GH_HOST", "github.acme.com")

    assert _config_module().Settings(_env_file=None).api_url == "https://github.acme.com/api/v3"


def test_settings_strip_trailing_slash_from_github_api_url(monkeypatch):
    monkeypatch.setenv(
        "GH_BABYSITTER_GITHUB_API_URL",
        "https://github.acme.com/api/v3/",
    )

    assert _config_module().Settings(_env_file=None).api_url == "https://github.acme.com/api/v3"


def test_settings_prefer_gh_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")

    assert _config_module().Settings(_env_file=None).github_token == SecretStr("gh-token")


def test_settings_fall_back_to_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")

    assert _config_module().Settings(_env_file=None).github_token == SecretStr("github-token")


def test_settings_read_shared_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GH_BABYSITTER_SERVER=https://babysitter.example\nGH_BABYSITTER_WEBHOOK_SECRET=server-only\n"
    )

    assert _config_module().Settings().server == "https://babysitter.example"


def test_get_settings_returns_cached_instance():
    get_settings = _config_module().get_settings
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
