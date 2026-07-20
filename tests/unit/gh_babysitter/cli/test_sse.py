"""Tests for the minimal SSE parser."""

from gh_babysitter.cli.sse import parse_sse


async def byte_lines(*lines):
    for line in lines:
        yield line


async def test_parse_sse_joins_multiline_data_and_uses_event_type():
    messages = [
        message
        async for message in parse_sse(
            byte_lines(
                b"event: ready\r\n",
                b"data: first\n",
                b"data:second\n",
                b"\n",
            )
        )
    ]

    assert messages == [("ready", "first\nsecond")]


async def test_parse_sse_ignores_comments_and_unknown_fields():
    messages = [
        message
        async for message in parse_sse(
            byte_lines(
                b": keepalive\n",
                b"id: 12\n",
                b"data: payload\n",
                b"\n",
            )
        )
    ]

    assert messages == [("message", "payload")]


async def test_parse_sse_does_not_dispatch_without_data_or_blank_line():
    messages = [message async for message in parse_sse(byte_lines(b"event: ready\n", b"\n", b"data: partial\n"))]

    assert messages == []
