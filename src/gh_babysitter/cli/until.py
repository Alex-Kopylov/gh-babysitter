"""Terminal-state checks for ``listen --until``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx2

UNTIL_MATRIX: dict[str, frozenset[str]] = {
    "merged": frozenset({"pull_request"}),
    "closed": frozenset({"pull_request", "issues"}),
    "approved": frozenset({"pull_request_review"}),
    "changes_requested": frozenset({"pull_request_review"}),
}


def satisfied_by_event(until: str, envelope: dict[str, Any]) -> bool:
    """Return whether an event envelope satisfies a terminal condition."""
    event = envelope.get("event")
    action = envelope.get("action")
    payload = envelope.get("payload", {})
    if until == "merged":
        return event == "pull_request" and action == "closed" and payload.get("pull_request", {}).get("merged") is True
    if until == "closed":
        return event in UNTIL_MATRIX["closed"] and action == "closed"
    state = "approved" if until == "approved" else "changes_requested"
    return event == "pull_request_review" and action == "submitted" and payload.get("review", {}).get("state") == state


async def satisfied_by_poll(until: str, client: httpx2.AsyncClient, repo: str, number: int) -> bool:
    """Poll GitHub for whether an object already reached a terminal state."""
    if until == "merged":
        response = await client.get(f"/repos/{repo}/pulls/{number}")
        response.raise_for_status()
        return response.json().get("merged") is True
    if until == "closed":
        response = await client.get(f"/repos/{repo}/issues/{number}")
        response.raise_for_status()
        return response.json().get("state") == "closed"

    response = await client.get(f"/repos/{repo}/pulls/{number}/reviews")
    response.raise_for_status()
    state = "APPROVED" if until == "approved" else "CHANGES_REQUESTED"
    return any(review.get("state") == state for review in response.json())
