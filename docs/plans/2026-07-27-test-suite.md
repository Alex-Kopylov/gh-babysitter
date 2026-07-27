# Test-suite reorganization and coverage plan

Follow-up to `2026-07-27-v1-release.md`. The suite grew 143 → 224 tests across four parallel
workstreams; nobody owned test architecture during that. This plan fixes structure and, more
importantly, **power**.

## The actual problem is power, not layout

Mutation testing against shipped v1.0 behaviour: **seven mutations pass 224 green tests unchanged.**

| Mutation | Source | Scenario |
|---|---|---|
| Delete the post-disconnect `--until` boundary poll | `cli/listen.py:266-272` | 19 |
| Stop sending the `number` filter | `cli/listen.py:249-250` | 8 |
| Stop sending the `action` filter | `cli/listen.py:251-252` | 14 |
| Disable the SSE `: ping` keepalive | `server/app.py:187` | 23 |
| `ensure_ascii=True` when encoding envelopes (server) | `server/app.py:183` | 26 |
| `ensure_ascii=True` when encoding envelopes (CLI) | `cli/listen.py:162` | 26 |
| `flush=False` on stdout writes | `cli/listen.py:162` | 16 |
| Reverse subscriber match order | `server/registry.py:95` | 16 |

The first is the worst: it is the **documented mitigation for the reconnect blind window**, the reason
scenario 19 is considered acceptable at all. Verified independently — deleting the block leaves 224
tests green.

Two tests were also found to be powerless (they pass with the code they guard reverted):

- `tests/unit/gh_babysitter/cli/test_listen.py:313-343` — its fail → ready-then-fail → success
  sequence yields `sleeps == [1.0, 1.0]` under both the guarded and unguarded implementation.
- (Already fixed in `3bb361e`: the teardown regression test sent its whole SSE body in one chunk.)

## Structure: five tiers, two directories

`tests/unit/` correctly mirrors `src/` and **must not be touched**. `tests/integration/` is a bag of
three different tiers:

| File | Actual tier |
|---|---|
| `integration/test_cli_resilience.py` | `MockTransport` only — no app, no socket. This is a **unit** test |
| `integration/test_server.py` | real app over `ASGITransport` — genuinely integration |
| `integration/test_cli_listen.py` | real app over `ASGITransport` — genuinely integration |
| `integration/test_cli_teardown.py` | real uvicorn + real CLI subprocess — a **system** test |

Timing confirms the mismatch: the suite runs in 9.8s serial, of which **8.44s is the single
subprocess test**. It must be independently selectable.

There is **no `conftest.py` anywhere**, yet `.ruff.toml:86-87` already carries per-file-ignores for
`tests/**/conftest.py` and `tests/constants.py` — both nonexistent. The lint config was written for a
fixture layer nobody built. Consequences: `_FakeAuthenticator` duplicated, `wait_until_registered`
byte-identical in two files, **21** inline `monkeypatch.setattr(listen, "resolve_token", …)`, **6**
copies of the backoff harness, **16** copies of the envelope literal.

## Target layout

```
tests/
  conftest.py                   NEW  shared doubles and factories
  unit/gh_babysitter/           UNCHANGED
    cli/test_listen_retry.py    MOVED from integration/test_cli_resilience.py
  integration/                  "real app, ASGITransport, no sockets"
    conftest.py                 NEW
  system/                       NEW "real sockets, real processes"
    conftest.py                 NEW
    test_cli_teardown.py        MOVED from integration/
```

`tests/conftest.py`: `FakeAuthenticator` (the superset with `calls`/`rechecks`/`revoked`),
`server_settings(**overrides)`, `make_app(...)`, `sign(body)` / `webhook_headers(...)`,
`deliver(app, event, payload)`, `wait_until_registered(registry)`, a `fake_token` fixture, a
`deterministic_backoff` fixture yielding the recorded `sleeps` list, and `envelope(**overrides)` /
`sse_body(*frames)` builders.

`tests/system/conftest.py`: `free_port`, `uvicorn_server(app_target)`, `run_cli(*args, env=...)`.

## Phase 0 — structure and safety nets

No new assertions; the suite must stay green.

