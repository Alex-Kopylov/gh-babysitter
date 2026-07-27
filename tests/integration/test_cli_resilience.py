"""Integration-style tests for CLI HTTP resilience."""

from __future__ import annotations

import json
from functools import partial
from unittest.mock import AsyncMock

import anyio
import httpx2
import pytest

from gh_babysitter.cli import listen


def _success_stream() -> httpx2.Response:
    envelope = {
        "repo": "octo/repo",
        "event": "issues",
        "action": "opened",
        "number": 42,
    }
    content = f"event: ready\ndata: {{}}\n\ndata: {json.dumps(envelope)}\n\n"
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=content,
    )


def _client_factory(handler):
    return partial(
        httpx2.AsyncClient,
        transport=httpx2.MockTransport(handler),
    )


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_permanent_client_error_exits_once_with_server_detail(monkeypatch, capsys, status):
    requests = []
    detail = f"permanent error {status}"

    def handler(request):
        requests.append(request)
        if len(requests) > 1:
            raise AssertionError("permanent client error was retried")
        return httpx2.Response(status, json={"detail": detail})

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")

    with anyio.fail_after(1):
        result = await listen.listen(
            listen.ListenOptions(
                repo="octo/repo",
                events="issues",
                server="https://babysitter.example",
            ),
            _client_factory(handler),
        )

    assert result == 1
    assert len(requests) == 1
    assert detail in capsys.readouterr().err


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_rejection_exits_once_with_existing_message(monkeypatch, capsys, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(status, json={"detail": "ignored"})

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            server="https://babysitter.example",
        ),
        _client_factory(handler),
    )

    assert result == 1
    assert len(requests) == 1
    assert f"server rejected the GitHub token ({status})" in capsys.readouterr().err


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503, 507])
async def test_retryable_status_reconnects(monkeypatch, status):
    requests = []
    sleeps = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            headers = {"retry-after": "5"} if status == 503 else None
            return httpx2.Response(status, headers=headers)
        return _success_stream()

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    monkeypatch.setattr(
        listen.asyncio,
        "sleep",
        AsyncMock(side_effect=lambda delay: sleeps.append(delay) if delay else None),
    )
    monkeypatch.setattr(listen.random, "uniform", lambda low, high: 1.0)

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server="https://babysitter.example",
        ),
        _client_factory(handler),
    )

    assert result == 0
    assert len(requests) == 2
    assert sleeps == [1.0]


async def test_retryable_status_does_not_reset_backoff(monkeypatch):
    requests = []
    sleeps = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx2.Response(503)
        return _success_stream()

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    monkeypatch.setattr(
        listen.asyncio,
        "sleep",
        AsyncMock(side_effect=lambda delay: sleeps.append(delay) if delay else None),
    )
    monkeypatch.setattr(listen.random, "uniform", lambda low, high: 1.0)

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server="https://babysitter.example",
        ),
        _client_factory(handler),
    )

    assert result == 0
    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]


async def test_stream_without_ready_does_not_reset_backoff(monkeypatch):
    requests = []
    sleeps = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx2.Response(200, content=b"")
        return _success_stream()

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    monkeypatch.setattr(
        listen.asyncio,
        "sleep",
        AsyncMock(side_effect=lambda delay: sleeps.append(delay) if delay else None),
    )
    monkeypatch.setattr(listen.random, "uniform", lambda low, high: 1.0)

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server="https://babysitter.example",
        ),
        _client_factory(handler),
    )

    assert result == 0
    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]


async def test_each_retryable_status_prints_disconnect_warning(monkeypatch, capsys):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx2.Response(503)
        return _success_stream()

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    monkeypatch.setattr(listen.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(listen.random, "uniform", lambda low, high: 1.0)

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server="https://babysitter.example",
        ),
        _client_factory(handler),
    )

    stderr = capsys.readouterr().err
    assert result == 0
    assert stderr.count("warning: disconnected (server returned 503)") == 2
    assert "events during the gap are lost; reconnecting in 1.0s" in stderr
    assert "events during the gap are lost; reconnecting in 2.0s" in stderr
    assert "subscribed" in stderr


async def test_transport_error_prints_disconnect_warning(monkeypatch, capsys):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx2.ConnectError("connection refused")
        return _success_stream()

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    monkeypatch.setattr(listen.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(listen.random, "uniform", lambda low, high: 1.0)

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server="https://babysitter.example",
        ),
        _client_factory(handler),
    )

    assert result == 0
    assert (
        "warning: disconnected (connection refused); events during the gap are lost; reconnecting in 1.0s"
        in capsys.readouterr().err
    )


async def test_lag_event_warns_without_consuming_event_count(monkeypatch, capsys):
    envelope = {
        "repo": "octo/repo",
        "event": "issues",
        "action": "opened",
        "number": 42,
    }
    content = f'event: ready\ndata: {{}}\n\nevent: lag\ndata: {{"dropped":4}}\n\ndata: {json.dumps(envelope)}\n\n'

    def handler(request):
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=content,
        )

    monkeypatch.setattr(listen, "resolve_token", lambda: "token")

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            events="issues",
            count=1,
            server="https://babysitter.example",
        ),
        _client_factory(handler),
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.out == f"{json.dumps(envelope, separators=(',', ':'))}\n"
    assert "warning: server dropped 4 events (consumer too slow)" in output.err
