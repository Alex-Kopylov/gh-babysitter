#!/usr/bin/env bash

# Shared helpers for scripts/e2e.sh. This file is sourced, not executed.

NEGATIVE_AUTH_ENABLED=0

# ponytail: Replace the four-spelling parser and requested flag with one check for GH_BABYSITTER_E2E_REQUIRE_NEGATIVE=1.
_e2e_truthy() {
    case "${1,,}" in
        1 | true | yes | on) return 0 ;;
        *) return 1 ;;
    esac
}

configure_negative_auth() {
    local missing=()
    local requested=0

    if [[ -n "${GH_BABYSITTER_E2E_SECONDARY_REPO:-}" || -n "${GH_BABYSITTER_E2E_SECONDARY_TOKEN:-}" ]]; then
        requested=1
    fi
    if _e2e_truthy "${GH_BABYSITTER_E2E_REQUIRE_NEGATIVE:-0}"; then
        requested=1
    fi

    if ((requested == 0)); then
        printf 'SKIP negative authorization: secondary fixture is not configured\n'
        NEGATIVE_AUTH_ENABLED=0
        return 0
    fi

    [[ -n "${GH_BABYSITTER_E2E_REPO:-}" ]] || missing+=("GH_BABYSITTER_E2E_REPO")
    [[ -n "${GH_BABYSITTER_E2E_SECONDARY_REPO:-}" ]] ||
        missing+=("GH_BABYSITTER_E2E_SECONDARY_REPO")
    [[ -n "${GH_BABYSITTER_E2E_SECONDARY_TOKEN:-}" ]] ||
        missing+=("GH_BABYSITTER_E2E_SECONDARY_TOKEN")
    [[ -n "${GH_TOKEN:-}" ]] || missing+=("GH_TOKEN")

    if ((${#missing[@]})); then
        if _e2e_truthy "${GH_BABYSITTER_E2E_REQUIRE_NEGATIVE:-0}"; then
            printf 'FAIL Preflight: negative authorization is required; missing %s\n' "${missing[*]}" >&2
        else
            printf 'FAIL Preflight: negative authorization fixture is incomplete; missing %s\n' "${missing[*]}" >&2
        fi
        return 1
    fi
    if [[ "${GH_BABYSITTER_E2E_REPO}" == "${GH_BABYSITTER_E2E_SECONDARY_REPO}" ]]; then
        printf 'FAIL Preflight: primary and secondary repositories must differ\n' >&2
        return 1
    fi

    NEGATIVE_AUTH_ENABLED=1
    printf 'Negative authorization fixture enabled for %s and %s\n' \
        "${GH_BABYSITTER_E2E_REPO}" "${GH_BABYSITTER_E2E_SECONDARY_REPO}"
}

_github_repo_status() {
    local output
    local repo=$1
    local status
    local token=${2-}

    if [[ -n "${token}" ]]; then
        output=$(GH_TOKEN="${token}" GITHUB_TOKEN='' gh api --include --silent "repos/${repo}" 2>&1) || true
    else
        output=$(gh api --include --silent "repos/${repo}" 2>&1) || true
    fi
    status=$(sed -nE 's#^HTTP/[0-9.]+ ([0-9]{3}).*#\1#p' <<<"${output}" | tail -n 1) || true
    printf '%s\n' "${status}"
}

validate_negative_auth_matrix() {
    local primary_primary
    local primary_secondary
    local secondary_primary
    local secondary_secondary

    if ((NEGATIVE_AUTH_ENABLED == 0)); then
        return 0
    fi

    primary_primary=$(_github_repo_status "${GH_BABYSITTER_E2E_REPO}")
    primary_secondary=$(_github_repo_status "${GH_BABYSITTER_E2E_SECONDARY_REPO}")
    secondary_primary=$(
        _github_repo_status "${GH_BABYSITTER_E2E_REPO}" "${GH_BABYSITTER_E2E_SECONDARY_TOKEN}"
    )
    secondary_secondary=$(
        _github_repo_status "${GH_BABYSITTER_E2E_SECONDARY_REPO}" "${GH_BABYSITTER_E2E_SECONDARY_TOKEN}"
    )

    if [[ "${primary_primary}" != 200 ]]; then
        printf 'FAIL Preflight: primary credential cannot read %s (HTTP %s)\n' \
            "${GH_BABYSITTER_E2E_REPO}" "${primary_primary:-unknown}" >&2
        return 1
    fi
    if [[ "${primary_secondary}" != 404 ]]; then
        printf 'FAIL Preflight: primary credential unexpectedly reads %s (HTTP %s, expected 404)\n' \
            "${GH_BABYSITTER_E2E_SECONDARY_REPO}" "${primary_secondary:-unknown}" >&2
        return 1
    fi
    if [[ "${secondary_primary}" != 404 ]]; then
        printf 'FAIL Preflight: secondary credential unexpectedly reads %s (HTTP %s, expected 404)\n' \
            "${GH_BABYSITTER_E2E_REPO}" "${secondary_primary:-unknown}" >&2
        return 1
    fi
    if [[ "${secondary_secondary}" != 200 ]]; then
        printf 'FAIL Preflight: secondary credential cannot read %s (HTTP %s)\n' \
            "${GH_BABYSITTER_E2E_SECONDARY_REPO}" "${secondary_secondary:-unknown}" >&2
        return 1
    fi

    printf 'PASS credential access matrix: primary and secondary tokens are repository-isolated\n'
}

# ponytail: Require a token argument and use one GH_TOKEN-prefixed listener command instead of duplicating the command.
assert_denied_subscription() {
    local elapsed
    local file_label
    local label=$1
    local repo=$2
    local started
    local status
    local stderr_file
    local server_url
    local stdout_file
    local tmp_dir
    local token=${3-}

    # These globals belong to the sourcing live harness.
    # shellcheck disable=SC2153,SC2154
    tmp_dir=${TMP_DIR}
    # shellcheck disable=SC2153,SC2154
    server_url=${SERVER_URL}
    file_label=${label//[^[:alnum:]]/_}
    stdout_file="${tmp_dir}/${file_label}.stdout"
    stderr_file="${tmp_dir}/${file_label}.stderr"
    LOG_FILES+=("${stdout_file}" "${stderr_file}")

    started=${SECONDS}
    if [[ -n "${token}" ]]; then
        if GH_TOKEN="${token}" GITHUB_TOKEN='' uv run gh-babysitter listen \
            -R "${repo}" \
            -E issues \
            --count 1 \
            --timeout 15 \
            --server "${server_url}" >"${stdout_file}" 2>"${stderr_file}"; then
            status=0
        else
            status=$?
        fi
    elif uv run gh-babysitter listen \
        -R "${repo}" \
        -E issues \
        --count 1 \
        --timeout 15 \
        --server "${server_url}" >"${stdout_file}" 2>"${stderr_file}"; then
        status=0
    else
        status=$?
    fi
    elapsed=$((SECONDS - started))

    if ((status != 1)); then
        printf 'FAIL %s: expected rc=1 for a confirmed denial, got rc=%s\n' "${label}" "${status}" >&2
        return 1
    fi
    if [[ -s "${stdout_file}" ]]; then
        printf 'FAIL %s: denied listener wrote event data to stdout\n' "${label}" >&2
        return 1
    fi
    if ! grep -Fq 'server rejected the GitHub token (403)' "${stderr_file}"; then
        printf 'FAIL %s: listener did not report the server 403\n' "${label}" >&2
        return 1
    fi
    if grep -Fq 'subscribed' "${stderr_file}"; then
        printf 'FAIL %s: denied listener registered a stream before rejection\n' "${label}" >&2
        return 1
    fi
    if ((elapsed >= 15)); then
        printf 'FAIL %s: denial took %ss instead of exiting promptly\n' "${label}" "${elapsed}" >&2
        return 1
    fi

    printf 'PASS %s: server returned 403 in %ss without opening a stream\n' "${label}" "${elapsed}"
}
