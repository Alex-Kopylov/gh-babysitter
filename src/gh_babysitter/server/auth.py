"""GitHub token and repository access verification."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol

import httpx2


def _response_verdict(response: httpx2.Response) -> Verdict:
    """Classify one GitHub API response."""
    if response.status_code == httpx2.codes.OK:
        return Verdict.ALLOWED
    if response.status_code == httpx2.codes.FORBIDDEN and (
        response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
    ):
        return Verdict.UNAVAILABLE
    if response.status_code == httpx2.codes.TOO_MANY_REQUESTS or httpx2.codes.is_server_error(response.status_code):
        return Verdict.UNAVAILABLE
    return Verdict.DENIED


class Verdict(StrEnum):
    """Outcome of a repository-access verification."""

    ALLOWED = "allowed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Access:
    """Repository-access verdict with an optional authenticated login."""

    verdict: Verdict
    login: str | None = None


class Authenticator(Protocol):
    """Repository-access verifier accepted by the server app."""

    async def verify(self, token: str, repo: str, *, fresh: bool = False) -> Access:
        """Return an explicit repository-access verdict."""
        ...


class GitHubAuthenticator:
    """Verify GitHub tokens and cache repository visibility checks."""

    def __init__(self, api_url: str, cache_ttl: int, client: httpx2.AsyncClient) -> None:
        """Configure GitHub API access and the verification cache."""
        self.api_url = api_url.rstrip("/")
        self.cache_ttl = cache_ttl
        self.client = client
        self._cache: dict[tuple[str, str], tuple[float, Access]] = {}

    async def verify(self, token: str, repo: str, *, fresh: bool = False) -> Access:
        """Return an explicit repository-access verdict."""
        await asyncio.sleep(0)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        key = (token_hash, repo)
        cached = self._cache.get(key)
        if not fresh and cached is not None and cached[0] > monotonic():
            return cached[1]

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        }
        try:
            user_response = await self.client.get(f"{self.api_url}/user", headers=headers)
        except httpx2.HTTPError:
            return Access(Verdict.UNAVAILABLE)
        user_verdict = _response_verdict(user_response)
        if user_verdict is not Verdict.ALLOWED:
            access = Access(user_verdict)
        else:
            candidate = user_response.json().get("login")
            if not isinstance(candidate, str):
                access = Access(Verdict.DENIED)
            else:
                try:
                    repo_response = await self.client.get(f"{self.api_url}/repos/{repo}", headers=headers)
                except httpx2.HTTPError:
                    return Access(Verdict.UNAVAILABLE)
                repo_verdict = _response_verdict(repo_response)
                access = Access(repo_verdict, candidate if repo_verdict is Verdict.ALLOWED else None)

        if access.verdict is not Verdict.UNAVAILABLE:
            self._cache[key] = (monotonic() + self.cache_ttl, access)
        return access
