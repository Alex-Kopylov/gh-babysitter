"""Real-pipe tests for CLI JSONL output."""

from __future__ import annotations

import asyncio
import json
import selectors
import time
from typing import Any

import pytest

pytestmark = pytest.mark.system


def _event(number: int) -> bytes:
    envelope = {
        "repo": "octo/repo",
        "event": "issues",
        "action": "opened",
        "number": number,
    }
    return f"data: {json.dumps(envelope, separators=(',', ':'))}\n\n".encode()


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Serve health checks and two deliberately separated SSE events."""
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
    await send(
        {
            "type": "http.response.body",
            "body": b"event: ready\ndata: {}\n\n" + _event(1),
            "more_body": True,
        }
    )
    await asyncio.sleep(2)
    await send(
        {
            "type": "http.response.body",
            "body": _event(2),
            "more_body": False,
        }
    )


class TestJsonlPipe:
    """JSONL framing and flushing when stdout is a real pipe."""

    def test_each_frame_is_complete_before_stream_ends(
        self,
        uvicorn_server,
        popen_cli,
    ):
        """Expose the first JSON line while the second event is still pending."""
        port = uvicorn_server("tests.system.test_cli_pipe:app")
        process = popen_cli(
            "listen",
            "-R",
            "octo/repo",
            "-E",
            "issues",
            "--server",
            f"http://127.0.0.1:{port}",
            "--count",
            "2",
            "--timeout",
            "10s",
            env={"GH_TOKEN": "test-token"},
        )
        assert process.stdout is not None

        started = time.monotonic()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=1), "first JSONL frame was not flushed within one second"
            first = process.stdout.readline()
            first_elapsed = time.monotonic() - started
            assert selector.select(timeout=3), "second JSONL frame did not arrive"
            second = process.stdout.readline()

        _, stderr = process.communicate(timeout=5)

        assert process.returncode == 0, stderr
        assert first_elapsed < 1
        assert first.endswith("\n")
        assert second.endswith("\n")
        assert [json.loads(first), json.loads(second)] == [
            {
                "repo": "octo/repo",
                "event": "issues",
                "action": "opened",
                "number": 1,
            },
            {
                "repo": "octo/repo",
                "event": "issues",
                "action": "opened",
                "number": 2,
            },
        ]
