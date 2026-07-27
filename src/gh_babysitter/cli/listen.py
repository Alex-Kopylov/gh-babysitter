"""SSE listening core for the CLI."""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx2
import typer

from gh_babysitter.cli.config import DEFAULT_SERVER, get_settings
from gh_babysitter.cli.sse import parse_sse
from gh_babysitter.cli.token import resolve_token
from gh_babysitter.cli.until import UNTIL_MATRIX, satisfied_by_event, satisfied_by_poll
from gh_babysitter.server.config import DEFAULT_GITHUB_API_URL
from gh_babysitter.server.events import EVENT_MENU

if TYPE_CHECKING:
    from collections.abc import Callable

_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ListenOptions:
    """Validated inputs for one listen process."""

    repo: str
    events: str | None = None
    number: int | None = None
    action: str | None = None
    until: str | None = None
    timeout: float | None = None
    count: int | None = None
    first_event: bool = False
    server: str = DEFAULT_SERVER
    api_url: str = DEFAULT_GITHUB_API_URL
    format: str = "json"


def _validate_target(options: ListenOptions) -> None:
    if _REPO.fullmatch(options.repo) is None:
        message = "--repo must be owner/name using only letters, digits, dots, underscores, and hyphens"
        raise typer.BadParameter(message)
    if options.number is not None and options.number < 1:
        message = "--number must be at least 1"
        raise typer.BadParameter(message)
    if options.action is not None and not options.action:
        message = "--action must not be empty"
        raise typer.BadParameter(message)
    try:
        server_url = urlsplit(options.server)
        valid_server = server_url.scheme in {"http", "https"} and server_url.hostname is not None
    except ValueError:
        valid_server = False
    if not valid_server:
        message = "--server must be an http or https URL with a host"
        raise typer.BadParameter(message)


def _validated(options: ListenOptions) -> tuple[list[str], int | None]:
    _validate_target(options)
    if options.until and options.number is None:
        message = "--until requires --number"
        raise typer.BadParameter(message)
    if options.first_event and options.count is not None:
        message = "--first-event cannot be combined with --count"
        raise typer.BadParameter(message)
    if options.until is not None and options.until not in UNTIL_MATRIX:
        message = f"invalid --until value: {options.until}"
        raise typer.BadParameter(message)
    if options.count is not None and options.count < 1:
        message = "--count must be at least 1"
        raise typer.BadParameter(message)
    if options.format not in {"json", "pretty"}:
        message = "--format must be json or pretty"
        raise typer.BadParameter(message)

    events = [event.strip() for event in (options.events or "").split(",") if event.strip()]
    if any(event not in EVENT_MENU for event in events):
        message = "--events contains an unsupported event"
        raise typer.BadParameter(message)
    if options.until:
        events.extend(sorted(UNTIL_MATRIX[options.until] - set(events)))
    if not events:
        message = "--events is required without --until"
        raise typer.BadParameter(message)
    return events, 1 if options.first_event else options.count


def _print_event(envelope: dict[str, Any], output_format: str) -> None:
    if output_format == "pretty":
        print(
            f"{envelope.get('ts')} {envelope.get('repo')} "
            f"{envelope.get('event')}.{envelope.get('action')} #{envelope.get('number')}",
            flush=True,
        )
    else:
        print(json.dumps(envelope, separators=(",", ":")), flush=True)


async def _consume_stream(
    response: httpx2.Response,
    options: ListenOptions,
    remaining: int | None,
) -> tuple[int | None, int | None]:
    async for event_type, data in parse_sse(response.aiter_lines()):
        if event_type == "ready":
            print("subscribed", file=sys.stderr)
            continue
        envelope = json.loads(data)
        _print_event(envelope, options.format)
        if options.until and satisfied_by_event(options.until, envelope):
            return 0, remaining
        if remaining is not None:
            remaining -= 1
            if remaining == 0:
                return 0, remaining
    return None, remaining


def _response_exit_code(response: httpx2.Response) -> int | None:
    if response.status_code in {401, 403}:
        print(f"server rejected the GitHub token ({response.status_code})", file=sys.stderr)
        return 1
    response.raise_for_status()
    return None


async def _listen(
    options: ListenOptions,
    events: list[str],
    count: int | None,
    server_client: httpx2.AsyncClient,
    github_client: httpx2.AsyncClient | None,
) -> int:
    await asyncio.sleep(0)
    if (
        options.until is not None
        and github_client is not None
        and options.number is not None
        and await satisfied_by_poll(options.until, github_client, options.repo, options.number)
    ):
        return 0

    params: dict[str, str | int] = {"repo": options.repo, "events": ",".join(events)}
    if options.number is not None:
        params["number"] = options.number
    if options.action is not None:
        params["action"] = options.action

    remaining = count
    backoff = 1.0
    while True:
        try:
            async with server_client.stream("GET", "/events/stream", params=params) as response:
                if (exit_code := _response_exit_code(response)) is not None:
                    return exit_code
                backoff = 1.0
                result, remaining = await _consume_stream(response, options, remaining)
                if result is not None:
                    return result
        except httpx2.HTTPError:
            pass

        if (
            options.until is not None
            and github_client is not None
            and options.number is not None
            and await satisfied_by_poll(options.until, github_client, options.repo, options.number)
        ):
            return 0
        await asyncio.sleep(random.uniform(0.8, 1.2) * backoff)  # ruff:ignore[suspicious-non-cryptographic-random-usage]
        backoff = min(backoff * 2, 30)


async def listen(
    options: ListenOptions,
    client_factory: Callable[..., httpx2.AsyncClient] = httpx2.AsyncClient,
) -> int:
    """Listen for matching server events until an exit condition is met."""
    await asyncio.sleep(0)
    events, count = _validated(options)
    token = resolve_token()
    settings = get_settings()
    # An SSE stream stays open indefinitely, so only its read timeout is unbounded.
    server_timeout = httpx2.Timeout(
        connect=settings.server_timeout,
        read=None,
        write=settings.server_timeout,
        pool=settings.server_timeout,
    )
    server_headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    try:
        async with asyncio.timeout(options.timeout):
            async with client_factory(
                base_url=options.server.rstrip("/"),
                headers=server_headers,
                timeout=server_timeout,
            ) as server_client:
                if options.until:
                    github_headers = {
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    }
                    async with client_factory(
                        base_url=options.api_url,
                        headers=github_headers,
                        timeout=httpx2.Timeout(settings.github_timeout),
                    ) as github_client:
                        return await _listen(options, events, count, server_client, github_client)
                return await _listen(options, events, count, server_client, None)
    except TimeoutError:
        return 124
    except (httpx2.HTTPError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
