#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/e2e-lib.sh
source "${SCRIPT_DIR}/e2e-lib.sh"

if ! command -v gh >/dev/null 2>&1; then
    printf 'FAIL Preflight: gh is not installed\n' >&2
    exit 1
fi

GH_BABYSITTER_E2E_REQUIRE_NEGATIVE=1
configure_negative_auth
validate_negative_auth_matrix

CONFIG_REPO=${GH_BABYSITTER_E2E_CONFIG_REPO:-}
if [[ -z "${CONFIG_REPO}" ]]; then
    CONFIG_REPO=$(
        env -u GH_TOKEN -u GITHUB_TOKEN gh repo view \
            --json nameWithOwner \
            --jq .nameWithOwner
    )
fi

env -u GH_TOKEN -u GITHUB_TOKEN gh variable set E2E_PRIMARY_REPO \
    --repo "${CONFIG_REPO}" \
    --body "${GH_BABYSITTER_E2E_REPO}"
env -u GH_TOKEN -u GITHUB_TOKEN gh variable set E2E_SECONDARY_REPO \
    --repo "${CONFIG_REPO}" \
    --body "${GH_BABYSITTER_E2E_SECONDARY_REPO}"

printf '%s' "${GH_TOKEN}" |
    env -u GH_TOKEN -u GITHUB_TOKEN gh secret set E2E_PRIMARY_TOKEN --repo "${CONFIG_REPO}"
printf '%s' "${GH_BABYSITTER_E2E_SECONDARY_TOKEN}" |
    env -u GH_TOKEN -u GITHUB_TOKEN gh secret set E2E_SECONDARY_TOKEN --repo "${CONFIG_REPO}"

printf 'PASS configured Actions variables and encrypted secrets in %s\n' "${CONFIG_REPO}"
