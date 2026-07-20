"""Tests for the in-memory connection registry."""

import asyncio

from gh_babysitter.server.normalize import Normalized
from gh_babysitter.server.registry import Filter, Registry


def test_match_checks_repo_event_action_and_number():
    registry = Registry()
    queue = asyncio.Queue()
    registry.register(
        "octocat",
        [Filter(repo="octo/repo", event="issues", action="opened", number=42)],
        queue,
    )

    assert registry.match(Normalized("octo/repo", "issues", "opened", 42)) == [queue]
    assert registry.match(Normalized("other/repo", "issues", "opened", 42)) == []
    assert registry.match(Normalized("octo/repo", "release", "opened", 42)) == []
    assert registry.match(Normalized("octo/repo", "issues", "closed", 42)) == []
    assert registry.match(Normalized("octo/repo", "issues", "opened", 41)) == []


def test_optional_filter_fields_are_wildcards():
    registry = Registry()
    queue = asyncio.Queue()
    registry.register(
        "octocat",
        [Filter(repo="octo/repo", event="issues", action=None, number=None)],
        queue,
    )

    assert registry.match(Normalized("octo/repo", "issues", "closed", 42)) == [queue]


def test_overlapping_filters_deliver_once_per_connection():
    registry = Registry()
    queue = asyncio.Queue()
    registry.register(
        "octocat",
        [
            Filter(repo="octo/repo", event="issues", action=None, number=None),
            Filter(repo="octo/repo", event="issues", action="opened", number=42),
        ],
        queue,
    )

    assert registry.match(Normalized("octo/repo", "issues", "opened", 42)) == [queue]


def test_unregister_is_idempotent_and_removes_empty_index_entries():
    registry = Registry()
    queue = asyncio.Queue()
    connection_id = registry.register(
        "octocat",
        [Filter(repo="octo/repo", event="issues")],
        queue,
    )

    registry.unregister(connection_id)
    registry.unregister(connection_id)

    assert registry.connections == {}
    assert registry.index == {}
    assert registry.match(Normalized("octo/repo", "issues", None, None)) == []


def test_unregister_handles_overlapping_filters_with_same_index_key():
    registry = Registry()
    connection_id = registry.register(
        "octocat",
        [
            Filter(repo="octo/repo", event="issues"),
            Filter(repo="octo/repo", event="issues", action="opened"),
        ],
        asyncio.Queue(),
    )

    registry.unregister(connection_id)

    assert registry.connections == {}
    assert registry.index == {}
