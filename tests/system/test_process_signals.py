"""Real-process signal handling and reconnect tests."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from io import TextIOBase
from typing import Any

import pytest

pytestmark = pytest.mark.system


async def github_api_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Serve deterministic GitHub authentication responses and a health check."""
    path = scope["path"]
    if path == "/health":
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        return
    if path == "/user":
        body = json.dumps({"login": "octocat"}).encode()
        status = 200
    elif path == "/repos/octo/repo":
        body = json.dumps({"full_name": "octo/repo"}).encode()
        status = 200
    else:
        body = json.dumps({"detail": "not found"}).encode()
        status = 404
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _LineReader:
    """Read process lines without losing prefetched bytes between waits."""

    def __init__(self, stream: TextIOBase) -> None:
        self.stream = stream
        self.buffer = bytearray()

    def _pop_line(self) -> str | None:
        newline = self.buffer.find(b"\n")
        if newline < 0:
            return None
        raw = bytes(self.buffer[: newline + 1])
        del self.buffer[: newline + 1]
        return raw.decode()

    def read_until(self, expected: str, *, timeout: float) -> tuple[list[str], float]:
        """Return lines through the expected text before the deadline."""
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        with selectors.DefaultSelector() as selector:
            selector.register(self.stream.fileno(), selectors.EVENT_READ)
            while True:
                while (line := self._pop_line()) is not None:
                    lines.append(line)
                    if expected in line:
                        return lines, time.monotonic()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(timeout=remaining):
                    break
                chunk = os.read(self.stream.fileno(), 4_096)
                if not chunk:
                    break
                self.buffer.extend(chunk)
        pytest.fail(f"process output did not contain {expected!r} within {timeout:.1f}s; lines={lines!r}")

    def take_buffered_text(self) -> str:
        """Return and clear bytes already read beyond the last matched line."""
        value = bytes(self.buffer).decode()
        self.buffer.clear()
        return value


def _server_environment(github_api_port: int) -> dict[str, str]:
    return {
        "GH_BABYSITTER_WEBHOOK_SECRET": "secret",
        "GH_BABYSITTER_GITHUB_API_URL": f"http://127.0.0.1:{github_api_port}",
        "GH_BABYSITTER_PING_INTERVAL": "1",
        "GH_BABYSITTER_RECHECK_INTERVAL": "60",
    }


def _listener_environment() -> dict[str, str]:
    return {
        "GH_TOKEN": "test-token",
        "GH_BABYSITTER_SERVER_TIMEOUT": "1",
        "GH_BABYSITTER_STREAM_TIMEOUT": "3",
    }


def _deliver_webhook(port: int, number: int) -> dict[str, int]:
    body = json.dumps(
        {
            "repository": {"full_name": "octo/repo"},
            "action": "opened",
            "issue": {"number": number},
        }
    ).encode()
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request(
            "POST",
            "/webhook",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        )
        response = connection.getresponse()
        assert response.status == 202
        payload = json.loads(response.read())
    finally:
        connection.close()
    assert isinstance(payload, dict)
    return payload


