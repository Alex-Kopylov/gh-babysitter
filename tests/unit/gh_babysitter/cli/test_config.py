"""Tests for CLI configuration."""

from importlib import import_module

import pytest
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def _clear_settings_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("GH_BABYSITTER_SERVER", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def _config_module():
    return import_module("gh_babysitter.cli.config")


def test_settings_use_defaults():
    settings = _config_module().Settings(_env_file=None)

    assert settings.server == "http://localhost:8000"
    assert settings.github_token is None


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
