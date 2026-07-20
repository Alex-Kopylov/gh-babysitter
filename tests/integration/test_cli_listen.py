"""Integration tests for the CLI listen core against the real server app."""

import asyncio
import hashlib
import hmac
import json

import anyio.lowlevel
import httpx
import pytest
import respx

from gh_babysitter.cli import listen
from gh_babysitter.server.app import create_app
from gh_babysitter.server.config import Settings
from gh_babysitter.server.registry import Registry


class _FakeAuthenticator:
    def __init__(self):
        self.revoked = False

    async def verify(self, token, repo, *, fresh=False):
        if token == "token" and repo == "octo/repo" and not self.revoked:
            return "octocat"
        return None


def make_app(registry, authenticator):
    return create_app(
        Settings(webhook_secret="secret", recheck_interval=0.01, ping_interval=60),
        registry=registry,
        authenticator=authenticator,
    )


def client_factory(app):
    def make(*, base_url, headers, **kwargs):
        transport = httpx.ASGITransport(app=app) if base_url == "http://server" else None
        return httpx.AsyncClient(transport=transport, base_url=base_url, headers=headers, **kwargs)

    return make


async def wait_until_registered(registry):
    with anyio.fail_after(1):
        while not registry.connections:
            await anyio.lowlevel.checkpoint()


async def deliver(app, event, payload):
    body = json.dumps(payload).encode()
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": event, "X-Hub-Signature-256": f"sha256={digest}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://server") as client:
        response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 202


@pytest.mark.parametrize(
    "exit_options",
    [
        {"count": 1},
        {"first_event": True},
    ],
)
async def test_listen_exits_after_requested_event_count(monkeypatch, capsys, exit_options):
    registry = Registry()
    authenticator = _FakeAuthenticator()
    app = make_app(registry, authenticator)
    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    task = asyncio.create_task(
        listen.listen(
            listen.ListenOptions(
                repo="octo/repo",
                events="issues",
                timeout=1,
                server="http://server",
                **exit_options,
            ),
            client_factory(app),
        )
    )
    await wait_until_registered(registry)
    await deliver(
        app,
        "issues",
        {"repository": {"full_name": "octo/repo"}, "action": "opened", "issue": {"number": 42}},
    )
    authenticator.revoked = True

    assert await task == 0
    assert json.loads(capsys.readouterr().out)["number"] == 42


@respx.mock
async def test_listen_until_exits_on_terminal_stream_event(monkeypatch, capsys):
    registry = Registry()
    authenticator = _FakeAuthenticator()
    app = make_app(registry, authenticator)
    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    respx.get("https://api.github.com/repos/octo/repo/pulls/42").mock(
        return_value=httpx.Response(200, json={"merged": False}),
    )
    task = asyncio.create_task(
        listen.listen(
            listen.ListenOptions(
                repo="octo/repo",
                number=42,
                until="merged",
                timeout=1,
                server="http://server",
            ),
            client_factory(app),
        )
    )
    await wait_until_registered(registry)
    await deliver(
        app,
        "pull_request",
        {
            "repository": {"full_name": "octo/repo"},
            "action": "closed",
            "pull_request": {"number": 42, "merged": True},
        },
    )
    authenticator.revoked = True

    assert await task == 0
    assert json.loads(capsys.readouterr().out)["event"] == "pull_request"


@respx.mock
async def test_listen_until_polls_before_connecting(monkeypatch, capsys):
    registry = Registry()
    authenticator = _FakeAuthenticator()
    app = make_app(registry, authenticator)
    monkeypatch.setattr(listen, "resolve_token", lambda: "token")
    poll = respx.get("https://api.github.com/repos/octo/repo/issues/42").mock(
        return_value=httpx.Response(200, json={"state": "closed"}),
    )

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            number=42,
            until="closed",
            timeout=1,
            server="http://server",
        ),
        client_factory(app),
    )

    assert result == 0
    assert poll.called
    assert registry.connections == {}
    assert capsys.readouterr().out == ""


async def test_listen_timeout_returns_124(monkeypatch):
    registry = Registry()
    authenticator = _FakeAuthenticator()
    app = make_app(registry, authenticator)
    monkeypatch.setattr(listen, "resolve_token", lambda: "token")

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues", timeout=0.05, server="http://server"),
        client_factory(app),
    )

    assert result == 124
