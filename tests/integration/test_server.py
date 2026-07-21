"""Integration tests for webhook-to-SSE server behavior."""

import asyncio
import hashlib
import hmac
import json

import anyio
import anyio.lowlevel
import httpx2

from gh_babysitter.server.app import create_app
from gh_babysitter.server.auth import Authenticator
from gh_babysitter.server.config import Settings
from gh_babysitter.server.registry import Filter, Registry


class _FakeAuthenticator:
    def __init__(self) -> None:
        self.revoked = False
        self.calls: list[tuple[str, str, bool]] = []

    async def verify(self, token: str, repo: str, *, fresh: bool = False) -> str | None:
        await anyio.lowlevel.checkpoint()
        self.calls.append((token, repo, fresh))
        if token != "token" or repo != "octo/repo" or self.revoked:
            return None
        return "octocat"


def make_client(
    *,
    registry: Registry | None = None,
    authenticator: Authenticator | None = None,
    recheck_interval: float = 0.02,
) -> httpx2.AsyncClient:
    settings = Settings(
        webhook_secret="secret",
        recheck_interval=recheck_interval,
        ping_interval=60,
        queue_maxsize=1,
    )
    app = create_app(settings, registry=registry, authenticator=authenticator)
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


def webhook_headers(body, event="issues", *, valid=True):
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": f"sha256={digest if valid else '0' * 64}",
    }


async def wait_until_registered(registry: Registry) -> None:
    with anyio.fail_after(1):
        await anyio.lowlevel.checkpoint()
        while not registry.connections:  # noqa: ASYNC110 - Registry has no notification hook.
            await anyio.lowlevel.checkpoint()


def sse_data(response: httpx2.Response):
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]


async def test_webhook_rejects_bad_hmac_before_parsing_json():
    async with make_client(authenticator=_FakeAuthenticator()) as client:
        response = await client.post("/webhook", content=b"not-json", headers=webhook_headers(b"not-json", valid=False))

    assert response.status_code == 401


async def test_webhook_rejects_requests_when_secret_is_unset():
    app = create_app(Settings(webhook_secret=None), authenticator=_FakeAuthenticator())
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhook", content=b"{}")

    assert response.status_code == 401


async def test_webhook_ping_returns_ok():
    body = b"{}"
    async with make_client(authenticator=_FakeAuthenticator()) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body, event="ping"))

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_webhook_matches_and_enqueues_event():
    registry = Registry()
    queue = asyncio.Queue()
    registry.register("octocat", [Filter(repo="octo/repo", event="issues")], queue)
    payload = {
        "repository": {"full_name": "octo/repo"},
        "action": "opened",
        "issue": {"number": 42},
    }
    body = json.dumps(payload).encode()

    async with make_client(registry=registry, authenticator=_FakeAuthenticator()) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))

    assert response.status_code == 202
    assert response.json() == {"matched": 1}
    envelope = queue.get_nowait()
    assert envelope | {"ts": "ignored"} == {
        "ts": "ignored",
        "repo": "octo/repo",
        "event": "issues",
        "action": "opened",
        "number": 42,
        "payload": payload,
    }
    assert envelope["ts"].endswith("Z")


async def test_full_queue_drops_event_without_failing_webhook():
    registry = Registry()
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"existing": True})
    registry.register("octocat", [Filter(repo="octo/repo", event="issues")], queue)
    payload = {"repository": {"full_name": "octo/repo"}, "issue": {"number": 1}}
    body = json.dumps(payload).encode()

    async with make_client(registry=registry, authenticator=_FakeAuthenticator()) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))

    assert response.status_code == 202
    assert response.json() == {"matched": 1}
    assert queue.get_nowait() == {"existing": True}


async def test_webhook_without_repository_returns_204():
    body = b"{}"
    async with make_client(authenticator=_FakeAuthenticator()) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))

    assert response.status_code == 204