1. Add `tests/conftest.py` and `tests/system/conftest.py`; delete the duplicated helpers above.
2. Add `pytest-timeout`; set `.pytest.ini` `addopts = -n auto --strict-markers --timeout=60` and
   register a `system` marker. **Rationale: during analysis a one-line source change hung the suite
   indefinitely, twice. With `-n auto` and a real-socket tier there is nothing to stop that in CI.**
3. Moves: `test_cli_resilience.py` → `unit/gh_babysitter/cli/test_listen_retry.py`;
   `test_cli_teardown.py` → `system/`, **updating the uvicorn target string to
   `tests.system.test_cli_teardown:app`** (hard coupling); the teardown-handler unit test
   (`test_cli_teardown.py:79-103`) → `unit/gh_babysitter/cli/test_listen.py`.
4. Delete `test_listen.py:313-343` and its four orphaned doubles at `:54-90` (powerless — see above);
   delete `test_listen.py:137-146` (strictly dominated by `test_cli_resilience.py:66-87`, which
   parametrizes 401 *and* 403 and asserts the request count); delete the dead `raise_for_status`
   stub at `:33-34`.
5. Raise `fail_under` 80 → 93 in `pyproject.toml` (actual is 95.63). Drop or populate the
   `tests/constants.py` ruff ignore.

## Phase 1 — kill the surviving mutants

Cheap tiers only. Each item must fail when the named source line is reverted.

6. `--until` boundary poll after a disconnect — ASGI.
7. CLI actually sends `number` and `action` — unit assertion on the outgoing query params, plus one
   ASGI end-to-end. Closes scenario 14.
8. Server emits `: ping` — ASGI with `ping_interval=0.05`. Half of 23.
9. Unicode / newline / ANSI / ~1MB body round-trip, including a payload containing a literal
   `data: ` line and `\n\n` — the SSE-injection case. Unit + ASGI. Closes 26.
10. Burst ordering — ASGI. Half of 16.
11. Two-listener fan-out, non-matching exclusion, second-repo exclusion — ASGI. Closes 10, most of 7.
12. 50 subscribers × 100 events — ASGI. Closes 28.
13. Issue-vs-PR reverse matrix — unit. Closes 9.

## Phase 2 — system tier

Needs Phase 0's fixtures and the timeout guard.

14. JSONL framing over a real pipe (`flush=True` only matters when stdout is not a tty). Rest of 16.
15. SIGINT/SIGTERM, CLI side and server side. Closes 13. Server-side SIGINT is the asymmetry from
    issue #21; measured behaviour to assert: detection ≈0-1s, resubscribe ≈1-2s, exactly one
    `warning: disconnected`. **Kill the server process directly, never the `uv run` wrapper — `uv`
    forwards SIGINT but not SIGKILL, leaving an orphaned server that still serves.**
16. Keepalive soak: `GH_BABYSITTER_STREAM_TIMEOUT=3`, `GH_BABYSITTER_PING_INTERVAL=1`, ~10s, assert
    zero `warning: disconnected`. Rest of 23.

## Phase 3 — decisions, not code

17. Scenario 6 needs the #37 low-privilege credential — genuinely blocked.
18. Implement the `stream_timeout <= ping_interval` startup warning (issue #21 checklist, never built).
19. Scenario 25 (rename/transfer/delete): document as unsupported rather than building a fixture.
20. Rename the teardown regression test to say it proves the *filter* works, and put the #34 link in
    the comment at `cli/listen.py:66-67`.

## Scope correction for #37

Of the five scenarios filed as "open pending fixtures", only **6** and **25** actually need GitHub
fixtures. **9** and **14** need none (pure unit + ASGI), and **7** is mostly local — only the
"GitHub delivers repo B to the same endpoint" assertion needs a second repository. #37 is blocking
roughly 1.5 scenarios, not 4.

## Not worth changing

- `tests/unit/` mirroring `src/` — correct as-is.
- Splitting `test_server.py` further — it loses ~60 lines to `conftest.py` anyway.
- The direct `await _stream(request, ...)` style at `test_server.py:186,357` — white-box and ugly,
  but the only way to step the SSE generator deterministically. Keep it; add a comment saying why.
- `-n auto` — benchmarked at 10.83s parallel vs 11.43s serial; a wash. Leave it, but the `--timeout`
  flag is what makes it safe.
- Issues #13/#15 (duplicated server tests) — already resolved; `test_app.py` is 54 lines with zero
  overlap. Close them.
