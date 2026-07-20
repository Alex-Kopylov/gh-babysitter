"""Tests for server configuration."""

from dataclasses import FrozenInstanceError

import pytest

from gh_babysitter.server.config import Settings


def test_from_env_uses_defaults(monkeypatch):
    for name in (
        "WEBHOOK_SECRET",
        "GITHUB_API_URL",
        "AUTH_CACHE_TTL",
        "RECHECK_INTERVAL",
        "PING_INTERVAL",
        "QUEUE_MAXSIZE",
    ):
        monkeypatch.delenv(f"GH_BABYSITTER_{name}", raising=False)

    assert Settings.from_env() == Settings(webhook_secret=None)


def test_from_env_reads_all_settings(monkeypatch):
    values = {
        "WEBHOOK_SECRET": "secret",
        "GITHUB_API_URL": "https://github.example/api/",
        "AUTH_CACHE_TTL": "1",
        "RECHECK_INTERVAL": "2",
        "PING_INTERVAL": "3",
        "QUEUE_MAXSIZE": "4",
    }
    for name, value in values.items():
        monkeypatch.setenv(f"GH_BABYSITTER_{name}", value)

    assert Settings.from_env() == Settings(
        webhook_secret="secret",
        github_api_url="https://github.example/api/",
        auth_cache_ttl=1,
        recheck_interval=2,
        ping_interval=3,
        queue_maxsize=4,
    )


def test_settings_are_frozen():
    settings = Settings(webhook_secret=None)

    with pytest.raises(FrozenInstanceError):
        settings.queue_maxsize = 1
