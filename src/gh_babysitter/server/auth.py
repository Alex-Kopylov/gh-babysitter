"""GitHub token and repository access verification."""

import hashlib
from time import monotonic

import httpx


class GitHubAuthenticator:
    """Verify GitHub tokens and cache repository visibility checks."""

    def __init__(self, api_url: str, cache_ttl: int, client: httpx.AsyncClient) -> None:
        """Configure GitHub API access and the verification cache."""
        self.api_url = api_url.rstrip("/")
        self.cache_ttl = cache_ttl
        self.client = client
        self._cache: dict[tuple[str, str], tuple[float, str | None]] = {}

    async def verify(self, token: str, repo: str, *, fresh: bool = False) -> str | None:
        """Return the token login when it can read the repository."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        key = (token_hash, repo)
        cached = self._cache.get(key)
        if not fresh and cached is not None and cached[0] > monotonic():
            return cached[1]

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        }
        user_response = await self.client.get(f"{self.api_url}/user", headers=headers)
        login = None
        if user_response.status_code == httpx.codes.OK:
            candidate = user_response.json().get("login")
            if isinstance(candidate, str):
                repo_response = await self.client.get(f"{self.api_url}/repos/{repo}", headers=headers)
                if repo_response.status_code == httpx.codes.OK:
                    login = candidate

        self._cache[key] = (monotonic() + self.cache_ttl, login)
        return login
