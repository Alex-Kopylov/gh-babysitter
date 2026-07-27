"""Integration tests for the CLI listen core against the real server app."""

import asyncio
import json

import httpx2
import pytest

from gh_babysitter.cli import listen
from gh_babysitter.server.registry import Registry


@pytest.mark.parametrize(
    "exit_options",
    [
        {"count": 1},
        {"first_event": True},
    ],
)
async def test_listen_exits_after_requested_event_count(
    capsys,
    exit_options,
    fake_authenticator,
    make_app,
    client_factory,
    wait_until_registered,
    deliver,
    fake_token,
):
    registry = Registry()
    app = make_app(
        registry=registry,
        authenticator=fake_authenticator,
        recheck_interval=0.01,
    )
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
    fake_authenticator.revoked = True

    assert await task == 0
    assert json.loads(capsys.readouterr().out)["number"] == 42


async def test_combined_filters_emit_only_exact_match(
    capsys,
    fake_authenticator,
    make_app,
    client_factory,
    wait_until_registered,
    deliver,
    fake_token,
):
    registry = Registry()
    app = make_app(
        registry=registry,
        authenticator=fake_authenticator,
        recheck_interval=0.01,
    )
    task = asyncio.create_task(
        listen.listen(
            listen.ListenOptions(
                repo="octo/repo",
                events="issues",
                number=42,
                action="closed",
                count=1,
                timeout=1,
                server="http://server",
            ),
            client_factory(app),
        )
    )
    await wait_until_registered(registry)

    opened = await deliver(
        app,
        "issues",
        {
            "repository": {"full_name": "octo/repo"},
            "action": "opened",
            "issue": {"number": 42},
        },
    )
    wrong_number = await deliver(
        app,
        "issues",
        {
            "repository": {"full_name": "octo/repo"},
            "action": "closed",
            "issue": {"number": 41},
        },
    )
    exact = await deliver(
        app,
        "issues",
        {
            "repository": {"full_name": "octo/repo"},
            "action": "closed",
            "issue": {"number": 42},
        },
    )
    fake_authenticator.revoked = True

    assert opened.json()["matched"] == 0
    assert wrong_number.json()["matched"] == 0
    assert exact.json()["matched"] == 1
    assert await task == 0
    assert json.loads(capsys.readouterr().out)["payload"]["issue"]["number"] == 42


async def test_listen_until_exits_on_terminal_stream_event(
    capsys,
    fake_authenticator,
    make_app,
    client_factory,
    wait_until_registered,
    deliver,
    fake_token,
):
    registry = Registry()
    app = make_app(
        registry=registry,
        authenticator=fake_authenticator,
        recheck_interval=0.01,
    )
    github_requests = []

    def github_handler(request):
        assert request.method == "GET"
        github_requests.append(request)
        return httpx2.Response(200, json={"merged": False})

    task = asyncio.create_task(
        listen.listen(
            listen.ListenOptions(
                repo="octo/repo",
                number=42,
                until="merged",
                timeout=1,
                server="http://server",
            ),
            client_factory(app, github_handler),
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
    fake_authenticator.revoked = True

    assert await task == 0
    assert [request.url.path for request in github_requests] == ["/repos/octo/repo/pulls/42"]
    assert json.loads(capsys.readouterr().out)["event"] == "pull_request"


async def test_listen_until_polls_before_connecting(
    capsys,
    fake_authenticator,
    make_app,
    client_factory,
    fake_token,
):
    registry = Registry()
    app = make_app(
        registry=registry,
        authenticator=fake_authenticator,
        recheck_interval=0.01,
    )
    github_requests = []

    def github_handler(request):
        assert request.method == "GET"
        github_requests.append(request)
        return httpx2.Response(200, json={"state": "closed"})

    result = await listen.listen(
        listen.ListenOptions(
            repo="octo/repo",
            number=42,
            until="closed",
            timeout=1,
            server="http://server",
        ),
        client_factory(app, github_handler),
    )

    assert result == 0
    assert [request.url.path for request in github_requests] == ["/repos/octo/repo/issues/42"]
    assert registry.connections == {}
    assert capsys.readouterr().out == ""


async def test_stream_disconnect_with_satisfied_until_poll_exits_before_reconnect(
    monkeypatch,
    capsys,
    fake_authenticator,
    make_app,
    client_factory,
    wait_until_registered,
    fake_token,
):
    registry = Registry()
    app = make_app(
        registry=registry,
        authenticator=fake_authenticator,
        recheck_interval=0.01,
    )
    github_requests = []

    def github_handler(request):
        github_requests.append(request)
        return httpx2.Response(200, json={"merged": len(github_requests) == 2})

    monkeypatch.setattr(listen.random, "uniform", lambda _low, _high: 0)
    task = asyncio.create_task(
        listen.listen(
            listen.ListenOptions(
                repo="octo/repo",
                number=42,
                until="merged",
                timeout=1,
                server="http://server",
            ),
            client_factory(app, github_handler),
        )
    )
    await wait_until_registered(registry)
    fake_authenticator.revoked = True

    result = await task

    assert result == 0
    assert [request.url.path for request in github_requests] == [
        "/repos/octo/repo/pulls/42",
        "/repos/octo/repo/pulls/42",
    ]
    assert "warning: disconnected" not in capsys.readouterr().err
    assert registry.connections == {}


async def test_listen_timeout_returns_124(
    fake_authenticator,
    make_app,
    client_factory,
    fake_token,
):
    registry = Registry()
    app = make_app(
        registry=registry,
        authenticator=fake_authenticator,
        recheck_interval=0.01,
    )

    result = await listen.listen(
        listen.ListenOptions(repo="octo/repo", events="issues", timeout=0.05, server="http://server"),
        client_factory(app),
    )

    assert result == 124
