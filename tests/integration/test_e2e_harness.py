"""Behavior tests for the live E2E harness configuration."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).parents[2]
LIBRARY = ROOT / "scripts" / "e2e-lib.sh"
SECRET_CONFIGURATOR = ROOT / "scripts" / "configure-e2e-secrets.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "e2e-negative-auth.yml"
E2E_ENV_NAMES = {
    "GH_BABYSITTER_E2E_REPO",
    "GH_BABYSITTER_E2E_REQUIRE_NEGATIVE",
    "GH_BABYSITTER_E2E_SECONDARY_REPO",
    "GH_BABYSITTER_E2E_SECONDARY_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
}


def run_library(command: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = {key: value for key, value in os.environ.items() if key not in E2E_ENV_NAMES}
    process_env.update(env or {})
    return subprocess.run(
        ["bash", "-c", f"source {LIBRARY!s}; {command}"],
        cwd=ROOT,
        env=process_env,
        check=False,
        capture_output=True,
        text=True,
    )


def write_fake_gh(directory: Path, *, primary_can_read_secondary: bool = False) -> None:
    secondary_for_primary = "200" if primary_can_read_secondary else "404"
    script = directory / "gh"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            endpoint="${{@: -1}}"
            case "${{GH_TOKEN:-}}:${{endpoint}}" in
                token-a:repos/octo/repo-a) status=200 ;;
                token-a:repos/octo/repo-b) status={secondary_for_primary} ;;
                token-b:repos/octo/repo-a) status=404 ;;
                token-b:repos/octo/repo-b) status=200 ;;
                *) status=500 ;;
            esac
            printf 'HTTP/2.0 %s TEST\\n' "${{status}}"
            if ((status >= 400)); then
                exit 1
            fi
            """
        )
    )
    script.chmod(0o755)


def write_fake_uv(directory: Path, *, status: int, stderr: str, stdout: str = "") -> None:
    script = directory / "uv"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s' {stdout!r}
            printf '%s' {stderr!r} >&2
            exit {status}
            """
        )
    )
    script.chmod(0o755)


def write_fake_admin_gh(directory: Path) -> tuple[Path, Path]:
    argument_log = directory / "gh-arguments.log"
    stdin_log = directory / "gh-stdin.log"
    script = directory / "gh"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >>"${FAKE_GH_ARGUMENT_LOG}"
            if [[ "$1 $2" == "secret set" ]]; then
                printf '%s:' "$3" >>"${FAKE_GH_STDIN_LOG}"
                cat >>"${FAKE_GH_STDIN_LOG}"
                printf '\\n' >>"${FAKE_GH_STDIN_LOG}"
                exit 0
            fi
            if [[ "$1 $2" == "variable set" ]]; then
                exit 0
            fi
            endpoint="${@: -1}"
            case "${GH_TOKEN:-}:${endpoint}" in
                token-a:repos/octo/repo-a) status=200 ;;
                token-a:repos/octo/repo-b) status=404 ;;
                token-b:repos/octo/repo-a) status=404 ;;
                token-b:repos/octo/repo-b) status=200 ;;
                *) status=500 ;;
            esac
            printf 'HTTP/2.0 %s TEST\\n' "${status}"
            if ((status >= 400)); then
                exit 1
            fi
            """
        )
    )
    script.chmod(0o755)
    return argument_log, stdin_log


def full_environment(tmp_path: Path) -> dict[str, str]:
    write_fake_gh(tmp_path)
    return {
        "GH_BABYSITTER_E2E_REPO": "octo/repo-a",
        "GH_BABYSITTER_E2E_SECONDARY_REPO": "octo/repo-b",
        "GH_BABYSITTER_E2E_SECONDARY_TOKEN": "token-b",
        "GH_TOKEN": "token-a",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }


def test_negative_auth_is_explicitly_skipped_without_fixture() -> None:
    result = run_library('configure_negative_auth; printf "enabled=%s\\n" "$NEGATIVE_AUTH_ENABLED"')

    assert result.returncode == 0
    assert "SKIP negative authorization" in result.stdout
    assert "enabled=0" in result.stdout


def test_required_negative_auth_fails_when_fixture_is_missing() -> None:
    result = run_library(
        "configure_negative_auth",
        env={"GH_BABYSITTER_E2E_REQUIRE_NEGATIVE": "1"},
    )

    assert result.returncode == 1
    assert "required" in result.stderr


def test_partial_negative_auth_fixture_fails_closed() -> None:
    result = run_library(
        "configure_negative_auth",
        env={"GH_BABYSITTER_E2E_SECONDARY_REPO": "octo/repo-b"},
    )

    assert result.returncode == 1
    assert "incomplete" in result.stderr


def test_negative_auth_requires_explicit_primary_token() -> None:
    result = run_library(
        "configure_negative_auth",
        env={
            "GH_BABYSITTER_E2E_REPO": "octo/repo-a",
            "GH_BABYSITTER_E2E_SECONDARY_REPO": "octo/repo-b",
            "GH_BABYSITTER_E2E_SECONDARY_TOKEN": "token-b",
        },
    )

    assert result.returncode == 1
    assert "GH_TOKEN" in result.stderr


