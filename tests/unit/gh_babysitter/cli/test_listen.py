"""Tests for the listen core."""

import json
from typing import cast
from unittest.mock import AsyncMock

import anyio.lowlevel
import httpx
import pytest
import typer

from gh_babysitter.cli import listen


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

    def raise_for_status(self):
        return None


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


class _FailingContext:
    async def __aenter__(self):
        raise httpx.ConnectError("disconnected")

    async def __aexit__(self, *args):
        return None  # noqa: ASYNC910 - Test double has no asynchronous cleanup.


class _ReadFailure(_Response):
    def aiter_lines(self):
        iterable = AsyncMock()
        iterable.__aiter__.return_value = _ReadFailureLines()
        return iterable


class _ReadFailureLines:
    def __init__(self):
        self.lines = iter(("event: ready", "data: {}", ""))

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.lines)
        except StopIteration as error:
            raise httpx.ReadError("disconnected") from error


class _SequenceClient(_Client):
    def __init__(self, *responses):
        self.responses = list(responses)
        self.params = None

    def stream(self, method, path, *, params):
        self.params = params
        return self.responses.pop(0)


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


async def test_listen_prints_pretty_events_and_handles_ready(monkeypatch, capsys):
    envelope = {
        "ts": "2026-07-20T12:00:00Z",
        "repo": "octo/repo",
        "event": "issues",
        "action": "closed",
        "number": 42,
        "payload": {},
    }
    response = _Response(
        "event: ready",
        "data: {}",
        "",
        f"data: {json.dumps(envelope)}",
        "",
    )
    client = _Client(response)
    monkeypatch.setattr(listen, "resolve_token", lambda: "token")

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues", count=1, format="pretty"),
        lambda **kwargs: cast("httpx.AsyncClient", client),
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.out == "2026-07-20T12:00:00Z octo/repo issues.closed #42\n"
    assert "subscribed" in output.err
    assert client.params == {"repo": "octo/repo", "events": "issues"}


async def test_listen_treats_server_auth_rejection_as_fatal(monkeypatch, capsys):
    monkeypatch.setattr(listen, "resolve_token", lambda: "token")

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues"),
        lambda **kwargs: cast("httpx.AsyncClient", _Client(_Response(status_code=403))),
    )

    assert result == 1
    assert "403" in capsys.readouterr().err


async def test_listen_stream_client_disables_read_timeout(monkeypatch):
    envelope = {
        "ts": "2026-07-20T12:00:00Z",
        "repo": "octo/repo",
        "event": "issues",
        "action": "opened",
        "number": 42,
        "payload": {},
    }
    calls = []
    monkeypatch.setattr(listen, "resolve_token", lambda: "token")

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues", count=1),
        lambda **kwargs: (
            calls.append(kwargs) or cast("httpx.AsyncClient", _Client(_Response(f"data: {json.dumps(envelope)}", "")))
        ),
    )

    timeout = calls[0]["timeout"]
    assert result == 0
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, None, 10, 10)


async def test_listen_github_client_keeps_finite_timeout(monkeypatch):
    calls = []

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    monkeypatch.setattr(listen, "satisfied_by_poll", AsyncMock(return_value=True))

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", number=42, until="closed"),
        lambda **kwargs: calls.append(kwargs) or cast("httpx.AsyncClient", _Client(_Response())),
    )

    timeout = calls[1]["timeout"]
    assert result == 0
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, 10, 10, 10)


async def test_listen_resets_backoff_after_a_successful_connection(monkeypatch):
    envelope = {
        "ts": "2026-07-20T12:00:00Z",
        "repo": "octo/repo",
        "event": "issues",
        "action": "opened",
        "number": 42,
        "payload": {},
    }
    client = _SequenceClient(
        _FailingContext(),
        _ReadFailure(),
        _Response(f"data: {json.dumps(envelope)}", ""),
    )
    sleeps = []

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    monkeypatch.setattr(
        listen.asyncio,
        "sleep",
        AsyncMock(side_effect=lambda delay: sleeps.append(delay) if delay else None),
    )
    monkeypatch.setattr(listen.random, "uniform", lambda low, high: 1.0)

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues", count=1),
        lambda **kwargs: cast("httpx.AsyncClient", client),
    )

    assert result == 0
    assert sleeps == [1.0, 1.0]
