"""Tests for terminal-state checks."""

import httpx
import pytest
import respx

from gh_babysitter.cli.until import UNTIL_MATRIX, satisfied_by_event, satisfied_by_poll


def envelope(event, action, payload):
    return {"event": event, "action": action, "payload": payload}


def test_until_matrix_declares_required_subscription_events():
    assert UNTIL_MATRIX == {
        "merged": frozenset({"pull_request"}),
        "closed": frozenset({"pull_request", "issues"}),
        "approved": frozenset({"pull_request_review"}),
        "changes_requested": frozenset({"pull_request_review"}),
    }


@pytest.mark.parametrize(
    ("until", "event"),
    [
        ("merged", envelope("pull_request", "closed", {"pull_request": {"merged": True}})),
        ("closed", envelope("issues", "closed", {})),
        ("closed", envelope("pull_request", "closed", {})),
        ("approved", envelope("pull_request_review", "submitted", {"review": {"state": "approved"}})),
        (
            "changes_requested",
            envelope("pull_request_review", "submitted", {"review": {"state": "changes_requested"}}),
        ),
    ],
)
def test_satisfied_by_event_matches_terminal_event(until, event):
    assert satisfied_by_event(until, event)


@pytest.mark.parametrize(
    ("until", "event"),
    [
        ("merged", envelope("pull_request", "closed", {"pull_request": {"merged": False}})),
        ("closed", envelope("issue_comment", "closed", {})),
        ("approved", envelope("pull_request_review", "edited", {"review": {"state": "approved"}})),
        (
            "changes_requested",
            envelope("pull_request_review", "submitted", {"review": {"state": "approved"}}),
        ),
    ],
)
def test_satisfied_by_event_rejects_non_terminal_event(until, event):
    assert not satisfied_by_event(until, event)


@respx.mock
@pytest.mark.parametrize(
    ("until", "path", "response", "expected"),
    [
        ("merged", "/repos/octo/repo/pulls/42", {"merged": True}, True),
        ("merged", "/repos/octo/repo/pulls/42", {"merged": False}, False),
        ("closed", "/repos/octo/repo/issues/42", {"state": "closed"}, True),
        ("closed", "/repos/octo/repo/issues/42", {"state": "open"}, False),
        ("approved", "/repos/octo/repo/pulls/42/reviews", [{"state": "APPROVED"}], True),
        ("approved", "/repos/octo/repo/pulls/42/reviews", [{"state": "COMMENTED"}], False),
        (
            "changes_requested",
            "/repos/octo/repo/pulls/42/reviews",
            [{"state": "CHANGES_REQUESTED"}],
            True,
        ),
    ],
)
async def test_satisfied_by_poll_checks_github_state(until, path, response, expected):
    respx.get(f"https://api.github.com{path}").mock(return_value=httpx.Response(200, json=response))

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        result = await satisfied_by_poll(until, client, "octo/repo", 42)

    assert result is expected
