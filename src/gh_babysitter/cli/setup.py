"""Organization webhook setup."""

import secrets
from typing import Any

import httpx2
import typer

from gh_babysitter.cli.token import resolve_token
from gh_babysitter.server.config import DEFAULT_GITHUB_API_URL
from gh_babysitter.server.events import EVENT_MENU


async def setup_webhook(
    org: str,
    url: str,
    events: str | None = None,
    secret: str | None = None,
    api_url: str = DEFAULT_GITHUB_API_URL,
) -> None:
    """Create or update an organization webhook and print its secret once."""
    event_names = [event.strip() for event in events.split(",")] if events else sorted(EVENT_MENU)
    if any(not event or event not in EVENT_MENU for event in event_names):
        message = "--events contains an unsupported event"
        raise typer.BadParameter(message)
    supplied_secret = secret is not None
    secret = secret if supplied_secret else secrets.token_hex(32)
    body: dict[str, Any] = {
        "name": "web",
        "active": True,
        "events": event_names,
        "config": {"url": url, "content_type": "json", "secret": secret},
    }
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {resolve_token()}",
    }
    hook_id = None
    next_url: str | None = f"/orgs/{org}/hooks"
    params: dict[str, int] | None = {"per_page": 100}
    async with httpx2.AsyncClient(base_url=api_url, headers=headers) as client:
        while next_url:
            response = await client.get(next_url, params=params)
            response.raise_for_status()
            params = None
            for hook in response.json():
                if hook.get("config", {}).get("url") == url:
                    hook_id = hook["id"]
                    break
            if hook_id is not None:
                break
            next_url = response.links.get("next", {}).get("url")

        path = f"/orgs/{org}/hooks/{hook_id}" if hook_id is not None else f"/orgs/{org}/hooks"
        response = await (client.patch(path, json=body) if hook_id is not None else client.post(path, json=body))
        response.raise_for_status()

    if supplied_secret:
        print("webhook configured; reusing the supplied GH_BABYSITTER_WEBHOOK_SECRET")
    else:
        print(f"Set GH_BABYSITTER_WEBHOOK_SECRET={secret} on the server.")
