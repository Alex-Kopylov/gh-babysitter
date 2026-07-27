"""In-memory registry for active SSE connections."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    from gh_babysitter.server.normalize import Normalized


@dataclass(frozen=True)
class Filter:
    """A subscription filter belonging to one SSE connection."""

    repo: str
    event: str
    action: str | None = None
    number: int | None = None


@dataclass
class Subscriber:
    """Bounded event queue with delivery-loss accounting."""

    queue: asyncio.Queue[dict[str, Any]]
    dropped: int = 0

    def offer(self, envelope: dict[str, Any]) -> bool:
        """Enqueue an event and report whether delivery succeeded."""
        try:
            self.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.dropped += 1
            return False
        return True

    def take_dropped(self) -> int:
        """Return and reset the pending delivery-loss count."""
        count, self.dropped = self.dropped, 0
        return count


@dataclass(frozen=True)
class Connection:
    """An authenticated SSE connection and its event filters."""

    login: str
    filters: tuple[Filter, ...]
    subscriber: Subscriber


class Registry:
    """Index active connections by repository and event."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self.connections: dict[int, Connection] = {}
        self.index: dict[tuple[str, str], set[int]] = {}
        self._next_id = 1

    def register(
        self,
        login: str,
        filters: list[Filter],
        subscriber: Subscriber,
    ) -> int:
        """Register one connection and return its identifier."""
        connection_id = self._next_id
        self._next_id += 1
        connection = Connection(login, tuple(filters), subscriber)
        self.connections[connection_id] = connection
        for event_filter in connection.filters:
            self.index.setdefault((event_filter.repo, event_filter.event), set()).add(connection_id)
        return connection_id

    def unregister(self, connection_id: int) -> None:
        """Remove a connection if it remains registered."""
        connection = self.connections.pop(connection_id, None)
        if connection is None:
            return

        for key in {(event_filter.repo, event_filter.event) for event_filter in connection.filters}:
            connection_ids = self.index[key]
            connection_ids.discard(connection_id)
            if not connection_ids:
                del self.index[key]

    def match(self, norm: Normalized) -> list[Subscriber]:
        """Return one subscriber per connection matching a normalized event."""
        subscribers = []
        for connection_id in sorted(self.index.get((norm.repo, norm.event), ())):
            connection = self.connections[connection_id]
            if any(
                (event_filter.action is None or event_filter.action == norm.action)
                and (event_filter.number is None or event_filter.number == norm.number)
                for event_filter in connection.filters
                if event_filter.repo == norm.repo and event_filter.event == norm.event
            ):
                subscribers.append(connection.subscriber)
        return subscribers
