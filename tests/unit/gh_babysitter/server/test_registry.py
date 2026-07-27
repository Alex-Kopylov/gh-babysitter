"""Tests for the in-memory connection registry."""

import asyncio

from gh_babysitter.server.normalize import Normalized
from gh_babysitter.server.registry import Filter, Registry, Subscriber


def test_subscriber_counts_and_resets_dropped_events():
    queue = asyncio.Queue(maxsize=1)
    subscriber = Subscriber(queue)

    assert subscriber.offer({"number": 1})
    assert not subscriber.offer({"number": 2})
    assert subscriber.take_dropped() == 1
    assert subscriber.take_dropped() == 0
    assert queue.get_nowait() == {"number": 1}


def test_match_checks_repo_event_action_and_number():
    registry = Registry()
    subscriber = Subscriber(asyncio.Queue())
    connection_id = registry.register(
        "octocat",
        [Filter(repo="octo/repo", event="issues", action="opened", number=42)],
        subscriber,
    )

    assert registry.connections[connection_id].subscriber is subscriber
    assert registry.match(Normalized("octo/repo", "issues", "opened", 42)) == [subscriber]
    assert registry.match(Normalized("other/repo", "issues", "opened", 42)) == []
    assert registry.match(Normalized("octo/repo", "release", "opened", 42)) == []
    assert registry.match(Normalized("octo/repo", "issues", "closed", 42)) == []
    assert registry.match(Normalized("octo/repo", "issues", "opened", 41)) == []


def test_optional_filter_fields_are_wildcards():
    registry = Registry()
    subscriber = Subscriber(asyncio.Queue())
    registry.register(
        "octocat",
        [Filter(repo="octo/repo", event="issues", action=None, number=None)],
        subscriber,
    )

    assert registry.match(Normalized("octo/repo", "issues", "closed", 42)) == [subscriber]


def test_overlapping_filters_deliver_once_per_connection():
    registry = Registry()
    subscriber = Subscriber(asyncio.Queue())
    registry.register(
        "octocat",
        [
            Filter(repo="octo/repo", event="issues", action=None, number=None),
            Filter(repo="octo/repo", event="issues", action="opened", number=42),
        ],
        subscriber,
    )

    assert registry.match(Normalized("octo/repo", "issues", "opened", 42)) == [subscriber]


def test_unregister_is_idempotent_and_removes_empty_index_entries():
    registry = Registry()
    connection_id = registry.register(
        "octocat",
        [Filter(repo="octo/repo", event="issues")],
        Subscriber(asyncio.Queue()),
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
        Subscriber(asyncio.Queue()),
    )

    registry.unregister(connection_id)

    assert registry.connections == {}
    assert registry.index == {}
