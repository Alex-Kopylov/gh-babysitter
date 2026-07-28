"""Tests for Typer command wiring and the gh extension entry point."""

import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

import gh_babysitter
from gh_babysitter.cli import listen as listen_core
from gh_babysitter.cli import main
from tests.conftest import cli_text

runner = CliRunner()
ROOT = Path(__file__).parents[4]


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    main.get_settings.cache_clear()
    yield
    main.get_settings.cache_clear()


def test_app_exposes_all_commands():
    result = runner.invoke(main.app, ["--help"])

    assert result.exit_code == 0
    assert all(command in result.stdout for command in ("listen", "setup", "serve"))


def test_version_flag_prefers_package_version(monkeypatch):
    monkeypatch.setattr(gh_babysitter, "__version__", "1.0.0", raising=False)

    result = runner.invoke(main.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "1.0.0\n"


def test_version_flag_falls_back_to_distribution_metadata(monkeypatch):
    monkeypatch.delattr(gh_babysitter, "__version__", raising=False)

    result = runner.invoke(main.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == f"{distribution_version('gh_babysitter')}\n"


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
            "--api-url",
            "https://github.acme.com/api/v3",
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
            api_url="https://github.acme.com/api/v3",
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


def test_listen_command_reads_api_url_from_environment(monkeypatch):
    captured = []
    monkeypatch.setattr(main, "listen", AsyncMock(side_effect=lambda options: captured.append(options) or 0))

    result = runner.invoke(
        main.app,
        ["listen", "-R", "octo/repo", "-E", "issues"],
        env={
            "GH_BABYSITTER_GITHUB_API_URL": "https://github.acme.com/api/v3",
        },
    )

    assert result.exit_code == 0
    assert captured[0].api_url == "https://github.acme.com/api/v3"


def test_listen_command_reports_invalid_duration_as_usage_error():
    result = runner.invoke(
        main.app,
        ["listen", "-R", "octo/repo", "-E", "issues", "--timeout", "later"],
    )

    assert result.exit_code == 2
    assert "invalid duration" in cli_text(result.output)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["-R", "not-a-repo", "-E", "issues"], "--repo must be owner/name"),
        (["-R", "owner/repo/extra", "-E", "issues"], "--repo must be owner/name"),
        (["-R", "/repo", "-E", "issues"], "--repo must be owner/name"),
        (["-R", "owner/", "-E", "issues"], "--repo must be owner/name"),
        (["-R", "owner/repo!", "-E", "issues"], "--repo must be owner/name"),
        (["-R", "owner/repo", "-E", "issues", "-n", "0"], "--number must be at least 1"),
        (["-R", "owner/repo", "-E", "issues", "-n", "-1"], "--number must be at least 1"),
        (["-R", "owner/repo", "-E", "issues", "--action", ""], "--action must not be empty"),
        (
            ["-R", "owner/repo", "-E", "issues", "--server", "babysitter.example"],
            "--server must be an http or https URL with a host",
        ),
        (
            ["-R", "owner/repo", "-E", "issues", "--server", "ftp://babysitter.example"],
            "--server must be an http or https URL with a host",
        ),
        (
            ["-R", "owner/repo", "-E", "issues", "--server", "http:///events"],
            "--server must be an http or https URL with a host",
        ),
    ],
)
def test_listen_command_rejects_invalid_options_before_creating_client(monkeypatch, args, message):
    async def invoke(options):
        return await listen_core.listen(
            options,
            client_factory=lambda **kwargs: pytest.fail("client created"),
        )

    monkeypatch.setattr(main, "listen", invoke)
    monkeypatch.setattr(listen_core, "resolve_token", lambda: pytest.fail("token resolved"))

    result = runner.invoke(main.app, ["listen", *args])

    assert result.exit_code == 2
    assert message in cli_text(result.output)


@pytest.mark.parametrize("value", ["0", "0s"])
def test_listen_command_rejects_non_positive_timeout_before_running_core(monkeypatch, value):
    core = AsyncMock()
    monkeypatch.setattr(main, "listen", core)

    result = runner.invoke(
        main.app,
        ["listen", "-R", "owner/repo", "-E", "issues", "--timeout", value],
    )

    assert result.exit_code == 2
    assert "--timeout must be greater than zero" in cli_text(result.output)
    core.assert_not_awaited()


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
            "--api-url",
            "https://github.acme.com/api/v3",
        ],
    )
    serve_result = runner.invoke(main.app, ["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert setup_result.exit_code == 0
    assert setup_calls == [
        {
            "org": "acme",
            "url": "https://hooks.example/webhook",
            "events": "issues,release",
            "secret": None,
            "api_url": "https://github.acme.com/api/v3",
        }
    ]
    assert serve_result.exit_code == 0
    assert serve_calls == [("0.0.0.0", 9000)]


def test_setup_command_prefers_stdin_secret_to_environment(monkeypatch):
    setup_webhook = AsyncMock()
    monkeypatch.setattr(main, "setup_webhook", setup_webhook)

    result = runner.invoke(
        main.app,
        [
            "setup",
            "--org",
            "acme",
            "--url",
            "https://hooks.example/webhook",
            "--secret-stdin",
        ],
        input="stdin-secret\n",
        env={"GH_BABYSITTER_WEBHOOK_SECRET": "environment-secret"},
    )

    assert result.exit_code == 0
    assert setup_webhook.await_args.kwargs["secret"] == "stdin-secret"


def test_setup_command_uses_environment_secret(monkeypatch):
    setup_webhook = AsyncMock()
    monkeypatch.setattr(main, "setup_webhook", setup_webhook)

    result = runner.invoke(
        main.app,
        [
            "setup",
            "--org",
            "acme",
            "--url",
            "https://hooks.example/webhook",
        ],
        env={"GH_BABYSITTER_WEBHOOK_SECRET": "environment-secret"},
    )

    assert result.exit_code == 0
    assert setup_webhook.await_args.kwargs["secret"] == "environment-secret"


def test_setup_command_rejects_empty_secret_stdin(monkeypatch):
    setup_webhook = AsyncMock()
    monkeypatch.setattr(main, "setup_webhook", setup_webhook)

    result = runner.invoke(
        main.app,
        [
            "setup",
            "--org",
            "acme",
            "--url",
            "https://hooks.example/webhook",
            "--secret-stdin",
        ],
        input=" \n",
    )

    assert result.exit_code == 2
    assert "stdin secret must not be empty" in cli_text(result.output)
    setup_webhook.assert_not_awaited()


def test_setup_command_rejects_removed_secret_option(monkeypatch):
    setup_webhook = AsyncMock()
    monkeypatch.setattr(main, "setup_webhook", setup_webhook)

    result = runner.invoke(
        main.app,
        [
            "setup",
            "--org",
            "acme",
            "--url",
            "https://hooks.example/webhook",
            "--secret",
            "exposed-secret",
        ],
    )

    assert result.exit_code == 2
    assert "No such option: --secret" in cli_text(result.output)
    setup_webhook.assert_not_awaited()


def test_package_and_extension_entrypoints_are_configured():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    script = ROOT / "gh-babysitter"

    assert any(dependency.startswith("typer") for dependency in config["project"]["dependencies"])
    assert config["project"]["scripts"] == {"gh-babysitter": "gh_babysitter.cli.main:main"}
    assert script.stat().st_mode & 0o111
    assert 'exec uv run --directory "$extension_dir" gh-babysitter "$@"' in script.read_text()
    assert "command -v uv" in script.read_text()
