"""Tests for Typer command wiring and the gh extension entry point."""

import tomllib
from pathlib import Path
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from gh_babysitter.cli import main

runner = CliRunner()
ROOT = Path(__file__).parents[4]


def test_app_exposes_all_commands():
    result = runner.invoke(main.app, ["--help"])

    assert result.exit_code == 0
    assert all(command in result.stdout for command in ("listen", "setup", "serve"))


def test_listen_command_maps_flags_and_returns_core_exit_code(monkeypatch):
    captured = []
    monkeypatch.setattr(main, "listen", AsyncMock(side_effect=lambda options: captured.append(options) or 124))

    result = runner.invoke(
        main.app,
        [
            "listen",
            "-R",
            "octo/repo",
            "-E",
            "issues,release",
            "-n",
            "42",
            "--action",
            "closed",
            "--until",
            "closed",
            "--timeout",
            "1h30m",
            "--count",
            "2",
            "--server",
            "http://server",
            "--format",
            "pretty",
        ],
    )

    assert result.exit_code == 124
    assert captured == [
        main.ListenOptions(
            repo="octo/repo",
            events="issues,release",
            number=42,
            action="closed",
            until="closed",
            timeout=5_400,
            count=2,
            server="http://server",
            format="pretty",
        )
    ]


def test_listen_command_reads_server_from_environment(monkeypatch):
    captured = []
    monkeypatch.setattr(main, "listen", AsyncMock(side_effect=lambda options: captured.append(options) or 0))

    result = runner.invoke(
        main.app,
        ["listen", "-R", "octo/repo", "-E", "issues", "--first-event"],
        env={"GH_BABYSITTER_SERVER": "https://babysitter.example"},
    )

    assert result.exit_code == 0
    assert captured[0].server == "https://babysitter.example"
    assert captured[0].first_event is True


def test_listen_command_reports_invalid_duration_as_usage_error():
    result = runner.invoke(
        main.app,
        ["listen", "-R", "octo/repo", "-E", "issues", "--timeout", "later"],
    )

    assert result.exit_code == 2
    assert "invalid duration" in result.output


def test_setup_and_serve_commands_delegate(monkeypatch):
    setup_calls = []
    serve_calls = []
    monkeypatch.setattr(main, "setup_webhook", AsyncMock(side_effect=lambda **kwargs: setup_calls.append(kwargs)))
    monkeypatch.setattr(main, "run_server", lambda host, port: serve_calls.append((host, port)))

    setup_result = runner.invoke(
        main.app,
        [
            "setup",
            "--org",
            "acme",
            "--url",
            "https://hooks.example/webhook",
            "--events",
            "issues,release",
            "--secret",
            "secret",
        ],
    )
    serve_result = runner.invoke(main.app, ["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert setup_result.exit_code == 0
    assert setup_calls == [
        {
            "org": "acme",
            "url": "https://hooks.example/webhook",
            "events": "issues,release",
            "secret": "secret",
        }
    ]
    assert serve_result.exit_code == 0
    assert serve_calls == [("0.0.0.0", 9000)]


def test_package_and_extension_entrypoints_are_configured():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    script = ROOT / "gh-babysitter"

    assert any(dependency.startswith("typer") for dependency in config["project"]["dependencies"])
    assert config["project"]["scripts"] == {"gh-babysitter": "gh_babysitter.cli.main:main"}
    assert script.stat().st_mode & 0o111
    assert 'exec uv run --directory "$extension_dir" gh-babysitter "$@"' in script.read_text()
    assert "command -v uv" in script.read_text()
