"""Tests for GitHub token resolution."""

import subprocess

import pytest
import typer

from gh_babysitter.cli import token


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    token.get_settings.cache_clear()
    yield
    token.get_settings.cache_clear()


def test_resolve_token_prefers_gh_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("gh called"))

    assert token.resolve_token() == "gh-token"


def test_resolve_token_falls_back_to_github_token(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")

    assert token.resolve_token() == "github-token"


def test_resolve_token_uses_gh_auth_token(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="from-gh\n"),
    )

    assert token.resolve_token() == "from-gh"


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.CalledProcessError(1, ["gh"])])
def test_resolve_token_exits_with_login_hint(monkeypatch, capsys, failure):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(typer.Exit) as caught:
        token.resolve_token()

    assert caught.value.exit_code == 1
    assert "gh auth login" in capsys.readouterr().err
