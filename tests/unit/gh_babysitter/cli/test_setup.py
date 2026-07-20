"""Tests for organization webhook setup."""

import json

import httpx
import pytest
import respx
import typer

from gh_babysitter.cli import setup
from gh_babysitter.server.events import EVENT_MENU


@respx.mock
async def test_setup_webhook_creates_hook_and_prints_generated_secret_once(monkeypatch, capsys):
    monkeypatch.setattr(setup, "resolve_token", lambda: "token")
    monkeypatch.setattr(setup.secrets, "token_hex", lambda size: "generated-secret")
    list_route = respx.get("https://api.github.com/orgs/acme/hooks", params={"per_page": "100"}).mock(
        return_value=httpx.Response(200, json=[]),
    )
    create_route = respx.post("https://api.github.com/orgs/acme/hooks").mock(
        return_value=httpx.Response(201, json={"id": 7}),
    )

    await setup.setup_webhook(org="acme", url="https://hooks.example/webhook")

    assert list_route.called
    request = create_route.calls[0].request
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


@respx.mock
async def test_setup_webhook_paginates_and_updates_matching_hook(monkeypatch, capsys):
    monkeypatch.setattr(setup, "resolve_token", lambda: "token")
    first_page = respx.get("https://api.github.com/orgs/acme/hooks", params={"per_page": "100"}).mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "config": {"url": "https://other.example/webhook"}}],
            headers={"Link": '<https://api.github.com/orgs/acme/hooks?page=2>; rel="next"'},
        ),
    )
    second_page = respx.get("https://api.github.com/orgs/acme/hooks?page=2").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 9, "config": {"url": "https://hooks.example/webhook"}}],
        ),
    )
    update_route = respx.patch("https://api.github.com/orgs/acme/hooks/9").mock(
        return_value=httpx.Response(200, json={"id": 9}),
    )

    await setup.setup_webhook(
        org="acme",
        url="https://hooks.example/webhook",
        events="issues, release",
        secret="provided-secret",
    )

    assert first_page.called
    assert second_page.called
    assert json.loads(update_route.calls[0].request.content)["events"] == ["issues", "release"]
    assert capsys.readouterr().out.count("provided-secret") == 1


async def test_setup_webhook_rejects_events_outside_the_allowlist(monkeypatch):
    monkeypatch.setattr(setup, "resolve_token", lambda: pytest.fail("token resolved"))

    with pytest.raises(typer.BadParameter, match="unsupported event"):
        await setup.setup_webhook(
            org="acme",
            url="https://hooks.example/webhook",
            events="issues,push",
        )
