"""Shared pytest fixtures and deterministic payload builders."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from gh_babysitter.cli import listen


@pytest.fixture
def fake_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make CLI listen tests resolve a deterministic token."""
    token = "token"
    monkeypatch.setattr(listen, "resolve_token", lambda: token)
    return token


@pytest.fixture
def deterministic_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Disable retry waits and return the requested nonzero delays."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        listen.asyncio,
        "sleep",
        AsyncMock(side_effect=lambda delay: sleeps.append(delay) if delay else None),
    )
    monkeypatch.setattr(listen.random, "uniform", lambda _low, _high: 1.0)
    return sleeps


@pytest.fixture
def envelope() -> Callable[..., dict[str, Any]]:
    """Build a representative webhook envelope with optional overrides."""

    def build(**overrides: Any) -> dict[str, Any]:
        value = {
            "ts": "2026-07-20T12:00:00Z",
            "repo": "octo/repo",
            "event": "issues",
            "action": "opened",
            "number": 42,
            "payload": {},
        }
        value.update(overrides)
        return value

    return build


@pytest.fixture
def sse_body() -> Callable[..., str]:
    """Join complete SSE frames with protocol frame separators."""

    def build(*frames: str) -> str:
        return f"{'\n\n'.join(frames)}\n\n"

    return build
