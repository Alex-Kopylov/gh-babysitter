"""Tests for the server process entry point."""

from gh_babysitter.server import main as server_main


def test_run_warns_when_webhook_secret_is_unset(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("GH_BABYSITTER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        server_main.uvicorn,
        "run",
        lambda app, *, host, port: calls.append((app, host, port)),
    )

    server_main.run("127.0.0.1", 8000)

    assert "GH_BABYSITTER_WEBHOOK_SECRET is unset" in capsys.readouterr().err
    assert calls[0][1:] == ("127.0.0.1", 8000)


def test_run_is_quiet_when_webhook_secret_is_set(monkeypatch, capsys):
    monkeypatch.setenv("GH_BABYSITTER_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(server_main.uvicorn, "run", lambda *args, **kwargs: None)

    server_main.run("0.0.0.0", 9000)

    assert capsys.readouterr().err == ""