def test_complete_negative_auth_fixture_is_enabled_without_printing_tokens(tmp_path: Path) -> None:
    env = full_environment(tmp_path)

    result = run_library('configure_negative_auth; printf "enabled=%s\\n" "$NEGATIVE_AUTH_ENABLED"', env=env)

    assert result.returncode == 0
    assert "enabled=1" in result.stdout
    assert env["GH_TOKEN"] not in result.stdout + result.stderr
    assert env["GH_BABYSITTER_E2E_SECONDARY_TOKEN"] not in result.stdout + result.stderr


def test_access_matrix_accepts_two_repo_scoped_credentials(tmp_path: Path) -> None:
    result = run_library(
        "configure_negative_auth && validate_negative_auth_matrix",
        env=full_environment(tmp_path),
    )

    assert result.returncode == 0
    assert "credential access matrix" in result.stdout


def test_access_matrix_rejects_primary_credential_that_can_read_both_repositories(tmp_path: Path) -> None:
    write_fake_gh(tmp_path, primary_can_read_secondary=True)
    env = full_environment(tmp_path)
    write_fake_gh(tmp_path, primary_can_read_secondary=True)

    result = run_library(
        "configure_negative_auth && validate_negative_auth_matrix",
        env=env,
    )

    assert result.returncode == 1
    assert "primary credential unexpectedly reads" in result.stderr


def run_denied_subscription(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "GH_TOKEN": "token-a",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    command = (
        f"TMP_DIR={tmp_path!s}; SERVER_URL=http://127.0.0.1:9999; LOG_FILES=(); "
        'assert_denied_subscription "Denied fixture" "octo/repo-b"'
    )
    return run_library(command, env=env)


def test_denied_subscription_requires_prompt_server_403(tmp_path: Path) -> None:
    write_fake_uv(
        tmp_path,
        status=1,
        stderr="server rejected the GitHub token (403)\n",
    )

    result = run_denied_subscription(tmp_path)

    assert result.returncode == 0
    assert "PASS Denied fixture" in result.stdout
    assert "token-a" not in result.stdout + result.stderr


def test_denied_subscription_rejects_retry_timeout(tmp_path: Path) -> None:
    write_fake_uv(
        tmp_path,
        status=124,
        stderr="warning: disconnected (server returned 503)\n",
    )

    result = run_denied_subscription(tmp_path)

    assert result.returncode == 1
    assert "expected rc=1" in result.stderr


def test_denied_subscription_rejects_any_stream_registration(tmp_path: Path) -> None:
    write_fake_uv(
        tmp_path,
        status=1,
        stderr="subscribed\nserver rejected the GitHub token (403)\n",
    )

    result = run_denied_subscription(tmp_path)

    assert result.returncode == 1
    assert "registered a stream" in result.stderr


def test_secret_configurator_validates_then_writes_secrets_via_stdin(tmp_path: Path) -> None:
    argument_log, stdin_log = write_fake_admin_gh(tmp_path)
    env = {
        **full_environment(tmp_path),
        "FAKE_GH_ARGUMENT_LOG": str(argument_log),
        "FAKE_GH_STDIN_LOG": str(stdin_log),
        "GH_BABYSITTER_E2E_CONFIG_REPO": "octo/product",
    }
    write_fake_admin_gh(tmp_path)

    result = subprocess.run(
        ["bash", str(SECRET_CONFIGURATOR)],
        cwd=ROOT,
        env={**os.environ, **env},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    arguments = argument_log.read_text()
    secret_input = stdin_log.read_text()
    assert "variable set E2E_PRIMARY_REPO" in arguments
    assert "variable set E2E_SECONDARY_REPO" in arguments
    assert "secret set E2E_PRIMARY_TOKEN" in arguments
    assert "secret set E2E_SECONDARY_TOKEN" in arguments
    assert "token-a" not in arguments + result.stdout + result.stderr
    assert "token-b" not in arguments + result.stdout + result.stderr
    assert "E2E_PRIMARY_TOKEN:token-a" in secret_input
    assert "E2E_SECONDARY_TOKEN:token-b" in secret_input


def test_secret_configurator_fails_before_writes_with_incomplete_environment(tmp_path: Path) -> None:
    argument_log, _ = write_fake_admin_gh(tmp_path)
    env = {
        "FAKE_GH_ARGUMENT_LOG": str(argument_log),
        "FAKE_GH_STDIN_LOG": str(tmp_path / "gh-stdin.log"),
        "GH_BABYSITTER_E2E_CONFIG_REPO": "octo/product",
        "GH_BABYSITTER_E2E_REPO": "octo/repo-a",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["bash", str(SECRET_CONFIGURATOR)],
        cwd=ROOT,
        env={**os.environ, **env},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "required" in result.stderr
    assert not argument_log.exists()


def test_ci_pins_the_validated_webhook_extension_release() -> None:
    workflow = WORKFLOW.read_text()

    assert "gh extension install cli/gh-webhook --pin v0.2.0" in workflow


def test_ci_can_validate_an_internal_pull_request_before_merge() -> None:
    workflow = WORKFLOW.read_text()

    assert "pull_request:" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow


def test_ci_is_repeatable_without_a_calendar_schedule() -> None:
    workflow = WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "\n  schedule:" not in workflow
    assert "cron:" not in workflow