async def test_stream_requires_bearer_token():
    async with make_client(authenticator=_FakeAuthenticator()) as client:
        response = await client.get("/events/stream", params={"repo": "octo/repo", "events": "issues"})

    assert response.status_code == 401


async def test_stream_rejects_unknown_repository():
    async with make_client(authenticator=_FakeAuthenticator()) as client:
        response = await client.get(
            "/events/stream",
            params={"repo": "other/repo", "events": "issues"},
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 403


async def test_stream_rejects_bad_events_and_repository_names():
    async with make_client(authenticator=_FakeAuthenticator()) as client:
        bad_event = await client.get(
            "/events/stream",
            params={"repo": "octo/repo", "events": "push"},
            headers={"Authorization": "Bearer token"},
        )
        bad_repo = await client.get(
            "/events/stream",
            params={"repo": "octo", "events": "issues"},
            headers={"Authorization": "Bearer token"},
        )

    assert bad_event.status_code == 422
    assert bad_repo.status_code == 422


async def test_webhook_to_sse_respects_action_and_number_filters():
    registry = Registry()
    authenticator = _FakeAuthenticator()
    response: httpx2.Response | None = None

    async def consume(client):
        nonlocal response
        response = await client.get(
            "/events/stream",
            params={
                "repo": "octo/repo",
                "events": "issues",
                "action": "closed",
                "number": 42,
            },
            headers={"Authorization": "Bearer token"},
        )

    async with (
        make_client(registry=registry, authenticator=authenticator) as client,
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(consume, client)
        await wait_until_registered(registry)
        for action, number, matched in (
            ("opened", 42, 0),
            ("closed", 41, 0),
            ("closed", 42, 1),
        ):
            payload = {
                "repository": {"full_name": "octo/repo"},
                "action": action,
                "issue": {"number": number},
            }
            body = json.dumps(payload).encode()
            delivery = await client.post("/webhook", content=body, headers=webhook_headers(body))
            assert delivery.json() == {"matched": matched}
        authenticator.revoked = True

    assert response is not None
    assert response.status_code == 200
    messages = sse_data(response)
    assert messages[0]["filters"] == [
        {
            "repo": "octo/repo",
            "event": "issues",
            "action": "closed",
            "number": 42,
        }
    ]
    assert [(message["action"], message["number"]) for message in messages[1:]] == [("closed", 42)]


async def test_overlapping_stream_filters_receive_one_delivery():
    registry = Registry()
    authenticator = _FakeAuthenticator()
    response: httpx2.Response | None = None

    async def consume(client):
        nonlocal response
        response = await client.get(
            "/events/stream",
            params={"repo": "octo/repo", "events": "issues,issues"},
            headers={"Authorization": "Bearer token"},
        )

    async with (
        make_client(registry=registry, authenticator=authenticator) as client,
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(consume, client)
        await wait_until_registered(registry)
        payload = {
            "repository": {"full_name": "octo/repo"},
            "action": "opened",
            "issue": {"number": 42},
        }
        body = json.dumps(payload).encode()
        await client.post("/webhook", content=body, headers=webhook_headers(body))
        authenticator.revoked = True

    assert response is not None
    assert len(sse_data(response)[1:]) == 1


async def test_recheck_closes_stream_after_access_revocation():
    registry = Registry()
    authenticator = _FakeAuthenticator()
    response: httpx2.Response | None = None

    async def consume(client):
        nonlocal response
        response = await client.get(
            "/events/stream",
            params={"repo": "octo/repo", "events": "release"},
            headers={"Authorization": "Bearer token"},
        )

    async with (
        make_client(registry=registry, authenticator=authenticator) as client,
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(consume, client)
        await wait_until_registered(registry)
        authenticator.revoked = True

    assert response is not None
    assert response.status_code == 200
    assert sse_data(response) == [
        {
            "filters": [
                {
                    "repo": "octo/repo",
                    "event": "release",
                    "action": None,
                    "number": None,
                }
            ]
        }
    ]
    assert ("token", "octo/repo", True) in authenticator.calls
    assert registry.connections == {}
