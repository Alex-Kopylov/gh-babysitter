"""Tests for supported webhook events and number extraction."""

import pytest

from gh_babysitter.server.events import EVENT_MENU, extract_number


def test_event_menu_is_exact():
    assert EVENT_MENU == frozenset(
        {
            "issues",
            "pull_request",
            "issue_comment",
            "pull_request_review",
            "release",
        }
    )


@pytest.mark.parametrize(
    ("event", "payload", "expected"),
    [
        ("issues", {"issue": {"number": 1}}, 1),
        ("issue_comment", {"issue": {"number": 2, "pull_request": {}}}, 2),
        ("pull_request", {"pull_request": {"number": 3}}, 3),
        ("pull_request_review", {"pull_request": {"number": 4}}, 4),
        ("release", {"release": {"number": 5}}, None),
    ],
)
def test_extract_number_follows_normalization_table(event, payload, expected):
    assert extract_number(event, payload) == expected


@pytest.mark.parametrize("event", ["issues", "issue_comment", "pull_request"])
def test_extract_number_returns_none_when_subject_is_missing(event):
    assert extract_number(event, {}) is None


class TestExtractNumberSubjectSelection:
    """Issue and pull-request event families select their own subject."""

    @pytest.mark.parametrize(
        ("event", "expected"),
        [
            ("issues", 41),
            ("issue_comment", 41),
            ("pull_request", 42),
            ("pull_request_review", 42),
        ],
    )
    def test_payload_with_both_subjects_uses_event_family(self, event, expected):
        """Avoid selecting the opposite subject when both objects are present."""
        payload = {
            "issue": {"number": 41},
            "pull_request": {"number": 42},
        }

        assert extract_number(event, payload) == expected
