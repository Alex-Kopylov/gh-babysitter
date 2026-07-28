"""Tests for the listen core."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio.lowlevel
import httpx2
import pytest
import typer

from gh_babysitter.cli import listen
from gh_babysitter.cli.config import Settings


class _Response:
    def __init__(self, *lines, status_code=200):
        self.lines = lines
        self.status_code = status_code

    async def __aenter__(self):
        await anyio.lowlevel.checkpoint()
        return self

    async def __aexit__(self, *args):
        return None  # noqa: ASYNC910 - Test double has no asynchronous cleanup.

    def aiter_lines(self):
        iterable = AsyncMock()
        iterable.__aiter__.return_value = iter(self.lines)
        return iterable


class _Client:
    def __init__(self, response):
        self.response = response
        self.params = None

    async def __aenter__(self):
        await anyio.lowlevel.checkpoint()
        return self

    async def __aexit__(self, *args):
        return None  # noqa: ASYNC910 - Test double has no asynchronous cleanup.

    def stream(self, method, path, *, params):
        self.params = params
        return self.response


def test_teardown_handler_reports_unrelated_exception_events():
    loop = asyncio.new_event_loop()
    reported: list[dict[str, Any]] = []

    def previous_handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        reported.append(context)

    unrelated_runtime_error = {
        "exception": RuntimeError("unrelated failure"),
        "asyncgen": SimpleNamespace(ag_code=SimpleNamespace(co_filename="/site-packages/httpcore2/_async/http11.py")),
    }
    unrelated_asyncgen_failure = {
        "exception": RuntimeError("generator didn't stop after athrow()"),
        "asyncgen": SimpleNamespace(ag_code=SimpleNamespace(co_filename=__file__)),
    }

    loop.set_exception_handler(previous_handler)
    try:
        listen._install_httpcore2_shutdown_workaround(loop)  # ruff:ignore[private-member-access]
        loop.call_exception_handler(unrelated_runtime_error)
        loop.call_exception_handler(unrelated_asyncgen_failure)
    finally:
        loop.close()

    assert reported == [unrelated_runtime_error, unrelated_asyncgen_failure]


@pytest.mark.parametrize(
    "opts",
    [
        listen.ListenOptions(repo="octo/repo"),
        listen.ListenOptions(repo="octo/repo", until="merged"),
        listen.ListenOptions(repo="octo/repo", events="issues", count=1, first_event=True),
    ],
)
async def test_listen_rejects_invalid_exit_options(opts):
    with pytest.raises(typer.BadParameter):
        await listen.listen(opts, lambda **kwargs: pytest.fail("client created"))


async def test_listen_prints_pretty_events_and_handles_ready(capsys, fake_token, envelope):
    event = envelope(action="closed")
    response = _Response(
        "event: ready",
        "data: {}",
        "",
        f"data: {json.dumps(event)}",
        "",
    )
    client = _Client(response)

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues", count=1, format="pretty"),
        lambda **kwargs: cast("httpx2.AsyncClient", client),
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.out == "2026-07-20T12:00:00Z octo/repo issues.closed #42\n"
    assert "subscribed" in output.err
    assert client.params == {"repo": "octo/repo", "events": "issues"}


async def test_listen_with_number_and_action_sends_query_parameters(fake_token, envelope):
    event = envelope(action="closed")
    client = _Client(_Response(f"data: {json.dumps(event)}", ""))

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            number=42,
            action="closed",
            count=1,
        ),
        lambda **kwargs: cast("httpx2.AsyncClient", client),
    )

    assert result == 0
    assert client.params == {
        "repo": "octo/repo",
        "events": "issues",
        "number": 42,
        "action": "closed",
    }


async def test_listen_refuses_plain_http_to_non_loopback_before_resolving_token(monkeypatch):
    monkeypatch.setattr(listen, "resolve_token", lambda: pytest.fail("token resolved"))

    with pytest.raises(
        typer.BadParameter,
        match="refusing to send a GitHub token over plain HTTP to babysitter.example",
    ):
        await listen.listen(
            listen.ListenOptions(
                repo="octo/repo",
                events="issues",
                server="http://babysitter.example:8000",
            ),
        )


@pytest.mark.parametrize(
    "server",
    [
        "http://localhost:8000",
        "http://127.42.0.1:8000",
        "http://[::1]:8000",
        "https://babysitter.example",
    ],
)
async def test_listen_allows_secure_or_loopback_server(server, fake_token, envelope):
    event = envelope()
    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server=server,
        ),
        lambda **kwargs: cast(
            "httpx2.AsyncClient",
            _Client(_Response(f"data: {json.dumps(event)}", "")),
        ),
    )

    assert result == 0


async def test_listen_allows_explicit_insecure_server(monkeypatch, fake_token, envelope):
    event = envelope()
    monkeypatch.setattr(
        listen,
        "get_settings",
        lambda: Settings(_env_file=None, insecure=True),
    )

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server="http://babysitter.example:8000",
        ),
        lambda **kwargs: cast(
            "httpx2.AsyncClient",
            _Client(_Response(f"data: {json.dumps(event)}", "")),
        ),
    )

    assert result == 0


async def test_listen_stream_client_uses_bounded_read_timeout(fake_token, envelope):
    event = envelope()
    calls = []

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues", count=1),
        lambda **kwargs: (
            calls.append(kwargs) or cast("httpx2.AsyncClient", _Client(_Response(f"data: {json.dumps(event)}", "")))
        ),
    )

    timeout = calls[0]["timeout"]
    assert result == 0
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, 90, 10, 10)


async def test_listen_github_client_keeps_finite_timeout(monkeypatch, fake_token):
    calls = []
    api_url = "https://github.acme.com/api/v3"

    monkeypatch.setattr(listen, "satisfied_by_poll", AsyncMock(return_value=True))

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            number=42,
            until="closed",
            api_url=api_url,
        ),
        lambda **kwargs: calls.append(kwargs) or cast("httpx2.AsyncClient", _Client(_Response())),
    )

    timeout = calls[1]["timeout"]
    assert result == 0
    assert calls[1]["base_url"] == api_url
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, 10, 10, 10)


async def test_listen_clients_use_configured_timeouts(monkeypatch, fake_token):
    calls = []

    monkeypatch.setattr(listen, "satisfied_by_poll", AsyncMock(return_value=True))
    monkeypatch.setattr(
        listen,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            server_timeout=2.5,
            stream_timeout=75,
            github_timeout=30,
        ),
    )

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", number=42, until="closed"),
        lambda **kwargs: calls.append(kwargs) or cast("httpx2.AsyncClient", _Client(_Response())),
    )

    server_timeout, github_timeout = (call["timeout"] for call in calls)
    assert result == 0
    assert (server_timeout.connect, server_timeout.read, server_timeout.write, server_timeout.pool) == (
        2.5,
        75,
        2.5,
        2.5,
    )
    assert (github_timeout.connect, github_timeout.read, github_timeout.write, github_timeout.pool) == (
        30,
        30,
        30,
        30,
    )
