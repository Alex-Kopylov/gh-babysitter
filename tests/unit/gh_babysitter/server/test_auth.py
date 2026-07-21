"""Tests for GitHub authentication and caching."""

import httpx2

from gh_babysitter.server import auth as auth_module
from gh_babysitter.server.auth import GitHubAuthenticator


def make_client(*, user_status=200, repo_status=200):
    requests = []

    def handler(request):
        assert request.method == "GET"
        requests.append(request)
        if request.url.path == "/user":
            return httpx2.Response(user_status, json={"login": "octocat"})
        assert request.url.path == "/repos/octo/repo"
        return httpx2.Response(repo_status)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), requests


async def test_verify_returns_login_for_visible_repository():
    client, requests = make_client()
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == "octocat"

    assert requests[0].headers["accept"] == "application/vnd.github+json"
    assert requests[1].headers["authorization"] == "Bearer token"


async def test_verify_returns_none_for_invalid_token():
    client, requests = make_client(user_status=401)
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("bad", "octo/repo") is None

    assert [request.url.path for request in requests] == ["/user"]


async def test_verify_returns_none_for_hidden_repository():
    client, _ = make_client(repo_status=404)
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") is None


async def test_cache_hit_avoids_second_github_call():
    client, requests = make_client()
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == "octocat"
        assert await authenticator.verify("token", "octo/repo") == "octocat"

    assert [request.url.path for request in requests] == ["/user", "/repos/octo/repo"]


async def test_expired_cache_calls_github_again(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(auth_module, "monotonic", lambda: now[0])
    client, requests = make_client()
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 5, client)

        await authenticator.verify("token", "octo/repo")
        now[0] = 6
        await authenticator.verify("token", "octo/repo")

    assert len(requests) == 4


async def test_fresh_bypasses_and_refreshes_cache():
    client, requests = make_client()
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        await authenticator.verify("token", "octo/repo")
        await authenticator.verify("token", "octo/repo", fresh=True)
        await authenticator.verify("token", "octo/repo")

    assert len(requests) == 4
