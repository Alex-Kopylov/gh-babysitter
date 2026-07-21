"""Typer command wiring for gh-babysitter."""

import asyncio
from typing import Annotated

import typer

from gh_babysitter.cli.config import get_settings
from gh_babysitter.cli.durations import parse_duration
from gh_babysitter.cli.listen import ListenOptions, listen
from gh_babysitter.cli.setup import setup_webhook
from gh_babysitter.server.main import run as run_server

app = typer.Typer(no_args_is_help=True)


@app.command("listen")
def listen_command(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]
    repo: Annotated[str, typer.Option("-R", "--repo")],
    server: Annotated[
        str,
        typer.Option("--server", default_factory=lambda: get_settings().server),
    ],
    events: Annotated[str | None, typer.Option("-E", "--events")] = None,
    number: Annotated[int | None, typer.Option("-n", "--number")] = None,
    action: Annotated[str | None, typer.Option("--action")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    timeout: Annotated[str | None, typer.Option("--timeout")] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
    first_event: Annotated[bool, typer.Option("--first-event")] = False,
    output_format: Annotated[str, typer.Option("--format")] = "json",
) -> None:
    """Stream matching GitHub events."""
    timeout_seconds = parse_duration(timeout) if timeout is not None else None
    exit_code = asyncio.run(
        listen(
            ListenOptions(
                repo=repo,
                events=events,
                number=number,
                action=action,
                until=until,
                timeout=timeout_seconds,
                count=count,
                first_event=first_event,
                server=server,
                format=output_format,
            )
        )
    )
    if exit_code:
        raise typer.Exit(exit_code)


@app.command("setup")
def setup_command(
    org: Annotated[str, typer.Option("--org")],
    url: Annotated[str, typer.Option("--url")],
    events: Annotated[str | None, typer.Option("-E", "--events")] = None,
    secret: Annotated[str | None, typer.Option("--secret")] = None,
) -> None:
    """Create or update an organization webhook."""
    asyncio.run(setup_webhook(org=org, url=url, events=events, secret=secret))


@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
) -> None:
    """Run the gh-babysitter server."""
    run_server(host, port)


def main() -> None:
    """Run the command-line application."""
    app()
