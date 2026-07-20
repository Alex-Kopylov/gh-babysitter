"""FastAPI application for webhook ingress and SSE delivery."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from gh_babysitter.server.auth import Authenticator, GitHubAuthenticator
from gh_babysitter.server.events import EVENT_MENU
from gh_babysitter.server.normalize import normalize
from gh_babysitter.server.registry import Filter, Registry
from gh_babysitter.server.signature import verify_signature

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from gh_babysitter.server.config import Settings


def _bearer_token(authorization: str | None) -> str:
    """Extract a required bearer token from the authorization header."""
    scheme, separator, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    return token


def _event_names(events: str) -> list[str]:
    """Parse and validate a comma-separated event menu selection."""
    names = [name.strip() for name in events.split(",")]
    if not names or any(not name or name not in EVENT_MENU for name in names):
        raise HTTPException(status_code=422, detail="Invalid events")
    return names


def _valid_repo(repo: str) -> bool:
    """Return whether a repository has the required ``owner/name`` shape."""
    return repo.count("/") == 1 and all(repo.split("/"))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the default authenticator's HTTP client."""
    if app.state.authenticator is not None:
        yield
        return
    settings: Settings = app.state.settings
    async with httpx.AsyncClient() as client:
        app.state.authenticator = GitHubAuthenticator(settings.github_api_url, settings.auth_cache_ttl, client)
        yield


async def _webhook(request: Request) -> Response:
    """Verify, normalize, and dispatch one GitHub webhook."""
    settings: Settings = request.app.state.settings
    body = await request.body()
    if not settings.webhook_secret or not verify_signature(
        settings.webhook_secret,
        body,
        request.headers.get("X-Hub-Signature-256"),
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")
    github_event = request.headers.get("X-GitHub-Event", "")
    if github_event == "ping":
        return JSONResponse({"ok": True})

    payload: dict[str, Any] = await request.json()
    norm = normalize(github_event, payload)
    if norm is None:
        return Response(status_code=204)

    envelope = {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "repo": norm.repo,
        "event": norm.event,
        "action": norm.action,
        "number": norm.number,
        "payload": payload,
    }
    queues = request.app.state.registry.match(norm)
    for queue in queues:
        with suppress(asyncio.QueueFull):
            queue.put_nowait(envelope)
    return JSONResponse({"matched": len(queues)}, status_code=202)


async def _stream(
    request: Request,
    repo: Annotated[str, Query()],
    events: Annotated[str, Query()],
    number: Annotated[int | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
) -> Response:
    """Open an authenticated SSE stream for the requested filters."""
    token = _bearer_token(request.headers.get("Authorization"))
    if not _valid_repo(repo):
        raise HTTPException(status_code=422, detail="Invalid repository")
    event_names = _event_names(events)
    authenticator = request.app.state.authenticator
    if authenticator is None:
        raise RuntimeError
    login = await authenticator.verify(token, repo)
    if login is None:
        raise HTTPException(status_code=403, detail="Repository access denied")

    settings: Settings = request.app.state.settings
    registry: Registry = request.app.state.registry
    filters = [Filter(repo, event, action, number) for event in event_names]
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=settings.queue_maxsize)
    connection_id = registry.register(login, filters, queue)

    async def event_generator() -> AsyncIterator[dict[str, str]]:  # noqa: ASYNC900 - SSE requires streaming.
        await asyncio.sleep(0)
        next_recheck = asyncio.get_running_loop().time() + settings.recheck_interval
        try:
            yield {
                "event": "ready",
                "data": json.dumps(
                    {"filters": [asdict(event_filter) for event_filter in filters]},
                    separators=(",", ":"),
                ),
            }
            while True:
                timeout = max(0, next_recheck - asyncio.get_running_loop().time())
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    if await authenticator.verify(token, repo, fresh=True) is None:
                        return
                    next_recheck = asyncio.get_running_loop().time() + settings.recheck_interval
                else:
                    yield {"data": json.dumps(envelope, separators=(",", ":"))}
        finally:
            registry.unregister(connection_id)

    return EventSourceResponse(event_generator(), ping=settings.ping_interval)


def create_app(
    settings: Settings,
    registry: Registry | None = None,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    """Create the server application with optional test dependencies."""
    app = FastAPI(lifespan=_lifespan)
    app.state.settings = settings
    app.state.registry = registry or Registry()
    app.state.authenticator = authenticator
    app.add_api_route("/webhook", _webhook, methods=["POST"])
    app.add_api_route("/events/stream", _stream, methods=["GET"])
    return app
