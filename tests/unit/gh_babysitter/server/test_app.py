"""Unit tests for FastAPI application validation."""

import pytest
from fastapi import HTTPException

from gh_babysitter.server.app import (
    _bearer_token,
    _event_names,
    _valid_repo,
    create_app,
)
from gh_babysitter.server.config import Settings


@pytest.mark.parametrize("header", ["Basic token", "Bearer"])
def test_bearer_token_rejects_missing_or_malformed_header(header):
    with pytest.raises(HTTPException) as error:
        _bearer_token(header)

    assert error.value.status_code == 401


def test_event_names_accepts_supported_comma_list():
    assert _event_names("issues, pull_request") == ["issues", "pull_request"]


@pytest.mark.parametrize("events", ["", "issues,"])
def test_event_names_rejects_empty_or_unsupported_values(events):
    with pytest.raises(HTTPException) as error:
        _event_names(events)

    assert error.value.status_code == 422


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ("/repo", False),
        ("octo/", False),
        ("octo/repo/extra", False),
    ],
)
def test_valid_repo_requires_owner_and_name(repo, expected):
    assert _valid_repo(repo) is expected


async def test_lifespan_owns_default_authenticator_http_client():
    app = create_app(Settings(webhook_secret="secret"))

    async with app.router.lifespan_context(app):
        client = app.state.authenticator.client
        assert not client.is_closed

    assert client.is_closed
