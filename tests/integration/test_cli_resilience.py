"""Integration-style tests for CLI HTTP resilience."""

from __future__ import annotations

import json
from functools import partial

import anyio
import httpx2
import pytest

from gh_babysitter.cli import listen


@pytest.fixture
def success_stream(envelope, sse_body) -> httpx2.Response:
    content = sse_body(
        "event: ready\ndata: {}",
        f"data: {json.dumps(envelope())}",
    )
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
async def test_permanent_client_error_exits_once_with_server_detail(capsys, status, fake_token):
    requests = []
    detail = f"permanent error {status}"

    def handler(request):
        requests.append(request)
        if len(requests) > 1:
            raise AssertionError("permanent client error was retried")
        return httpx2.Response(status, json={"detail": detail})

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
async def test_auth_rejection_exits_once_with_existing_message(capsys, status, fake_token):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(status, json={"detail": "ignored"})

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
async def test_retryable_status_reconnects(
    status,
    fake_token,
    deterministic_backoff,
    success_stream,
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            headers = {"retry-after": "5"} if status == 503 else None
            return httpx2.Response(status, headers=headers)
        return success_stream

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
    assert deterministic_backoff == [1.0]


async def test_retryable_status_does_not_reset_backoff(
    fake_token,
    deterministic_backoff,
    success_stream,
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx2.Response(503)
        return success_stream

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
    assert deterministic_backoff == [1.0, 2.0]


async def test_stream_without_ready_does_not_reset_backoff(
    fake_token,
    deterministic_backoff,
    success_stream,
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx2.Response(200, content=b"")
        return success_stream

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
    assert deterministic_backoff == [1.0, 2.0]


async def test_each_retryable_status_prints_disconnect_warning(
    capsys,
    fake_token,
    deterministic_backoff,
    success_stream,
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx2.Response(503)
        return success_stream

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


async def test_transport_error_prints_disconnect_warning(
    capsys,
    fake_token,
    deterministic_backoff,
    success_stream,
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx2.ConnectError("connection refused")
        return success_stream

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


async def test_lag_event_warns_without_consuming_event_count(
    capsys,
    fake_token,
    envelope,
    sse_body,
):
    event = envelope()
    content = sse_body(
        "event: ready\ndata: {}",
        'event: lag\ndata: {"dropped":4}',
        f"data: {json.dumps(event)}",
    )

    def handler(request):
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=content,
        )

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
    assert output.out == f"{json.dumps(event, separators=(',', ':'))}\n"
    assert "warning: server dropped 4 events (consumer too slow)" in output.err
