"""Minimal Server-Sent Events parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator


async def parse_sse(  # noqa: ASYNC900 - SSE parsing intentionally streams results.
    lines: AsyncIterable[bytes | str],
) -> AsyncIterator[tuple[str, str]]:
    """Yield event type and joined data from an asynchronous line stream."""
    event_type = "message"
    data: list[str] = []
    async for raw_line in lines:
        line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r\n")
        if not line:
            if data:
                yield event_type, "\n".join(data)
            event_type = "message"
            data.clear()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_type = value or "message"
        elif field == "data":
            data.append(value)
