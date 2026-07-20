"""Supported GitHub webhook events and number extraction."""

from typing import Any

EVENT_MENU: frozenset[str] = frozenset(
    {
        "issues",
        "pull_request",
        "issue_comment",
        "pull_request_review",
        "release",
    }
)


def extract_number(event: str, payload: dict[str, Any]) -> int | None:
    """Extract the issue or pull-request number for a webhook event."""
    if event in {"issues", "issue_comment"}:
        subject = payload.get("issue")
    elif event in {"pull_request", "pull_request_review"}:
        subject = payload.get("pull_request")
    else:
        return None

    if not isinstance(subject, dict):
        return None
    number = subject.get("number")
    return number if isinstance(number, int) else None
