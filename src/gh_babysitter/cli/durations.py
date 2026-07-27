"""CLI duration parsing."""

import re

import typer

_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_duration(value: str) -> float:
    """Parse a duration into seconds."""
    if value.isdigit():
        duration = float(value)
    else:
        match = _DURATION.fullmatch(value)
        if match is None or not any(match.groups()):
            message = f"invalid duration: {value!r}"
            raise typer.BadParameter(message)
        hours, minutes, seconds = (int(part or 0) for part in match.groups())
        duration = float(hours * 3600 + minutes * 60 + seconds)
    if duration <= 0:
        message = "--timeout must be greater than zero"
        raise typer.BadParameter(message)
    return duration
