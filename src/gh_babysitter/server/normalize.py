"""GitHub webhook payload normalization."""

from dataclasses import dataclass
from typing import Any

from gh_babysitter.server.events import extract_number


@dataclass(frozen=True)
class Normalized:
    """Fields used to match a webhook event to active filters."""

    repo: str
    event: str
    action: str | None
    number: int | None


def normalize(event: str, payload: dict[str, Any]) -> Normalized | None:
    """Normalize a GitHub payload, or return ``None`` when its repo is absent."""
    repository = payload.get("repository")
    if not isinstance(repository, dict) or not repository.get("full_name"):
        return None

    return Normalized(
        repo=repository["full_name"],
        event=event,
        action=payload.get("action"),
        number=extract_number(event, payload),
    )
