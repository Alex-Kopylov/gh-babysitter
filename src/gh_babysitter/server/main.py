"""Uvicorn server entry point."""

import sys

import uvicorn

from gh_babysitter.server.app import create_app
from gh_babysitter.server.config import Settings


def run(host: str, port: int) -> None:
    """Run the gh-babysitter server."""
    settings = Settings.from_env()
    if not settings.webhook_secret:
        print(
            "warning: GH_BABYSITTER_WEBHOOK_SECRET is unset; webhooks will be rejected",
            file=sys.stderr,
        )
    uvicorn.run(create_app(settings), host=host, port=port)
