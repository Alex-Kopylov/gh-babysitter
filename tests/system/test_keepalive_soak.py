"""Fast real-process keepalive soak test."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.system


class TestKeepaliveSoak:
    """A quiet stream stays connected when pings beat the read timeout."""

    def test_idle_stream_has_no_disconnect_warning_for_ten_seconds(
        self,
        free_port,
        uvicorn_server,
        babysitter_server,
        run_cli,
    ):
        """Keep one subscription alive through multiple read-timeout windows."""
        github_api_port = uvicorn_server("tests.system.test_process_signals:github_api_app")
        babysitter_server(
            free_port,
            env={
                "GH_BABYSITTER_WEBHOOK_SECRET": "secret",
                "GH_BABYSITTER_GITHUB_API_URL": f"http://127.0.0.1:{github_api_port}",
                "GH_BABYSITTER_PING_INTERVAL": "1",
                "GH_BABYSITTER_RECHECK_INTERVAL": "60",
            },
        )

        started = time.monotonic()
        result = run_cli(
            "listen",
            "-R",
            "octo/repo",
            "-E",
            "issues",
            "--server",
            f"http://127.0.0.1:{free_port}",
            "--timeout",
            "10s",
            env={
                "GH_TOKEN": "test-token",
                "GH_BABYSITTER_SERVER_TIMEOUT": "1",
                "GH_BABYSITTER_STREAM_TIMEOUT": "3",
            },
            timeout=15,
        )
        elapsed = time.monotonic() - started

        assert result.returncode == 124, result.stderr
        assert 9.5 <= elapsed < 12
        assert result.stderr.count("subscribed") == 1
        assert "warning: disconnected" not in result.stderr
        assert result.stdout == ""
