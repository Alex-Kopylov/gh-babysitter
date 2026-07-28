"""Real-socket regression test for CLI stream teardown."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

_READY = b"event: ready\ndata: {}\n\n"
_EVENT = b'data: {"repo":"octo/repo","event":"issues","action":"opened","number":42}\n\n'

pytestmark = pytest.mark.system


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Serve a health check or a continuously flowing SSE stream."""
    if scope["path"] == "/health":
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        }
    )
    await send({"type": "http.response.body", "body": _READY, "more_body": True})
    # The client must abandon a response that is still actively streaming, which
    # is what a live SSE feed looks like. Delivering the whole body in one chunk
    # does not reproduce the httpcore2 teardown bug, so keep the feed flowing
    # until the client goes away.
    with suppress(Exception):
        for _ in range(500):
            await send({"type": "http.response.body", "body": _EVENT, "more_body": True})
            await asyncio.sleep(0.01)


def test_count_success_closes_real_stream_without_teardown_traceback(
    uvicorn_server,
    run_cli,
):
    port = uvicorn_server("tests.system.test_cli_teardown:app")

    for _ in range(5):
        result = run_cli(
            "listen",
            "-R",
            "octo/repo",
            "-E",
            "issues",
            "--server",
            f"http://127.0.0.1:{port}",
            "--count",
            "1",
            "--timeout",
            "5s",
            env={"GH_TOKEN": "test-token"},
        )

        assert result.returncode == 0, result.stderr
        assert "RuntimeError" not in result.stderr, result.stderr
        assert "GeneratorExit" not in result.stderr, result.stderr
