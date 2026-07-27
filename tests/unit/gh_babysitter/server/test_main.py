"""Tests for the server process entry point."""

import pytest

from gh_babysitter.server import main as server_main


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    server_main.get_settings.cache_clear()
    yield
    server_main.get_settings.cache_clear()


def test_run_warns_when_webhook_secret_is_unset(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("GH_BABYSITTER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        server_main.uvicorn,
        "run",
        lambda app, **kwargs: calls.append((app, kwargs)),
    )

    server_main.run("127.0.0.1", 8000)

    assert "GH_BABYSITTER_WEBHOOK_SECRET is unset" in capsys.readouterr().err
    assert calls[0][1] == {
        "host": "127.0.0.1",
        "port": 8000,
        "timeout_graceful_shutdown": 5,
    }


def test_run_is_quiet_when_webhook_secret_is_set(monkeypatch, capsys):
    monkeypatch.setenv("GH_BABYSITTER_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *args, **kwargs: None)

    server_main.run("0.0.0.0", 9000)

    assert capsys.readouterr().err == ""
