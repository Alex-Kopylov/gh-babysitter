"""Uvicorn server entry point."""

import sys

import uvicorn

from gh_babysitter.server.app import create_app
from gh_babysitter.server.config import get_settings


def run(host: str, port: int) -> None:
    """Run the gh-babysitter server."""
    settings = get_settings()
    if not settings.webhook_secret:
        print(
            "warning: GH_BABYSITTER_WEBHOOK_SECRET is unset; webhooks will be rejected",
            file=sys.stderr,
        )
    # SSE generators do not finish on their own, so bound uvicorn's graceful wait.
    uvicorn.run(
        create_app(settings),
        host=host,
        port=port,
        timeout_graceful_shutdown=5,
    )
