"""Fixtures for real-app ASGI integration tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any

import anyio
import anyio.lowlevel
import httpx2
import pytest
from fastapi import FastAPI

from gh_babysitter.server.app import create_app
from gh_babysitter.server.auth import Access, Authenticator, Verdict
from gh_babysitter.server.config import Settings
from gh_babysitter.server.registry import Registry


class FakeAuthenticator:
    """Controllable superset of the integration-test authenticators."""

    def __init__(self) -> None:
        """Create an allowed authenticator with no queued rechecks."""
        self.revoked = False
        self.access = Access(Verdict.ALLOWED, "octocat")
        self.rechecks: list[Access] = []
        self.calls: list[tuple[str, str, bool]] = []

    async def verify(self, token: str, repo: str, *, fresh: bool = False) -> Access:
        """Return configured access while recording authorization checks."""
        await anyio.lowlevel.checkpoint()
        self.calls.append((token, repo, fresh))
        if token != "token" or repo != "octo/repo" or self.revoked:
            return Access(Verdict.DENIED)
        if fresh and self.rechecks:
            return self.rechecks.pop(0)
        return self.access


@pytest.fixture
def fake_authenticator() -> FakeAuthenticator:
    """Return an allowed, controllable authenticator."""
    return FakeAuthenticator()


@pytest.fixture
def server_settings() -> Callable[..., Settings]:
    """Build deterministic server settings with optional overrides."""

    def build(**overrides: Any) -> Settings:
        values = {
            "webhook_secret": "secret",
            "recheck_interval": 0.02,
            "ping_interval": 60,
            "queue_maxsize": 1,
        }
        values.update(overrides)
        return Settings(**values)

    return build


@pytest.fixture
def make_app(server_settings: Callable[..., Settings]) -> Callable[..., FastAPI]:
    """Build the real FastAPI app with injected test dependencies."""

    def build(
        *,
        registry: Registry | None = None,
        authenticator: Authenticator | None = None,
        settings: Settings | None = None,
        **settings_overrides: Any,
    ) -> FastAPI:
        configured = settings or server_settings(**settings_overrides)
        return create_app(configured, registry=registry, authenticator=authenticator)

    return build


@pytest.fixture
def make_client(make_app: Callable[..., FastAPI]) -> Callable[..., httpx2.AsyncClient]:
    """Build an ASGI client against the real application."""

    def build(**app_options: Any) -> httpx2.AsyncClient:
        app = make_app(**app_options)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
        )

    return build


@pytest.fixture
def client_factory() -> Callable[..., Callable[..., httpx2.AsyncClient]]:
    """Build the CLI client factory for ASGI server and mocked GitHub calls."""

    def build(
        app: FastAPI,
        github_handler: Callable[[httpx2.Request], httpx2.Response] | None = None,
    ) -> Callable[..., httpx2.AsyncClient]:
        def make(*, base_url: str, headers: dict[str, str], **kwargs: Any) -> httpx2.AsyncClient:
            if base_url == "http://server":
                transport = httpx2.ASGITransport(app=app)
            else:
                transport = httpx2.MockTransport(github_handler) if github_handler else None
            return httpx2.AsyncClient(
                transport=transport,
                base_url=base_url,
                headers=headers,
                **kwargs,
            )

        return make

    return build


@pytest.fixture
def sign() -> Callable[[bytes], str]:
    """Sign a webhook body with the integration-test secret."""

    def build(body: bytes) -> str:
        return hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    return build


@pytest.fixture
def webhook_headers(sign: Callable[[bytes], str]) -> Callable[..., dict[str, str]]:
    """Build GitHub webhook headers for a body and event."""

    def build(body: bytes, event: str = "issues", *, valid: bool = True) -> dict[str, str]:
        digest = sign(body) if valid else "0" * 64
        return {
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": f"sha256={digest}",
        }

    return build


@pytest.fixture
def deliver(
    webhook_headers: Callable[..., dict[str, str]],
) -> Callable[[FastAPI, str, dict[str, Any]], Awaitable[httpx2.Response]]:
    """Return a helper that delivers one signed webhook through ASGI."""

    async def send(app: FastAPI, event: str, payload: dict[str, Any]) -> httpx2.Response:
        body = json.dumps(payload).encode()
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://server",
        ) as client:
            response = await client.post(
                "/webhook",
                content=body,
                headers=webhook_headers(body, event=event),
            )
        assert response.status_code == HTTPStatus.ACCEPTED
        return response

    return send


@pytest.fixture
def wait_until_registered() -> Callable[[Registry], Awaitable[None]]:
    """Return a helper that waits for an SSE connection to register."""

    async def wait(registry: Registry) -> None:
        with anyio.fail_after(1):
            await anyio.lowlevel.checkpoint()
            while not registry.connections:  # noqa: ASYNC110 - Registry has no notification hook.
                await anyio.lowlevel.checkpoint()

    return wait


@pytest.fixture
def sse_data() -> Callable[[httpx2.Response], list[Any]]:
    """Parse JSON data fields from a buffered SSE response."""

    def parse(response: httpx2.Response) -> list[Any]:
        return [
            json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")
        ]

    return parse
