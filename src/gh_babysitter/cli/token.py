"""GitHub token resolution for CLI commands."""

import os
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys

import typer


def resolve_token() -> str:
    """Resolve a GitHub token from standard environment variables or ``gh``."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if value := os.getenv(name):
            return value
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],  # ruff:ignore[start-process-with-partial-path]
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        if value := result.stdout.strip():
            return value
    except FileNotFoundError, subprocess.CalledProcessError:
        pass
    print("GitHub token unavailable; run `gh auth login` first.", file=sys.stderr)
    raise typer.Exit(1)
