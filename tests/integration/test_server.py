"""Integration tests for webhook-to-SSE server behavior."""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import patch

import anyio
import anyio.lowlevel
import httpx2
import pytest
from starlette.requests import Request

from gh_babysitter.server import app as app_module
from gh_babysitter.server.app import _stream, create_app
from gh_babysitter.server.auth import Access, Authenticator, Verdict
from gh_babysitter.server.config import Settings
from gh_babysitter.server.registry import Filter, Registry, Subscriber


class _FakeAuthenticator:
    def __init__(self) -> None:
        self.revoked = False
        self.access = Access(Verdict.ALLOWED, "octocat")
        self.rechecks: list[Access] = []
        self.calls: list[tuple[str, str, bool]] = []

    async def verify(self, token: str, repo: str, *, fresh: bool = False) -> Access:
        await anyio.lowlevel.checkpoint()
        self.calls.append((token, repo, fresh))
        if token != "token" or repo != "octo/repo" or self.revoked:
            return Access(Verdict.DENIED)
        if fresh and self.rechecks:
            return self.rechecks.pop(0)
        return self.access


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


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (b"{", "Malformed JSON payload"),
        (b"", "Malformed JSON payload"),
        (b"null", "Payload must be a JSON object"),
        (b"[]", "Payload must be a JSON object"),
        (b'"str"', "Payload must be a JSON object"),
        (b"42", "Payload must be a JSON object"),
    ],
)
async def test_webhook_rejects_invalid_json_and_remains_healthy(body, detail):
    ping_body = b"{}"
    async with make_client(authenticator=_FakeAuthenticator()) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))
        ping = await client.post(
            "/webhook",
            content=ping_body,
            headers=webhook_headers(ping_body, event="ping"),
        )

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    assert ping.status_code == 200


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


async def test_full_queue_drops_event_without_failing_webhook():
    registry = Registry()
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"existing": True})
    subscriber = Subscriber(queue)
    registry.register("octocat", [Filter(repo="octo/repo", event="issues")], subscriber)
    payload = {"repository": {"full_name": "octo/repo"}, "issue": {"number": 1}}
    body = json.dumps(payload).encode()

    async with make_client(registry=registry, authenticator=_FakeAuthenticator()) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))

    assert response.status_code == 202
    assert response.json() == {"matched": 1, "delivered": 0, "dropped": 1}
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
        with anyio.fail_after(1):
            response = await client.get(
                "/events/stream",
                params={"repo": "other/repo", "events": "issues"},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == 403
    assert response.json() == {"detail": "Repository access denied"}


async def test_stream_reports_unavailable_github():
    authenticator = _FakeAuthenticator()
    authenticator.access = Access(Verdict.UNAVAILABLE)
    async with make_client(authenticator=authenticator) as client:
        response = await client.get(
            "/events/stream",
            params={"repo": "octo/repo", "events": "issues"},
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json() == {"detail": "GitHub API unavailable"}


async def test_unavailable_recheck_stays_open_and_retries_within_thirty_seconds(monkeypatch):
    registry = Registry()
    authenticator = _FakeAuthenticator()
    authenticator.rechecks = [
        Access(Verdict.UNAVAILABLE),
        Access(Verdict.DENIED),
    ]
    app = create_app(
        Settings(webhook_secret="secret", recheck_interval=60, ping_interval=60),
        registry=registry,
        authenticator=authenticator,
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/events/stream",
            "headers": [(b"authorization", b"Bearer token")],
            "query_string": b"",
            "app": app,
        }
    )
    timeouts = []

    async def expire_immediately(awaitable, **options):
        await anyio.lowlevel.checkpoint()
        awaitable.close()
        timeouts.append(options["timeout"])
        raise TimeoutError

    monkeypatch.setattr(app_module.asyncio, "wait_for", expire_immediately)

    response = await _stream(request, repo="octo/repo", events="release")
    await anext(response.body_iterator)
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)

    assert timeouts == pytest.approx([60, 30], abs=0.01)
    assert [call[2] for call in authenticator.calls] == [False, True, True]
    assert registry.connections == {}


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
        with patch("gh_babysitter.server.app.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 7, 26, 12, 34, 56, 789012, tzinfo=UTC)
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
                assert delivery.json() == {
                    "matched": matched,
                    "delivered": matched,
                    "dropped": 0,
                }
        authenticator.revoked = True

    assert response is not None
    assert response.status_code == 200
    assert sse_data(response) == [
        {
            "filters": [
                {
                    "repo": "octo/repo",
                    "event": "issues",
                    "action": "closed",
                    "number": 42,
                }
            ]
        },
        {
            "ts": "2026-07-26T12:34:56.789012Z",
            "repo": "octo/repo",
            "event": "issues",
            "action": "closed",
            "number": 42,
            "payload": {
                "repository": {"full_name": "octo/repo"},
                "action": "closed",
                "issue": {"number": 42},
            },
        },
    ]


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


async def test_stalled_listener_receives_exact_delivery_loss_count():
    registry = Registry()
    authenticator = _FakeAuthenticator()
    app = create_app(
        Settings(
            webhook_secret="secret",
            recheck_interval=60,
            ping_interval=60,
            queue_maxsize=3,
        ),
        registry=registry,
        authenticator=authenticator,
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/events/stream",
            "headers": [(b"authorization", b"Bearer token")],
            "query_string": b"",
            "app": app,
        }
    )
    stream = await _stream(request, repo="octo/repo", events="issues")
    ready = await anext(stream.body_iterator)

    deliveries = []
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for number in range(7):
            payload = {
                "repository": {"full_name": "octo/repo"},
                "issue": {"number": number},
            }
            body = json.dumps(payload).encode()
            delivery = await client.post("/webhook", content=body, headers=webhook_headers(body))
            deliveries.append(delivery.json())

    lag = await anext(stream.body_iterator)
    event = await anext(stream.body_iterator)
    await stream.body_iterator.aclose()

    assert ready["event"] == "ready"
    assert sum(delivery["matched"] for delivery in deliveries) == 7
    assert sum(delivery["delivered"] for delivery in deliveries) == 3
    assert sum(delivery["dropped"] for delivery in deliveries) == 4
    assert lag == {"event": "lag", "data": '{"dropped":4}'}
    assert "event" not in event
    assert registry.connections == {}
