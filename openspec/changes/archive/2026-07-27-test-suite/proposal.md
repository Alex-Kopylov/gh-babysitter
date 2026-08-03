> Historical change, migrated 2026-08-03 from `docs/plans/2026-07-27-test-suite.md`
> during the docs → OpenSpec transfer. The original plan is preserved verbatim
> in [design.md](design.md). Test-only change: no capability requirements
> affected.

## Why

The suite grew 143 → 224 tests across four parallel workstreams with nobody
owning test architecture. Mutation testing against shipped v1.0 behaviour left
five effective mutations passing 224 green tests — including deletion of the
`--until` boundary poll, the documented mitigation for the reconnect blind
window.

## What Changes

- Shared fixture layer (`tests/conftest.py`, `tests/system/conftest.py`)
  replacing duplicated doubles and helpers.
- Tier split: mislabeled unit test moved out of `integration/`, subprocess
  test into a new `system/` tier, `pytest-timeout` guard, coverage gate
  80 → 93.
- Mutation-killing tests for the boundary poll, `number`/`action` filters,
  SSE keepalive, stdout flushing, fan-out and burst ordering.
- System tier: JSONL framing over a real pipe, SIGINT/SIGTERM behaviour,
  keepalive soak.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- None (test infrastructure only).

## Impact

`tests/**`, `pyproject.toml` (coverage gate), `.pytest.ini`, `.ruff.toml`.
