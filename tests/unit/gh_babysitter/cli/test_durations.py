"""Tests for CLI duration parsing."""

import pytest
import typer

from gh_babysitter.cli.durations import parse_duration


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("12h", 43_200.0),
        ("90m", 5_400.0),
        ("45s", 45.0),
        ("1h30m", 5_400.0),
        ("300", 300.0),
    ],
)
def test_parse_duration_returns_seconds(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("value", ["", "1x", "1h30", "1.5h", "-1"])
def test_parse_duration_rejects_invalid_values(value):
    with pytest.raises(typer.BadParameter, match="invalid duration"):
        parse_duration(value)


@pytest.mark.parametrize("value", ["0", "0s"])
def test_parse_duration_rejects_zero(value):
    with pytest.raises(typer.BadParameter, match="--timeout must be greater than zero"):
        parse_duration(value)