def _start_listener(
    popen_cli: Callable[..., subprocess.Popen[str]],
    port: int,
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[str]:
    environment = _listener_environment()
    if env is not None:
        environment.update(env)
    return popen_cli(
        "listen",
        "-R",
        "octo/repo",
        "-E",
        "issues",
        "--server",
        f"http://127.0.0.1:{port}",
        "--timeout",
        "30s",
        env=environment,
    )


def _assert_cli_signal_stops_cleanly(
    signum: signal.Signals,
    *,
    github_api_port: int,
    server_port: int,
    babysitter_server: Callable[..., subprocess.Popen[str]],
    popen_cli: Callable[..., subprocess.Popen[str]],
) -> int:
    babysitter_server(server_port, env=_server_environment(github_api_port))
    listener = _start_listener(popen_cli, server_port)
    assert listener.stderr is not None
    stderr_reader = _LineReader(listener.stderr)
    initial_lines, _ = stderr_reader.read_until("subscribed", timeout=3)
    assert _deliver_webhook(server_port, 1)["matched"] == 1

    signaled_at = time.monotonic()
    listener.send_signal(signum)
    _, stderr = listener.communicate(timeout=3)

    assert time.monotonic() - signaled_at < 2
    stderr = stderr_reader.take_buffered_text() + stderr
    assert "Traceback" not in "".join(initial_lines) + stderr
    assert "RuntimeError" not in stderr
    assert "GeneratorExit" not in stderr
    assert listener.returncode is not None
    deadline = time.monotonic() + 1
    while (delivery := _deliver_webhook(server_port, 2))["matched"] != 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert delivery == {"matched": 0, "delivered": 0, "dropped": 0}
    return listener.returncode


def _assert_server_signal_reconnects_once(
    signum: signal.Signals,
    *,
    github_api_port: int,
    server_port: int,
    babysitter_server: Callable[..., subprocess.Popen[str]],
    popen_cli: Callable[..., subprocess.Popen[str]],
) -> None:
    environment = _server_environment(github_api_port)
    server = babysitter_server(server_port, env=environment)
    listener = _start_listener(popen_cli, server_port)
    assert listener.stderr is not None
    stderr_reader = _LineReader(listener.stderr)
    initial_lines, _ = stderr_reader.read_until("subscribed", timeout=3)

    server.send_signal(signum)
    server.communicate(timeout=7)
    died_at = time.monotonic()
    expected_returncode = 0 if signum is signal.SIGINT else -signal.SIGTERM
    assert server.returncode == expected_returncode

    babysitter_server(server_port, env=environment)
    warning_lines, warning_at = stderr_reader.read_until(
        "warning: disconnected",
        timeout=max(0.01, died_at + 1.5 - time.monotonic()),
    )
    subscribed_lines, subscribed_at = stderr_reader.read_until(
        "subscribed",
        timeout=max(0.01, died_at + 2.5 - time.monotonic()),
    )
    listener.terminate()
    stdout, stderr = listener.communicate(timeout=3)
    all_stderr = (
        "".join([*initial_lines, *warning_lines, *subscribed_lines]) + stderr_reader.take_buffered_text() + stderr
    )

    assert warning_at - died_at < 1.5
    assert subscribed_at - died_at < 2.5
    assert all_stderr.count("warning: disconnected") == 1
    assert all_stderr.count("subscribed") == 2
    assert stdout == ""


class TestCliSignals:
    """Direct signals sent to an active listener process."""

    def test_sigint_stops_listener_cleanly(
        self,
        free_port,
        uvicorn_server,
        babysitter_server,
        popen_cli,
    ):
        """Stop an active listener promptly with SIGINT."""
        github_api_port = uvicorn_server("tests.system.test_process_signals:github_api_app")
        returncode = _assert_cli_signal_stops_cleanly(
            signal.SIGINT,
            github_api_port=github_api_port,
            server_port=free_port,
            babysitter_server=babysitter_server,
            popen_cli=popen_cli,
        )

        assert returncode == 130

    def test_sigterm_stops_listener_cleanly(
        self,
        free_port,
        uvicorn_server,
        babysitter_server,
        popen_cli,
    ):
        """Stop an active listener promptly with SIGTERM."""
        github_api_port = uvicorn_server("tests.system.test_process_signals:github_api_app")
        returncode = _assert_cli_signal_stops_cleanly(
            signal.SIGTERM,
            github_api_port=github_api_port,
            server_port=free_port,
            babysitter_server=babysitter_server,
            popen_cli=popen_cli,
        )

        assert returncode == -signal.SIGTERM


class TestServerSignals:
    """Listener recovery after the owned server process exits."""

    def test_sigint_reconnects_listener_once(
        self,
        free_port,
        uvicorn_server,
        babysitter_server,
        popen_cli,
    ):
        """Detect SIGINT shutdown and resubscribe without duplicate warnings."""
        github_api_port = uvicorn_server("tests.system.test_process_signals:github_api_app")

        _assert_server_signal_reconnects_once(
            signal.SIGINT,
            github_api_port=github_api_port,
            server_port=free_port,
            babysitter_server=babysitter_server,
            popen_cli=popen_cli,
        )

    def test_sigterm_reconnects_listener_once(
        self,
        free_port,
        uvicorn_server,
        babysitter_server,
        popen_cli,
    ):
        """Detect SIGTERM shutdown and resubscribe without duplicate warnings."""
        github_api_port = uvicorn_server("tests.system.test_process_signals:github_api_app")

        _assert_server_signal_reconnects_once(
            signal.SIGTERM,
            github_api_port=github_api_port,
            server_port=free_port,
            babysitter_server=babysitter_server,
            popen_cli=popen_cli,
        )
