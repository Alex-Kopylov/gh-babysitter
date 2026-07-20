"""In-memory registry for active SSE connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from typing import Any

    from gh_babysitter.server.normalize import Normalized


@dataclass(frozen=True)
class Filter:
    """A subscription filter belonging to one SSE connection."""

    repo: str
    event: str
    action: str | None = None
    number: int | None = None


@dataclass(frozen=True)
class Connection:
    """An authenticated SSE connection and its event filters."""

    login: str
    filters: tuple[Filter, ...]
    queue: asyncio.Queue[dict[str, Any]]


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
        queue: asyncio.Queue[dict[str, Any]],
    ) -> int:
        """Register one connection and return its identifier."""
        connection_id = self._next_id
        self._next_id += 1
        connection = Connection(login, tuple(filters), queue)
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

    def match(self, norm: Normalized) -> list[asyncio.Queue[dict[str, Any]]]:
        """Return one queue per connection matching a normalized event."""
        queues = []
        for connection_id in sorted(self.index.get((norm.repo, norm.event), ())):
            connection = self.connections[connection_id]
            if any(
                (event_filter.action is None or event_filter.action == norm.action)
                and (event_filter.number is None or event_filter.number == norm.number)
                for event_filter in connection.filters
                if event_filter.repo == norm.repo and event_filter.event == norm.event
            ):
                queues.append(connection.queue)
        return queues
