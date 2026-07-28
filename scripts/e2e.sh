#!/usr/bin/env bash

set -euo pipefail

TMP_DIR=$(mktemp -d)
REPO=""
REPO_IS_DISPOSABLE=0
declare -a PIDS=()
declare -a LOG_FILES=()
declare -a OPENED_ISSUES=()
declare -a PREEXISTING_HOOKS=()

dump_logs() {
    local line
    local log_file

    for log_file in "${LOG_FILES[@]}"; do
        [[ -f "${log_file}" ]] || continue
        printf '\n===== %s =====\n' "${log_file}" >&2
        while IFS= read -r line || [[ -n "${line}" ]]; do
            printf '%s\n' "${line}" >&2
        done <"${log_file}"
    done
}

cleanup() {
    local alive
    local deadline
    local hook_id
    local index
    local issue
    local known
    local pid
    local status

    status=$1
    set +e

    for ((index = ${#PIDS[@]} - 1; index >= 0; index--)); do
        pid=${PIDS[index]}
        kill "${pid}" 2>/dev/null
    done

    deadline=$((SECONDS + 5))
    while ((SECONDS < deadline)); do
        alive=0
        for pid in "${PIDS[@]}"; do
            if kill -0 "${pid}" 2>/dev/null; then
                alive=1
            fi
        done
        if ((alive == 0)); then
            break
        fi
        sleep 0.1
    done

    for pid in "${PIDS[@]}"; do
        if kill -0 "${pid}" 2>/dev/null; then
            kill -KILL "${pid}" 2>/dev/null
        fi
        wait "${pid}" 2>/dev/null
    done

    if ((status != 0)); then
        dump_logs
    fi

    # `gh webhook forward` does not reliably remove its hook when killed, so any
    # hook that appeared during this run is deleted here rather than left active
    # on the repository.
    if [[ -n "${REPO}" ]]; then
        while IFS= read -r hook_id; do
            [[ -n "${hook_id}" ]] || continue
            for known in ${PREEXISTING_HOOKS[@]+"${PREEXISTING_HOOKS[@]}"}; do
                if [[ "${hook_id}" == "${known}" ]]; then
                    continue 2
                fi
            done
            gh api -X DELETE "repos/${REPO}/hooks/${hook_id}" >/dev/null 2>&1 ||
                printf 'WARNING: could not delete leftover hook %s on %s\n' "${hook_id}" "${REPO}" >&2
        done < <(gh api "repos/${REPO}/hooks" --jq '.[].id' 2>/dev/null || true)
    fi

    if ((REPO_IS_DISPOSABLE)) && [[ -n "${REPO}" ]]; then
        if ! gh repo delete "${REPO}" --yes >/dev/null 2>&1; then
            printf 'WARNING: could not delete %s; the token may lack delete_repo. Delete it manually with: gh repo delete %q --yes\n' \
                "${REPO}" "${REPO}" >&2
        fi
    elif [[ -n "${REPO}" ]]; then
        # A caller-supplied repository is never deleted. Close what this run opened.
        printf 'NOTE: keeping caller-supplied repository %s; closing issues opened by this run.\n' "${REPO}" >&2
        for issue in "${OPENED_ISSUES[@]}"; do
            gh api -X PATCH "repos/${REPO}/issues/${issue}" -f state=closed >/dev/null 2>&1 || true
        done
    fi

    if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
        rm -rf -- "${TMP_DIR}"
    fi
    exit "${status}"
}

fail() {
    printf 'FAIL %s: %s\n' "$1" "$2" >&2
    exit 1
}

wait_for_subscription() {
    local deadline
    local label=$1
    local listener_log=$3
    local listener_pid=$2
    local listener_status

    deadline=$((SECONDS + 30))
    while ((SECONDS < deadline)); do
        if [[ -f "${listener_log}" && "$(<"${listener_log}")" == *subscribed* ]]; then
            return 0
        fi
        if ! kill -0 "${listener_pid}" 2>/dev/null; then
            if wait "${listener_pid}"; then
                listener_status=0
            else
                listener_status=$?
            fi
            fail "${label}" "listener exited before subscribing (rc=${listener_status})"
        fi
        sleep 0.2
    done
    fail "${label}" "listener did not subscribe within 30 seconds"
}

forwarded_hook_id() {
    local candidate
    local known

    while IFS= read -r candidate; do
        [[ -n "${candidate}" ]] || continue
        for known in ${PREEXISTING_HOOKS[@]+"${PREEXISTING_HOOKS[@]}"}; do
            if [[ "${candidate}" == "${known}" ]]; then
                continue 2
            fi
        done
        printf '%s\n' "${candidate}"
        return 0
    done < <(gh api "repos/${REPO}/hooks" --jq '.[].id' 2>/dev/null || true)
    return 0
}

wait_for_delivery_path() {
    local attempt
    local deadline
    local hook_id

    hook_id=$(forwarded_hook_id)
    if [[ -z "${hook_id}" ]]; then
        fail Setup "could not identify the hook created by gh webhook forward"
    fi

    # GitHub can report the hook as created before `gh webhook forward` has
    # finished wiring its relay, and deliveries in that window are dropped
    # silently. Waiting only for the hook to exist made scenarios start on an
    # unproven path and fail roughly one run in seven. Ping the hook until a
    # delivery actually reaches the local server, which exercises the same
    # GitHub -> forwarder -> server path the scenarios depend on.
    for ((attempt = 1; attempt <= 12; attempt++)); do
        gh api -X POST "repos/${REPO}/hooks/${hook_id}/pings" >/dev/null 2>&1
        deadline=$((SECONDS + 5))
        while ((SECONDS < deadline)); do
            if grep -q 'POST /webhook' "${SERVER_LOG}"; then
                printf 'Delivery path confirmed live after %s ping(s)\n' "${attempt}"
                return 0
            fi
            sleep 0.5
        done
    done
    fail Setup "no webhook delivery reached the server within 60 seconds"
}

trap 'cleanup "$?"' EXIT

if ! command -v gh >/dev/null 2>&1; then
    fail Preflight "gh is not installed"
fi
if ! gh auth status >/dev/null 2>&1; then
    fail Preflight "gh auth status failed"
fi
if ! EXTENSIONS=$(gh extension list 2>/dev/null); then
    fail Preflight "gh extension list failed"
fi
if [[ "${EXTENSIONS}" != *"cli/gh-webhook"* ]]; then
    fail Preflight "cli/gh-webhook is not installed"
fi
if ! command -v uv >/dev/null 2>&1; then
    fail Preflight "uv is not installed"
fi
if ! command -v curl >/dev/null 2>&1; then
    fail Preflight "curl is not installed"
fi
if ! command -v python3 >/dev/null 2>&1; then
    fail Preflight "python3 is not installed"
fi

if [[ -n "${GH_BABYSITTER_E2E_REPO:-}" ]]; then
    # Caller supplied a repository: reuse it and never delete it.
    REPO="${GH_BABYSITTER_E2E_REPO}"
    if ! gh repo view "${REPO}" --json nameWithOwner >/dev/null 2>&1; then
        fail Setup "cannot access ${REPO}"
    fi
    printf 'Using caller-supplied repository %s (it will not be deleted)\n' "${REPO}"
else
    OWNER=$(gh api user --jq .login)
    REPO="${OWNER}/gh-babysitter-e2e-$(date +%s)"
    REPO_IS_DISPOSABLE=1
    if ! gh repo create "${REPO}" --private --add-readme; then
        fail Setup "could not create ${REPO}"
    fi
fi

if ! PORT=$(python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
); then
    fail Setup "could not choose a free port"
fi

if ! SECRET=$(openssl rand -hex 32 2>/dev/null); then
    if ! SECRET=$(python3 - <<'PY'
import secrets

print(secrets.token_hex(32))
PY
    ); then
        fail Setup "could not generate a webhook secret"
    fi
fi

SERVER_URL="http://127.0.0.1:${PORT}"
SERVER_LOG="${TMP_DIR}/server.log"
FORWARD_LOG="${TMP_DIR}/forwarder.log"
LOG_FILES+=("${SERVER_LOG}" "${FORWARD_LOG}")

GH_BABYSITTER_WEBHOOK_SECRET=${SECRET} uv run gh-babysitter serve --port "${PORT}" >"${SERVER_LOG}" 2>&1 &
SERVER_PID=$!
PIDS+=("${SERVER_PID}")

SERVER_READY=0
SERVER_DEADLINE=$((SECONDS + 30))
while ((SECONDS < SERVER_DEADLINE)); do
    HTTP_CODE=$(curl --silent --output /dev/null --write-out '%{http_code}' --request POST "${SERVER_URL}/webhook" || true)
    if [[ "${HTTP_CODE}" == 401 ]]; then
        SERVER_READY=1
        break
    fi
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        fail Setup "server exited before becoming ready"
    fi
    sleep 0.2
done
if ((SERVER_READY == 0)); then
    fail Setup "server did not return HTTP 401 within 30 seconds"
fi

mapfile -t PREEXISTING_HOOKS < <(gh api "repos/${REPO}/hooks" --jq '.[].id' 2>/dev/null || true)

gh webhook forward \
    --repo="${REPO}" \
    --events='issues,pull_request,issue_comment,pull_request_review,release' \
    --url="${SERVER_URL}/webhook" \
    --secret="${SECRET}" >"${FORWARD_LOG}" 2>&1 &
FORWARD_PID=$!
PIDS+=("${FORWARD_PID}")

HOOK_READY=0
HOOK_DEADLINE=$((SECONDS + 30))
while ((SECONDS < HOOK_DEADLINE)); do
    if HOOK_COUNT=$(gh api "repos/${REPO}/hooks" --jq length 2>>"${FORWARD_LOG}"); then
        if [[ "${HOOK_COUNT}" =~ ^[0-9]+$ ]] && ((HOOK_COUNT >= 1)); then
            HOOK_READY=1
            break
        fi
    fi
    if ! kill -0 "${FORWARD_PID}" 2>/dev/null; then
        fail Setup "webhook forwarder exited before creating a hook"
    fi
    sleep 0.5
done
if ((HOOK_READY == 0)); then
    fail Setup "GitHub did not report an active repository hook within 30 seconds"
fi

wait_for_delivery_path

A_STDOUT="${TMP_DIR}/scenario-a.stdout"
A_STDERR="${TMP_DIR}/scenario-a.stderr"
LOG_FILES+=("${A_STDOUT}" "${A_STDERR}")
uv run gh-babysitter listen \
    -R "${REPO}" \
    -E issues \
    --count 1 \
    --timeout 180 \
    --server "${SERVER_URL}" >"${A_STDOUT}" 2>"${A_STDERR}" &
A_PID=$!
PIDS+=("${A_PID}")
wait_for_subscription "Scenario A" "${A_PID}" "${A_STDERR}"

if ! ISSUE=$(gh api "repos/${REPO}/issues" -f title='e2e probe' --jq .number); then
    fail "Scenario A" "could not create the probe issue"
fi
if [[ ! "${ISSUE}" =~ ^[0-9]+$ ]]; then
    fail "Scenario A" "GitHub returned an invalid issue number: ${ISSUE}"
fi
OPENED_ISSUES+=("${ISSUE}")
if wait "${A_PID}"; then
    A_STATUS=0
else
    A_STATUS=$?
fi
if ((A_STATUS != 0)); then
    fail "Scenario A" "listener exited with rc=${A_STATUS}"
fi
A_OUTPUT=$(<"${A_STDOUT}")
if [[ "${A_OUTPUT}" != *'"event":"issues"'* || "${A_OUTPUT}" != *'"action":"opened"'* ]]; then
    fail "Scenario A" "listener output did not contain issues.opened"
fi
printf 'PASS Scenario A: received issues.opened for #%s\n' "${ISSUE}"

B_STDOUT="${TMP_DIR}/scenario-b.stdout"
B_STDERR="${TMP_DIR}/scenario-b.stderr"
LOG_FILES+=("${B_STDOUT}" "${B_STDERR}")
uv run gh-babysitter listen \
    -R "${REPO}" \
    -n "${ISSUE}" \
    --until closed \
    --timeout 180 \
    --server "${SERVER_URL}" >"${B_STDOUT}" 2>"${B_STDERR}" &
B_PID=$!
PIDS+=("${B_PID}")
wait_for_subscription "Scenario B" "${B_PID}" "${B_STDERR}"

if ! gh api -X PATCH "repos/${REPO}/issues/${ISSUE}" -f state=closed >/dev/null; then
    fail "Scenario B" "could not close issue #${ISSUE}"
fi
if wait "${B_PID}"; then
    B_STATUS=0
else
    B_STATUS=$?
fi
if ((B_STATUS != 0)); then
    fail "Scenario B" "listener exited with rc=${B_STATUS}"
fi
printf 'PASS Scenario B: --until closed observed the stream event for #%s\n' "${ISSUE}"

C_STDOUT="${TMP_DIR}/scenario-c.stdout"
C_STDERR="${TMP_DIR}/scenario-c.stderr"
LOG_FILES+=("${C_STDOUT}" "${C_STDERR}")
C_STARTED=${SECONDS}
if uv run gh-babysitter listen \
    -R "${REPO}" \
    -n "${ISSUE}" \
    --until closed \
    --timeout 60 \
    --server "${SERVER_URL}" >"${C_STDOUT}" 2>"${C_STDERR}"; then
    C_STATUS=0
else
    C_STATUS=$?
fi
C_ELAPSED=$((SECONDS - C_STARTED))
if ((C_STATUS != 0)); then
    fail "Scenario C" "boundary poll exited with rc=${C_STATUS}"
fi
if ((C_ELAPSED > 15)); then
    fail "Scenario C" "boundary poll took ${C_ELAPSED}s (expected at most 15s)"
fi
printf 'PASS Scenario C: initial poll returned in %ss without a webhook event\n' "${C_ELAPSED}"
printf 'PASS all live E2E scenarios\n'
