"""Tests for organization webhook setup."""

import json
from functools import partial

import httpx2
import pytest
import typer

from gh_babysitter.cli import setup
from gh_babysitter.server.events import EVENT_MENU


async def test_setup_webhook_creates_hook_and_prints_generated_secret_once(monkeypatch, capsys):
    monkeypatch.setattr(setup, "resolve_token", lambda: "token")
    monkeypatch.setattr(setup.secrets, "token_hex", lambda size: "generated-secret")
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx2.Response(200, json=[])
        if request.method == "POST":
            return httpx2.Response(201, json={"id": 7})
        raise AssertionError(request.method)

    client_factory = partial(
        httpx2.AsyncClient,
        transport=httpx2.MockTransport(handler),
    )
    monkeypatch.setattr(setup.httpx2, "AsyncClient", client_factory)

    await setup.setup_webhook(
        org="acme",
        url="https://hooks.example/webhook",
        api_url="https://github.acme.com/api/v3",
    )

    list_request, request = requests
    assert str(list_request.url) == ("https://github.acme.com/api/v3/orgs/acme/hooks?per_page=100")
    assert dict(list_request.url.params) == {"per_page": "100"}
    assert request.method == "POST"
    assert str(request.url) == "https://github.acme.com/api/v3/orgs/acme/hooks"
    assert request.headers["Authorization"] == "Bearer token"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert json.loads(request.content) == {
        "name": "web",
        "active": True,
        "events": sorted(EVENT_MENU),
        "config": {
            "url": "https://hooks.example/webhook",
            "content_type": "json",
            "secret": "generated-secret",
        },
    }
    output = capsys.readouterr().out
    assert output.count("generated-secret") == 1
    assert "GH_BABYSITTER_WEBHOOK_SECRET" in output


async def test_setup_webhook_paginates_and_updates_matching_hook(monkeypatch, capsys):
    monkeypatch.setattr(setup, "resolve_token", lambda: "token")
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "PATCH":
            return httpx2.Response(200, json={"id": 9})
        if request.url.params.get("page") == "2":
            return httpx2.Response(
                200,
                json=[{"id": 9, "config": {"url": "https://hooks.example/webhook"}}],
            )
        return httpx2.Response(
            200,
            json=[{"id": 1, "config": {"url": "https://other.example/webhook"}}],
            headers={"Link": '<https://api.github.com/orgs/acme/hooks?page=2>; rel="next"'},
        )

    client_factory = partial(
        httpx2.AsyncClient,
        transport=httpx2.MockTransport(handler),
    )
    monkeypatch.setattr(setup.httpx2, "AsyncClient", client_factory)

    await setup.setup_webhook(
        org="acme",
        url="https://hooks.example/webhook",
        events="issues, release",
        secret="provided-secret",
    )

    first_page, second_page, update_request = requests
    assert dict(first_page.url.params) == {"per_page": "100"}
    assert dict(second_page.url.params) == {"page": "2"}
    assert update_request.method == "PATCH"
    assert update_request.url.path == "/orgs/acme/hooks/9"
    assert json.loads(update_request.content)["events"] == ["issues", "release"]
    assert capsys.readouterr().out.count("provided-secret") == 1


async def test_setup_webhook_rejects_events_outside_the_allowlist(monkeypatch):
    monkeypatch.setattr(setup, "resolve_token", lambda: pytest.fail("token resolved"))

    with pytest.raises(typer.BadParameter, match="unsupported event"):
        await setup.setup_webhook(
            org="acme",
            url="https://hooks.example/webhook",
            events="issues,push",
        )
