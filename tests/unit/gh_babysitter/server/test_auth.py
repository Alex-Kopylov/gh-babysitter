"""Tests for GitHub authentication and caching."""

import httpx2
import pytest

from gh_babysitter.server import auth as auth_module
from gh_babysitter.server.auth import Access, GitHubAuthenticator, Verdict


def test_access_carries_an_explicit_authorization_verdict():
    access = auth_module.Access(auth_module.Verdict.ALLOWED, login="octocat")

    assert access.verdict is auth_module.Verdict.ALLOWED
    assert access.login == "octocat"


def make_client(*, user_status=200, repo_status=200, user_login="octocat"):
    requests = []

    def handler(request):
        assert request.method == "GET"
        requests.append(request)
        if request.url.path == "/user":
            return httpx2.Response(user_status, json={"login": user_login})
        assert request.url.path == "/repos/octo/repo"
        return httpx2.Response(repo_status)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), requests


def make_recovering_client(*, failure_path, failure_status=None, failure_headers=None):
    requests = []
    recovered = [False]

    def handler(request):
        assert request.method == "GET"
        requests.append(request)
        if not recovered[0] and request.url.path == failure_path:
            if failure_status is None:
                raise httpx2.ConnectError("GitHub is unreachable", request=request)
            return httpx2.Response(failure_status, headers=failure_headers)
        if request.url.path == "/user":
            return httpx2.Response(200, json={"login": "octocat"})
        assert request.url.path == "/repos/octo/repo"
        return httpx2.Response(200)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return client, requests, recovered


async def test_verify_returns_login_for_visible_repository():
    client, requests = make_client()
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.ALLOWED, "octocat")

    assert requests[0].headers["accept"] == "application/vnd.github+json"
    assert requests[1].headers["authorization"] == "Bearer token"


@pytest.mark.parametrize("failure_path", ["/user", "/repos/octo/repo"])
@pytest.mark.parametrize("failure_status", [401, 404, 403, 418])
async def test_denied_responses_are_cached(failure_path, failure_status):
    user_status = failure_status if failure_path == "/user" else 200
    repo_status = failure_status if failure_path != "/user" else 200
    client, requests = make_client(user_status=user_status, repo_status=repo_status)
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.DENIED)
        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.DENIED)

    expected_calls = 1 if failure_path == "/user" else 2
    assert len(requests) == expected_calls


async def test_verify_denies_non_string_login():
    client, requests = make_client(user_login=42)
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.DENIED)
        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.DENIED)

    assert len(requests) == 1


@pytest.mark.parametrize("failure_path", ["/user", "/repos/octo/repo"])
@pytest.mark.parametrize(
    ("failure_status", "failure_headers"),
    [
        (403, {"x-ratelimit-remaining": "0"}),
        (403, {"retry-after": "5"}),
        (429, {}),
        (500, {}),
    ],
)
async def test_unavailable_response_is_not_cached(
    failure_path,
    failure_status,
    failure_headers,
):
    client, requests, recovered = make_recovering_client(
        failure_path=failure_path,
        failure_status=failure_status,
        failure_headers=failure_headers,
    )
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.UNAVAILABLE)
        failed_call_count = 1 if failure_path == "/user" else 2
        assert len(requests) == failed_call_count

        recovered[0] = True
        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.ALLOWED, "octocat")

    assert len(requests) == failed_call_count + 2


@pytest.mark.parametrize("failure_path", ["/user", "/repos/octo/repo"])
async def test_transport_failure_is_not_cached(failure_path):
    client, requests, recovered = make_recovering_client(failure_path=failure_path)
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.UNAVAILABLE)
        failed_call_count = 1 if failure_path == "/user" else 2
        assert len(requests) == failed_call_count

        recovered[0] = True
        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.ALLOWED, "octocat")

    assert len(requests) == failed_call_count + 2


async def test_cache_hit_avoids_second_github_call():
    client, requests = make_client()
    async with client:
        authenticator = GitHubAuthenticator("https://api.example", 300, client)

        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.ALLOWED, "octocat")
        assert await authenticator.verify("token", "octo/repo") == Access(Verdict.ALLOWED, "octocat")

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
