> Historical change, migrated 2026-08-03 from `docs/plans/2026-07-20-implementation.md`
> during the docs → OpenSpec transfer. Already implemented; the original plan is
> preserved verbatim (links updated) in [design.md](design.md).

## Why

The specs were complete but nothing was implemented. This change builds the
first working server, CLI, and test suite, resolving the specs' open questions
(event envelope, event menu, `--until` matrix, recheck period, `serve`
packaging, server discovery) and pinning module interfaces.

## What Changes

- Server (`src/gh_babysitter/server/`): FastAPI app with `POST /webhook`
  (HMAC), `GET /events/stream` (SSE), payload normalization, in-memory
  registry, GitHub-backed authenticator with TTL cache and periodic recheck.
- CLI (`src/gh_babysitter/cli/`): `listen` (SSE consumer with reconnect,
  `--until`/`--count`/`--first-event`/`--timeout`), admin `setup`, `serve`.
- gh extension entrypoint script at the repo root.
- Unit and integration tests (coverage ≥ 80) plus a live e2e script.

## Capabilities

### New Capabilities

- `event-gateway`: webhook ingress, normalization, matching, fan-out
- `event-delivery`: SSE transport, subscription lifecycle, envelope
- `authorization`: GitHub-backed token verification, HMAC ingress
- `github-webhook`: org-level webhook with a static event allowlist
- `cli`: `listen`/`setup`/`serve` as a gh extension

### Modified Capabilities

- None.

## Impact

`src/gh_babysitter/**`, `tests/**`, `scripts/e2e.sh`, `pyproject.toml`,
`gh-babysitter` entrypoint.
