"""Real-socket regression test for CLI stream teardown."""

from __future__ import annotations

import asyncio
import http.client
import os
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import gh_babysitter.cli.listen as listen_module

ROOT = Path(__file__).parents[2]
CLI = Path(sys.executable).with_name("gh-babysitter")
UVICORN = Path(sys.executable).with_name("uvicorn")
_READY = b"event: ready\ndata: {}\n\n"
_EVENT = b'data: {"repo":"octo/repo","event":"issues","action":"opened","number":42}\n\n'


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


def _wait_until_ready(server: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.poll() is not None:
            stdout, stderr = server.communicate()
            pytest.fail(f"uvicorn exited early ({server.returncode})\nstdout={stdout}\nstderr={stderr}")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
        try:
            connection.request("GET", "/health")
            if connection.getresponse().status == 204:
                return
        except OSError, http.client.HTTPException:
            time.sleep(0.02)
        finally:
            connection.close()
    pytest.fail("uvicorn did not become ready within 5 seconds")


def _stop_server(server: subprocess.Popen[str]) -> None:
    server.terminate()
    try:
        server.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.communicate(timeout=5)


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
        listen_module._install_httpcore2_shutdown_workaround(loop)  # ruff:ignore[private-member-access]
        loop.call_exception_handler(unrelated_runtime_error)
        loop.call_exception_handler(unrelated_asyncgen_failure)
    finally:
        loop.close()

    assert reported == [unrelated_runtime_error, unrelated_asyncgen_failure]


def test_count_success_closes_real_stream_without_teardown_traceback():
    environment = os.environ.copy()
    environment["GH_TOKEN"] = "test-token"
    environment["PYTHONPATH"] = str(ROOT)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = subprocess.Popen(
            [
                str(UVICORN),
                "tests.integration.test_cli_teardown:app",
                "--fd",
                str(listener.fileno()),
                "--lifespan",
                "off",
                "--log-level",
                "error",
            ],
            cwd=ROOT,
            env=environment,
            pass_fds=(listener.fileno(),),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    try:
        _wait_until_ready(server, port)
        for _ in range(5):
            result = subprocess.run(
                [
                    str(CLI),
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
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )

            assert result.returncode == 0, result.stderr
            assert "RuntimeError" not in result.stderr, result.stderr
            assert "GeneratorExit" not in result.stderr, result.stderr
    finally:
        _stop_server(server)
