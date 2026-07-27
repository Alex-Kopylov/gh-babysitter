"""Fixtures for tests that use real sockets and subprocesses."""

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from http import HTTPStatus
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CLI = Path(sys.executable).with_name("gh-babysitter")
UVICORN = Path(sys.executable).with_name("uvicorn")


def _wait_until_ready(server: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.poll() is not None:
            stdout, stderr = server.communicate()
            pytest.fail(f"uvicorn exited early ({server.returncode})\nstdout={stdout}\nstderr={stderr}")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
        try:
            connection.request("GET", "/health")
            if connection.getresponse().status == HTTPStatus.NO_CONTENT:
                return
        except OSError, http.client.HTTPException:
            time.sleep(0.02)
        finally:
            connection.close()
    pytest.fail("uvicorn did not become ready within 5 seconds")


def _wait_until_serving(server: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.poll() is not None:
            stdout, stderr = server.communicate()
            pytest.fail(f"server exited early ({server.returncode})\nstdout={stdout}\nstderr={stderr}")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
        try:
            connection.request("GET", "/events/stream?repo=octo%2Frepo&events=issues")
            if connection.getresponse().status == HTTPStatus.UNAUTHORIZED:
                return
        except OSError, http.client.HTTPException:
            time.sleep(0.02)
        finally:
            connection.close()
    pytest.fail("gh-babysitter server did not become ready within 5 seconds")


def _stop_server(server: subprocess.Popen[str]) -> None:
    server.terminate()
    try:
        server.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.communicate(timeout=5)


@pytest.fixture
def free_port() -> int:
    """Return a currently unbound loopback TCP port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def uvicorn_server() -> Iterator[Callable[[str], int]]:
    """Start uvicorn for an import target and clean it up after the test."""
    servers: list[subprocess.Popen[str]] = []

    def start(app_target: str) -> int:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = subprocess.Popen(
                [
                    str(UVICORN),
                    app_target,
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
        servers.append(server)
        _wait_until_ready(server, port)
        return port

    yield start

    for server in reversed(servers):
        _stop_server(server)


@pytest.fixture
def run_cli() -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run the installed CLI with deterministic subprocess defaults."""

    def run(
        *args: str,
        env: Mapping[str, str] | None = None,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        if env is not None:
            environment.update(env)
        return subprocess.run(
            [str(CLI), *args],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    return run


@pytest.fixture
def popen_cli() -> Iterator[Callable[..., subprocess.Popen[str]]]:
    """Start installed CLI processes with piped output and clean them up."""
    processes: list[subprocess.Popen[str]] = []

    def start(
        *args: str,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        if env is not None:
            environment.update(env)
        process = subprocess.Popen(
            [str(CLI), *args],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        return process

    yield start

    for process in reversed(processes):
        _stop_server(process)


@pytest.fixture
def babysitter_server() -> Iterator[Callable[..., subprocess.Popen[str]]]:
    """Start the installed server executable directly and clean it up."""
    servers: list[subprocess.Popen[str]] = []

    def start(
        port: int,
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        if env is not None:
            environment.update(env)
        server = subprocess.Popen(
            [
                str(CLI),
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        servers.append(server)
        _wait_until_serving(server, port)
        return server

    yield start

    for server in reversed(servers):
        _stop_server(server)
