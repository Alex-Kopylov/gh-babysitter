"""Tests for GitHub authentication and caching."""

import httpx
import pytest
import respx

from gh_babysitter.server import auth as auth_module
from gh_babysitter.server.auth import GitHubAuthenticator


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as value:
        yield value


@respx.mock
async def test_verify_returns_login_for_visible_repository(client):
    user = respx.get("https://api.example/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    repo = respx.get("https://api.example/repos/octo/repo").mock(return_value=httpx.Response(200, json={}))
    authenticator = GitHubAuthenticator("https://api.example", 300, client)

    assert await authenticator.verify("token", "octo/repo") == "octocat"
    assert user.calls.last.request.headers["accept"] == "application/vnd.github+json"
    assert repo.calls.last.request.headers["authorization"] == "Bearer token"


@respx.mock
async def test_verify_returns_none_for_invalid_token(client):
    respx.get("https://api.example/user").mock(return_value=httpx.Response(401))
    repo = respx.get("https://api.example/repos/octo/repo")
    authenticator = GitHubAuthenticator("https://api.example", 300, client)

    assert await authenticator.verify("bad", "octo/repo") is None
    assert not repo.called


@respx.mock
async def test_verify_returns_none_for_hidden_repository(client):
    respx.get("https://api.example/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    respx.get("https://api.example/repos/octo/repo").mock(return_value=httpx.Response(404))
    authenticator = GitHubAuthenticator("https://api.example", 300, client)

    assert await authenticator.verify("token", "octo/repo") is None


@respx.mock
async def test_cache_hit_avoids_second_github_call(client):
    user = respx.get("https://api.example/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    repo = respx.get("https://api.example/repos/octo/repo").mock(return_value=httpx.Response(200))
    authenticator = GitHubAuthenticator("https://api.example", 300, client)

    assert await authenticator.verify("token", "octo/repo") == "octocat"
    assert await authenticator.verify("token", "octo/repo") == "octocat"
    assert user.call_count == repo.call_count == 1


@respx.mock
async def test_expired_cache_calls_github_again(client, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(auth_module, "monotonic", lambda: now[0])
    user = respx.get("https://api.example/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    repo = respx.get("https://api.example/repos/octo/repo").mock(return_value=httpx.Response(200))
    authenticator = GitHubAuthenticator("https://api.example", 5, client)

    await authenticator.verify("token", "octo/repo")
    now[0] = 6
    await authenticator.verify("token", "octo/repo")

    assert user.call_count == repo.call_count == 2


@respx.mock
async def test_fresh_bypasses_and_refreshes_cache(client):
    user = respx.get("https://api.example/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    repo = respx.get("https://api.example/repos/octo/repo").mock(return_value=httpx.Response(200))
    authenticator = GitHubAuthenticator("https://api.example", 300, client)

    await authenticator.verify("token", "octo/repo")
    await authenticator.verify("token", "octo/repo", fresh=True)
    await authenticator.verify("token", "octo/repo")

    assert user.call_count == repo.call_count == 2
