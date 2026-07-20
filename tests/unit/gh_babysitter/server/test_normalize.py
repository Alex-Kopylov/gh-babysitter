"""Tests for webhook normalization."""

import pytest

from gh_babysitter.server.normalize import Normalized, normalize


@pytest.mark.parametrize(
    ("event", "object_name", "number"),
    [
        ("issues", "issue", 1),
        ("pull_request", "pull_request", 2),
        ("issue_comment", "issue", 3),
        ("pull_request_review", "pull_request", 4),
        ("release", "release", None),
    ],
)
def test_normalize_event_menu_numbers(event, object_name, number):
    payload = {
        "repository": {"full_name": "octo/repo"},
        "action": "opened",
        object_name: {"number": number or 99},
    }

    assert normalize(event, payload) == Normalized(
        repo="octo/repo",
        event=event,
        action="opened",
        number=number,
    )


def test_issue_comment_on_pull_request_uses_issue_number():
    payload = {
        "repository": {"full_name": "octo/repo"},
        "action": "created",
        "issue": {"number": 42, "pull_request": {"url": "https://example.test"}},
    }

    assert normalize("issue_comment", payload).number == 42


def test_missing_action_is_preserved_as_none():
    payload = {"repository": {"full_name": "octo/repo"}, "release": {}}

    assert normalize("release", payload).action is None


@pytest.mark.parametrize("payload", [{}, {"repository": {}}, {"repository": None}])
def test_missing_repository_returns_none(payload):
    assert normalize("issues", payload) is None
