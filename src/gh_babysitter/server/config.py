"""Server configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from ``GH_BABYSITTER_*`` environment variables."""

    webhook_secret: str | None = None
    github_api_url: str = "https://api.github.com"
    auth_cache_ttl: int = 300
    recheck_interval: int = 300
    ping_interval: int = 30
    queue_maxsize: int = 256

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from the process environment."""
        prefix = "GH_BABYSITTER_"
        return cls(
            webhook_secret=os.getenv(f"{prefix}WEBHOOK_SECRET"),
            github_api_url=os.getenv(f"{prefix}GITHUB_API_URL", "https://api.github.com"),
            auth_cache_ttl=int(os.getenv(f"{prefix}AUTH_CACHE_TTL", "300")),
            recheck_interval=int(os.getenv(f"{prefix}RECHECK_INTERVAL", "300")),
            ping_interval=int(os.getenv(f"{prefix}PING_INTERVAL", "30")),
            queue_maxsize=int(os.getenv(f"{prefix}QUEUE_MAXSIZE", "256")),
        )
