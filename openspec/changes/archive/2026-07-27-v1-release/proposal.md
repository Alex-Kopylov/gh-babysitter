> Historical change, migrated 2026-08-03 from `docs/plans/2026-07-27-v1-release.md`
> during the docs → OpenSpec transfer. Already implemented (released as 1.0.0);
> the original plan is preserved verbatim in [design.md](design.md).

## Why

A black-box E2E audit ([#21](https://github.com/Alex-Kopylov/gh-babysitter/issues/21))
plus two verification passes found P0/P1 issues sharing one root cause: the
system had no vocabulary for "transient" versus "permanent" — the CLI silently
retried permanent errors forever, the server cached a transient 403 as a
5-minute denial, and dropped events were counted as delivered.

## What Changes

- Server: three-state authorization verdict (`allowed`/`denied`/`unavailable`,
  transient never cached), `503` + `Retry-After` on unavailable, webhook
  payload hardening (`400` on malformed JSON), honest
  `matched`/`delivered`/`dropped` accounting with the `lag` event, bounded
  graceful shutdown.
- CLI: local validation before any network activity (exit `2`), plain-HTTP
  token egress guard, explicit HTTP error classification (retryable vs
  permanent), disconnect warnings, bounded stream read timeout, clean stream
  teardown, `setup` secret hygiene (`--secret-stdin`, no echo of supplied
  secrets).
- Docs and release: English README, spec updates, `1.0.0` version,
  `CHANGELOG.md`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `authorization`: three-state verdict, cache rules, `503` vs `403`
- `event-gateway`: `/webhook` responses (`400` on malformed body, `202`
  counters), `Subscriber` model
- `event-delivery`: at-most-once wording admits GitHub-retry duplicates; `lag`
  event
- `cli`: validation rules, exit-code contract, stream timeout, secret sources,
  disconnect notices

## Impact

`src/gh_babysitter/server/**`, `src/gh_babysitter/cli/**`, tests, `README.md`,
specs, `CHANGELOG.md`, version `1.0.0`.
