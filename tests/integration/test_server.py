"""Integration tests for webhook-to-SSE server behavior."""

import asyncio
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
from gh_babysitter.server.auth import Access, Verdict
from gh_babysitter.server.config import Settings
from gh_babysitter.server.registry import Filter, Registry, Subscriber


async def test_webhook_rejects_bad_hmac_before_parsing_json(
    make_client,
    fake_authenticator,
    webhook_headers,
):
    async with make_client(authenticator=fake_authenticator) as client:
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
async def test_webhook_rejects_invalid_json_and_remains_healthy(
    body,
    detail,
    make_client,
    fake_authenticator,
    webhook_headers,
):
    ping_body = b"{}"
    async with make_client(authenticator=fake_authenticator) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))
        ping = await client.post(
            "/webhook",
            content=ping_body,
            headers=webhook_headers(ping_body, event="ping"),
        )

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    assert ping.status_code == 200


async def test_webhook_rejects_requests_when_secret_is_unset(fake_authenticator):
    app = create_app(Settings(webhook_secret=None), authenticator=fake_authenticator)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhook", content=b"{}")

    assert response.status_code == 401


async def test_webhook_ping_returns_ok(
    make_client,
    fake_authenticator,
    webhook_headers,
):
    body = b"{}"
    async with make_client(authenticator=fake_authenticator) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body, event="ping"))

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_full_queue_drops_event_without_failing_webhook(
    make_client,
    fake_authenticator,
    webhook_headers,
):
    registry = Registry()
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"existing": True})
    subscriber = Subscriber(queue)
    registry.register("octocat", [Filter(repo="octo/repo", event="issues")], subscriber)
    payload = {"repository": {"full_name": "octo/repo"}, "issue": {"number": 1}}
    body = json.dumps(payload).encode()

    async with make_client(registry=registry, authenticator=fake_authenticator) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))

    assert response.status_code == 202
    assert response.json() == {"matched": 1, "delivered": 0, "dropped": 1}
    assert queue.get_nowait() == {"existing": True}


async def test_webhook_without_repository_returns_204(
    make_client,
    fake_authenticator,
    webhook_headers,
):
    body = b"{}"
    async with make_client(authenticator=fake_authenticator) as client:
        response = await client.post("/webhook", content=body, headers=webhook_headers(body))

    assert response.status_code == 204


async def test_stream_requires_bearer_token(make_client, fake_authenticator):
    async with make_client(authenticator=fake_authenticator) as client:
        response = await client.get("/events/stream", params={"repo": "octo/repo", "events": "issues"})

    assert response.status_code == 401


async def test_stream_rejects_unknown_repository(make_client, fake_authenticator):
    async with make_client(authenticator=fake_authenticator) as client:
        with anyio.fail_after(1):
            response = await client.get(
                "/events/stream",
                params={"repo": "other/repo", "events": "issues"},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == 403
    assert response.json() == {"detail": "Repository access denied"}


async def test_stream_reports_unavailable_github(make_client, fake_authenticator):
    fake_authenticator.access = Access(Verdict.UNAVAILABLE)
    async with make_client(authenticator=fake_authenticator) as client:
        response = await client.get(
            "/events/stream",
            params={"repo": "octo/repo", "events": "issues"},
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json() == {"detail": "GitHub API unavailable"}


async def test_unavailable_recheck_stays_open_and_retries_within_thirty_seconds(
    monkeypatch,
    fake_authenticator,
):
    registry = Registry()
    fake_authenticator.rechecks = [
        Access(Verdict.UNAVAILABLE),
        Access(Verdict.DENIED),
    ]
    app = create_app(
        Settings(webhook_secret="secret", recheck_interval=60, ping_interval=60),
        registry=registry,
        authenticator=fake_authenticator,
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
    assert [call[2] for call in fake_authenticator.calls] == [False, True, True]
    assert registry.connections == {}


async def test_stream_rejects_bad_events_and_repository_names(
    make_client,
    fake_authenticator,
):
    async with make_client(authenticator=fake_authenticator) as client:
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


async def test_webhook_to_sse_respects_action_and_number_filters(
    make_client,
    fake_authenticator,
    wait_until_registered,
    webhook_headers,
    sse_data,
):
    registry = Registry()
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
        make_client(registry=registry, authenticator=fake_authenticator) as client,
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
        fake_authenticator.revoked = True

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


class TestBurstOrdering:
    """Delivery order for a burst buffered by one subscriber."""

    async def test_single_subscriber_receives_burst_in_delivery_order(
        self,
        fake_authenticator,
        make_app,
        deliver,
    ):
        """Preserve webhook arrival order while draining a buffered burst."""
        event_count = 10
        registry = Registry()
        app = make_app(
            registry=registry,
            authenticator=fake_authenticator,
            queue_maxsize=event_count,
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

        deliveries = [
            await deliver(
                app,
                "issues",
                {
                    "repository": {"full_name": "octo/repo"},
                    "action": "opened",
                    "issue": {"number": number},
                },
            )
            for number in range(event_count)
        ]
        frames = [await anext(stream.body_iterator) for _ in range(event_count)]
        await stream.body_iterator.aclose()

        assert ready["event"] == "ready"
        assert [delivery.json() for delivery in deliveries] == [
            {"matched": 1, "delivered": 1, "dropped": 0}
        ] * event_count
        assert [json.loads(frame["data"])["number"] for frame in frames] == list(range(event_count))
        assert registry.connections == {}


async def test_recheck_closes_stream_after_access_revocation(
    make_client,
    fake_authenticator,
    wait_until_registered,
    sse_data,
):
    registry = Registry()
    response: httpx2.Response | None = None

    async def consume(client):
        nonlocal response
        response = await client.get(
            "/events/stream",
            params={"repo": "octo/repo", "events": "release"},
            headers={"Authorization": "Bearer token"},
        )

    async with (
        make_client(registry=registry, authenticator=fake_authenticator) as client,
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(consume, client)
        await wait_until_registered(registry)
        fake_authenticator.revoked = True

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
    assert ("token", "octo/repo", True) in fake_authenticator.calls
    assert registry.connections == {}


async def test_configured_ping_interval_emits_comment(
    make_client,
    fake_authenticator,
    wait_until_registered,
):
    registry = Registry()
    response: httpx2.Response | None = None

    async def consume(client):
        nonlocal response
        response = await client.get(
            "/events/stream",
            params={"repo": "octo/repo", "events": "issues"},
            headers={"Authorization": "Bearer token"},
        )

    async with (
        make_client(
            registry=registry,
            authenticator=fake_authenticator,
            ping_interval=1,
            recheck_interval=1.2,
        ) as client,
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(consume, client)
        await wait_until_registered(registry)
        await anyio.sleep(1.05)
        fake_authenticator.revoked = True

    assert response is not None
    assert response.status_code == 200
    assert "event: ready" in response.text
    assert any(line.startswith(": ping - ") for line in response.text.splitlines())
    assert registry.connections == {}


async def test_stalled_listener_receives_exact_delivery_loss_count(
    fake_authenticator,
    webhook_headers,
):
    registry = Registry()
    app = create_app(
        Settings(
            webhook_secret="secret",
            recheck_interval=60,
            ping_interval=60,
            queue_maxsize=3,
        ),
        registry=registry,
        authenticator=fake_authenticator,
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
