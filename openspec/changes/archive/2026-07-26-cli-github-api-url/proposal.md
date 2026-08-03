> Historical change, migrated 2026-08-03 from
> `docs/plans/2026-07-26-cli-github-api-url.md` during the docs → OpenSpec
> transfer. Already implemented; the original design is preserved verbatim
> (links updated) in [design.md](design.md).

## Why

The CLI hardcoded `https://api.github.com` in two places, so GitHub Enterprise
Server installs could not be served: `listen` validated tokens and polled
`--until` state against public GitHub, and `setup` would create the org
webhook on the wrong host.

## What Changes

- `--api-url` flag on `listen` and `setup`.
- `gh`-compatible resolution chain: `--api-url` →
  `GH_BABYSITTER_GITHUB_API_URL` → `GITHUB_API_URL` → `GH_HOST` (with host →
  API-base derivation) → default; blank values fall through.
- Server keeps reading only `GH_BABYSITTER_GITHUB_API_URL` — no ambient
  `GH_HOST` pickup.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `cli`: GitHub Enterprise API-base resolution (precedence chain and `GH_HOST`
  derivation rules)

## Impact

`src/gh_babysitter/server/config.py`, `src/gh_babysitter/cli/config.py`,
`src/gh_babysitter/cli/main.py`, `src/gh_babysitter/cli/listen.py`,
`src/gh_babysitter/cli/setup.py`, their tests.
