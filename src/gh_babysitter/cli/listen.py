"""SSE listening core for the CLI."""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from dataclasses import dataclass
from ipaddress import ip_address
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
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_HTTP_CLIENT_ERROR = 400
_HTTP_SERVER_ERROR = 500
_HTTP_STATUS_LIMIT = 600
_ASYNCGEN_SHUTDOWN_ERROR = "generator didn't stop after athrow()"


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


@dataclass(frozen=True)
class _Outcome:
    exit_code: int | None = None
    retry_reason: str | None = None


@dataclass
class _StreamState:
    remaining: int | None
    ready: bool = False


def _install_httpcore2_shutdown_workaround(loop: asyncio.AbstractEventLoop) -> None:
    # Work around httpcore2 2.7.0: httpcore2/_async/http11.py:311 catches
    # GeneratorExit via `except BaseException`, then awaits. Delete when upstream fixes it.
    previous_handler = loop.get_exception_handler()

    def handle_exception(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exception = context.get("exception")
        asyncgen = context.get("asyncgen")
        code = getattr(asyncgen, "ag_code", None)
        filename = getattr(code, "co_filename", "")
        if (
            isinstance(exception, RuntimeError)
            and str(exception) == _ASYNCGEN_SHUTDOWN_ERROR
            and isinstance(filename, str)
            and "httpcore2" in filename
        ):
            return
        if previous_handler is None:
            current_loop.default_exception_handler(context)
        else:
            previous_handler(current_loop, context)

    loop.set_exception_handler(handle_exception)


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


def _guard_token_transport(server: str, *, insecure: bool) -> None:
    server_url = urlsplit(server)
    host = server_url.hostname or ""
    try:
        loopback = host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if server_url.scheme == "http" and not loopback and not insecure:
        message = (
            f"refusing to send a GitHub token over plain HTTP to {host}; use https:// or set GH_BABYSITTER_INSECURE=1"
        )
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
    state: _StreamState,
) -> _Outcome:
    await asyncio.sleep(0)
    async for event_type, data in parse_sse(response.aiter_lines()):
        if event_type == "ready":
            state.ready = True
            print("subscribed", file=sys.stderr)
            continue
        if event_type == "lag":
            dropped = json.loads(data)["dropped"]
            print(f"warning: server dropped {dropped} events (consumer too slow)", file=sys.stderr)
            continue
        envelope = json.loads(data)
        _print_event(envelope, options.format)
        if options.until and satisfied_by_event(options.until, envelope):
            return _Outcome(exit_code=0)
        if state.remaining is not None:
            state.remaining -= 1
            if state.remaining == 0:
                return _Outcome(exit_code=0)
    return _Outcome(retry_reason="stream ended")


async def _response_detail(response: httpx2.Response) -> str | None:
    await response.aread()
    try:
        body = response.json()
    except json.JSONDecodeError:
        return None
    if isinstance(body, dict) and isinstance(detail := body.get("detail"), str):
        return detail
    return None


async def _response_outcome(response: httpx2.Response) -> _Outcome:
    await asyncio.sleep(0)
    status = response.status_code
    if status in {401, 403}:
        print(f"server rejected the GitHub token ({status})", file=sys.stderr)
        return _Outcome(exit_code=1)
    if status in _RETRYABLE_STATUS or _HTTP_SERVER_ERROR <= status < _HTTP_STATUS_LIMIT:
        return _Outcome(retry_reason=f"server returned {status}")
    if _HTTP_CLIENT_ERROR <= status < _HTTP_SERVER_ERROR:
        detail = await _response_detail(response)
        print(f"error: {detail or f'server returned {status}'}", file=sys.stderr)
        return _Outcome(exit_code=1)
    return _Outcome()


async def _stream_once(
    server_client: httpx2.AsyncClient,
    params: dict[str, str | int],
    options: ListenOptions,
    state: _StreamState,
) -> _Outcome:
    await asyncio.sleep(0)
    state.ready = False
    async with server_client.stream("GET", "/events/stream", params=params) as response:
        outcome = await _response_outcome(response)
        if outcome.exit_code is not None or outcome.retry_reason is not None:
            return outcome
        return await _consume_stream(response, options, state)


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

    state = _StreamState(remaining=count)
    backoff = 1.0
    while True:
        try:
            outcome = await _stream_once(server_client, params, options, state)
        except httpx2.HTTPError as error:
            outcome = _Outcome(retry_reason=str(error) or type(error).__name__)
        if state.ready:
            backoff = 1.0
        if outcome.exit_code is not None:
            return outcome.exit_code

        if (
            options.until is not None
            and github_client is not None
            and options.number is not None
            and await satisfied_by_poll(options.until, github_client, options.repo, options.number)
        ):
            return 0
        delay = random.uniform(0.8, 1.2) * backoff  # ruff:ignore[suspicious-non-cryptographic-random-usage]
        reason = outcome.retry_reason or "stream disconnected"
        print(
            f"warning: disconnected ({reason}); events during the gap are lost; reconnecting in {delay:.1f}s",
            file=sys.stderr,
        )
        await asyncio.sleep(delay)
        backoff = min(backoff * 2, 30)


async def listen(
    options: ListenOptions,
    client_factory: Callable[..., httpx2.AsyncClient] = httpx2.AsyncClient,
) -> int:
    """Listen for matching server events until an exit condition is met."""
    _install_httpcore2_shutdown_workaround(asyncio.get_running_loop())
    await asyncio.sleep(0)
    events, count = _validated(options)
    settings = get_settings()
    if client_factory is httpx2.AsyncClient:
        _guard_token_transport(options.server, insecure=settings.insecure)
    token = resolve_token()
    # The server emits keepalives, so a bounded read timeout detects half-dead streams.
    server_timeout = httpx2.Timeout(
        connect=settings.server_timeout,
        read=settings.stream_timeout,
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
