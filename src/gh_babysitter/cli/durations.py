"""CLI duration parsing."""

import re

import typer

_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_duration(value: str) -> float:
    """Parse a duration into seconds."""
    if value.isdigit():
        return float(value)
    match = _DURATION.fullmatch(value)
    if match is None or not any(match.groups()):
        message = f"invalid duration: {value!r}"
        raise typer.BadParameter(message)
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)
