# v1.0 release plan

Closes the findings of [#21](https://github.com/Alex-Kopylov/gh-babysitter/issues/21) — a black-box
E2E audit plus two verification passes — and makes the project releasable as `1.0.0`.

## Root cause

Three separately-reported P0/P1 symptoms are one design gap: **the system has no vocabulary for
"transient" versus "permanent".**

| Layer | Collapse | Consequence |
|---|---|---|
| `cli/listen.py:144` | every `httpx2.HTTPError` and every non-401/403 status → silent retry | 422/404/400/500 retried forever, empty stderr |
| `server/auth.py:51` | "denied" and "GitHub is down" both → `None`, both cached 300 s | one transient 403 locks a token out for 5 minutes |
| `server/app.py:91` | "delivered" and "dropped" both → counted in `matched` | slow consumer loses events, both sides told it succeeded |

Fixing this as one three-state concept (`allowed` / `denied` / `unavailable`) is cheaper and smaller
than seven independent patches, so the plan is organised around it.

## Also diagnosed here

The audits disagreed on reconnect behaviour: SIGKILL reconnected in ~1 s, SIGINT stranded the
listener for the full 45 s observation window. The mechanism is `cli/listen.py:170`, which sets
`read=None` on the SSE client. On SIGKILL the OS resets the socket, so the client notices at once. On
graceful shutdown uvicorn holds the connection open while the SSE generator sits in its 300 s recheck
wait, and the client — with no read deadline — blocks forever on a half-dead socket. The server
already emits a `: ping` keepalive every 30 s, so a bounded read timeout is both safe and sufficient.

## Decisions taken for this release

1. **README in English; `docs/specs/*.md` stay Russian.** README is the public entry point for a
   `gh` extension; the specs are internal design documents.
2. **No delivery-ID deduplication.** At-most-once is kept as a *no-state* guarantee. The contract is
   corrected in the docs instead: GitHub redelivery can produce duplicates, and consumers must be
   idempotent. This resolves the "at-most-once is ambiguous or violated" finding by fixing the claim
   rather than the code.
3. **No LICENSE file in this release.** `docs/specs/01-architecture.md` keeps `LICENSE: TBD`.

## Exit-code contract (target)

| Code | Meaning |
|---|---|
| `0` | Success: exit condition met (`--until` / `--count` / `--first-event`) |
| `1` | Runtime failure: token rejected, subscription permanently refused, protocol error |
| `2` | Usage error: invalid flags, malformed repo, non-positive number/timeout (Typer default) |
| `124` | `--timeout` expired |

---

## Dispatch A — server hardening

Files: `server/auth.py`, `server/app.py`, `server/registry.py`, `server/main.py`, and their tests.

### A1. Three-state authorization verdict (`server/auth.py`)

Replace the `str | None` return with an explicit verdict:

```python
class Verdict(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Access:
    verdict: Verdict
    login: str | None = None
```

`Authenticator` protocol becomes `async def verify(self, token: str, repo: str, *, fresh: bool = False) -> Access`.

Upstream status mapping, applied identically to `GET /user` and `GET /repos/{repo}`:

| Upstream | Verdict | Cached? |
|---|---|---|
| `200` (both calls succeed, `login` is a `str`) | `ALLOWED` | yes |
| `401` | `DENIED` | yes |
| `404` | `DENIED` | yes |
| `403` **without** rate-limit markers | `DENIED` | yes |
| `403` **with** rate-limit markers | `UNAVAILABLE` | **no** |
| `429` | `UNAVAILABLE` | **no** |
| `5xx` | `UNAVAILABLE` | **no** |
| `httpx2.HTTPError` (transport/timeout) | `UNAVAILABLE` | **no** |
| any other non-`200` | `DENIED` | yes |

Rate-limit markers: `x-ratelimit-remaining == "0"` or a `retry-after` header present.

**The cache rule is the fix**: only `ALLOWED` and `DENIED` are ever written to `self._cache`.
`UNAVAILABLE` must not be cached, so the very next request retries GitHub for real.

### A2. Verdict → HTTP status (`server/app.py::_stream`)

- `ALLOWED` → proceed, using `access.login`.
- `DENIED` → `403 {"detail": "Repository access denied"}` (unchanged).
- `UNAVAILABLE` → `503 {"detail": "GitHub API unavailable"}` with a `Retry-After: 5` header.

This is the "rate limit mislabeled as access denied" fix: a rate-limited or unreachable GitHub is now
distinguishable by the client from a genuine authorization failure.

In the periodic recheck loop (`app.py:137`): close the stream **only** on `DENIED`. On `UNAVAILABLE`,
keep the connection open and shorten the next recheck to `min(recheck_interval, 30)` seconds. A
GitHub outage must not tear down a subscription an agent has been holding all night.

### A3. Webhook payload hardening (`server/app.py::_webhook`)

Replace the unguarded `await request.json()` — the body is already in hand as `body`:

```python
try:
    payload = json.loads(body)
except ValueError:
    raise HTTPException(status_code=400, detail="Malformed JSON payload") from None
if not isinstance(payload, dict):
    raise HTTPException(status_code=400, detail="Payload must be a JSON object") from None
```

`400` (not `422`) — `422` already means "invalid query parameters" on `/events/stream`; keep the two
distinguishable. Must stay *after* signature verification, so unsigned garbage still gets `401` and is
never parsed. Must not emit an application traceback.

Covers all reported cases: `{`, empty body, `null`, `[]`, `"str"`, `42`.

### A4. Honest delivery accounting (`server/registry.py`, `server/app.py`)

Introduce a `Subscriber` that owns the queue and its own loss counter:

```python
@dataclass
class Subscriber:
    queue: asyncio.Queue[dict[str, Any]]
    dropped: int = 0

    def offer(self, envelope: dict[str, Any]) -> bool:
        try:
            self.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.dropped += 1
            return False
        return True

    def take_dropped(self) -> int:
        count, self.dropped = self.dropped, 0
        return count
```

`Connection` holds a `Subscriber` instead of a bare queue; `Registry.register` accepts one and
`Registry.match` returns `list[Subscriber]`.

`_webhook` reports what actually happened:

```python
subscribers = registry.match(norm)
delivered = sum(subscriber.offer(envelope) for subscriber in subscribers)
return JSONResponse(
    {"matched": len(subscribers), "delivered": delivered, "dropped": len(subscribers) - delivered},
    status_code=202,
)
```

`_stream`'s `event_generator` tells the *affected listener* too. At the top of each `while True`
iteration, before waiting on the queue:

```python
if dropped := subscriber.take_dropped():
    yield {"event": "lag", "data": json.dumps({"dropped": dropped}, separators=(",", ":"))}
```

A stalled consumer therefore learns its exact loss count the moment it resumes reading.

### A5. Bounded graceful shutdown (`server/main.py`)

`uvicorn.run(..., timeout_graceful_shutdown=5)`. SSE generators never finish on their own, so without
a bound uvicorn waits indefinitely on SIGINT — the server half of the stranded-listener bug. Add a
short comment saying exactly that.

### A6. Required test coverage

The audit noted that `test_app.py` exercises helpers and the route table but never the handler. Close
that specifically:

- `_webhook` handler, correctly signed, with each of `{`, `` (empty), `null`, `[]`, `"str"`, `42`
  → `400`, no traceback, process healthy afterwards.
- `verify` against `403`+rate-limit, `429`, `500`, and a raised `httpx2.HTTPError` → `UNAVAILABLE`,
  **and** a follow-up call after upstream recovery returns `ALLOWED` *and issues a real API call*
  (assert the call count — this is the regression that matters).
- `verify` against `401` and `404` → `DENIED`, cached, no second API call.
- `/events/stream` returns `503` on `UNAVAILABLE` and `403` on `DENIED`.
- Recheck loop: `DENIED` closes the stream; `UNAVAILABLE` leaves it open.
- Queue-full: with `queue_maxsize=3` and 7 events to one stalled subscriber, assert
  `delivered == 3`, `dropped == 4` across the responses, and that the listener receives a `lag`
  event reporting `4` once it resumes.

---

## Dispatch B — CLI hardening

Files: `cli/listen.py`, `cli/main.py`, `cli/config.py`, `cli/setup.py`, `cli/durations.py`, tests.

### B1. Local validation before any network activity (`cli/listen.py::_validated`)

All raise `typer.BadParameter` (exit `2`), all before a socket is opened:

- `--repo` must match `owner/name`: exactly one `/`, both halves non-empty, characters limited to
  `[A-Za-z0-9._-]`. Rejects `not-a-repo`, `owner/repo/extra`, `/repo`, `owner/`.
- `--number` must be `>= 1`. `-n 0` / `-n -1` currently subscribe successfully and can then never
  match anything — a healthy-looking listener that is permanently dead.
- `--timeout` must be `> 0`. `parse_duration("0")` and `parse_duration("0s")` both yield `0.0`, which
  today produces an instant exit `124` instead of a usage error. Reject in `parse_duration` so both
  spellings are covered.
- `--action`, when given, must be non-empty.
- `--server` must parse to an `http`/`https` URL with a host.

### B2. Plain-HTTP token egress guard (`cli/listen.py`)

The CLI sends `Authorization: Bearer <github token>` to whatever `--server` /
`GH_BABYSITTER_SERVER` names, with no scheme check. Refuse `http://` to a **non-loopback** host
unless `GH_BABYSITTER_INSECURE=1`:

```
error: refusing to send a GitHub token over plain HTTP to <host>;
       use https:// or set GH_BABYSITTER_INSECURE=1
```

Loopback (`localhost`, `127.0.0.0/8`, `::1`) stays allowed unconditionally — that is the documented
dev workflow and `scripts/e2e.sh` depends on it.

### B3. HTTP error classification — the P0 (`cli/listen.py`)

Delete the blanket `except httpx2.HTTPError: pass`. Classify explicitly:

```python
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class _Outcome:
    exit_code: int | None = None      # not None -> stop now
    retry_reason: str | None = None   # not None -> transient, back off and retry
```

- `401` / `403` → `_Outcome(exit_code=1)`, message unchanged.
- status in `_RETRYABLE_STATUS`, or any `5xx` → `_Outcome(retry_reason=f"server returned {status}")`.
- any other `4xx` → **permanent**: `_Outcome(exit_code=1)`. Before returning, `await response.aread()`
  and surface the server's JSON `detail` if present, so a `422 Invalid repository` reaches the
  operator instead of vanishing into an infinite loop.
- otherwise → stream.

Backoff reset must move. Today `backoff = 1.0` is reset as soon as a response is not 401/403, so a
server stuck on `503` is hammered once per second forever. Reset it only after the stream actually
delivered its `ready` event — have `_consume_stream` return whether it saw `ready`.

### B4. Disconnect visibility (`cli/listen.py`)

Every reconnect writes one line to stderr naming cause and delay:

```
warning: disconnected (<reason>); events during the gap are lost; reconnecting in 1.0s
```

Keep the existing `subscribed` line exactly as-is on every successful subscribe — `scripts/e2e.sh`
greps for it. The missing signal was the *disconnect* notice, not the resubscribe.

Also surface server-side loss (pairs with A4):

```python
if event_type == "lag":
    print(f"warning: server dropped {json.loads(data)['dropped']} events (consumer too slow)", file=sys.stderr)
    continue
```

### B5. Bounded stream read timeout (`cli/config.py`, `cli/listen.py`)

Add to CLI `Settings`:

```python
stream_timeout: float = Field(default=90, validation_alias="GH_BABYSITTER_STREAM_TIMEOUT")
```

Use it as `read=` instead of `None`. The server pings every 30 s by default, so 90 s tolerates three
missed pings. A read timeout raises `httpx2.ReadTimeout`, which B3 already classifies as transient —
so a graceful server shutdown now reconnects instead of stranding the listener.

`docs/specs/05-cli.md` currently states the read timeout is always unbounded; that claim must change.

### B6. Clean stream teardown (`cli/listen.py::_consume_stream`)

`RuntimeError: generator didn't stop after athrow()` fires on roughly half of all success-path exits.
The frames are in `httpcore2`'s pool byte-stream finalization: the SSE generator is abandoned
mid-iteration and finalized by GC after the connection pool has moved on. Close it deterministically
inside the response context:

```python
async with aclosing(parse_sse(response.aiter_lines())) as events:
    async for event_type, data in events:
        ...
```

Regression test must be a **subprocess** test: the existing integration tests use `ASGITransport`, so
`httpcore2`'s pool is never involved and cannot reproduce it. Start a real `uvicorn` on an ephemeral
loopback port, run the CLI to a `--count 1` success, assert exit `0` **and** that stderr contains
neither `RuntimeError` nor `GeneratorExit`.

If `aclosing` does not eliminate it, **do not suppress the traceback**. Report back with the residual
stack instead — an upstream `httpcore2` issue is the correct outcome, not a swallowed exception.

### B7. `setup` secret hygiene (`cli/main.py`, `cli/setup.py`, `cli/config.py`)

The secret is currently a `--secret <value>` option (visible in `ps`, persisted in shell history) and
is echoed to stdout unconditionally (lands in agent transcripts).

- Remove `--secret`. Add `--secret-stdin` (boolean), which reads and strips all of stdin; empty input
  is a `BadParameter`.
- Add `webhook_secret: SecretStr | None` to CLI `Settings`, alias `GH_BABYSITTER_WEBHOOK_SECRET`.
- Precedence: `--secret-stdin` → `GH_BABYSITTER_WEBHOOK_SECRET` → generate.
- Output rules: a **generated** secret is printed to stdout once, because that is its only copy. A
  **supplied** secret is never echoed — print `webhook configured; reusing the supplied
  GH_BABYSITTER_WEBHOOK_SECRET` instead.

### B8. Required test coverage

- Each `_validated` rejection above → exit `2`, no socket opened.
- `400`/`404`/`422` → exit `1` with the server's `detail` on stderr, **bounded** (assert the request
  was issued exactly once — no retry).
- `500`/`503`/`429` → retried, with a disconnect warning on stderr each time.
- `401`/`403` → exit `1`, unchanged message.
- Backoff is not reset by a repeated retryable status.
- `lag` event → stderr warning naming the count.
- Plain-HTTP guard: non-loopback `http://` refused; loopback and `GH_BABYSITTER_INSECURE=1` allowed.
- `setup`: stdin and env sources both work; a supplied secret never appears in stdout; a generated one
  does.

---

## Dispatch C — documentation and release

### C1. `README.md` → English rewrite

The current README says «Статус: спецификация. Имплементации пока нет», calls the CLI name
provisional, and has no install command — while a working `gh-babysitter` artifact exposes `listen`,
`setup`, and `serve`. Rewrite in English, keeping the ASCII architecture diagram:

- Status: implemented, `1.0.0`.
- Problem / solution (diagram retained).
- **Install**: `gh extension install Alex-Kopylov/gh-babysitter`, plus the `uv tool install` path.
  Prerequisites: `uv`, Python 3.14, `gh auth login`.
- **Quickstart**: `serve` → `setup` → `listen`, with the secret flowing via
  `GH_BABYSITTER_WEBHOOK_SECRET`.
- **Agent scenario**: the `--until merged --timeout 12h` example.
- **Configuration**: one table each for server and CLI environment variables, including the new
  `GH_BABYSITTER_STREAM_TIMEOUT` and `GH_BABYSITTER_INSECURE`.
- **Exit codes**: the table above.
- **Delivery guarantees**: at-most-once; no replay, no history; **GitHub redelivery can produce
  duplicates — consumers must be idempotent**; `lag` notices on consumer overrun; the reconnect blind
  window and the `--until` boundary poll that mitigates it.
- **Security**: HMAC on ingress, token never written to disk, plain-HTTP guard, `setup` secret
  sources.
- Non-goals (kept), and a link to the Russian specs.

### C2. Spec updates (stay in Russian)

- `02-delivery.md` — at-most-once wording admits GitHub-retry duplicates; add the `lag` event and the
  `matched`/`delivered`/`dropped` response shape.
- `05-cli.md` — local validation rules; correct the "read timeout is always unbounded" claim and add
  `GH_BABYSITTER_STREAM_TIMEOUT` to the timeout table; `setup` secret sources; exit-code table gains
  `2`; disconnect notices.
- `03-auth.md` — three-state verdict, `503` versus `403`, and the rule that transient failures are
  never cached negatively.
- `01-architecture.md` — `/webhook` responses (`400` on malformed body, `202` body shape), and the
  `Subscriber` model. Keep `LICENSE: TBD`.
- `README.md` «Открытые вопросы» — resolve the questions this release answers (event format, event
  allowlist, `--until` matrix, recheck period, `serve` packaging) rather than carrying them into 1.0.

### C3. Version

- `pyproject.toml` → `version = "1.0.0"`.
- `src/gh_babysitter/__init__.py` → real docstring plus `__version__ = "1.0.0"` (its current
  docstring still says "Starter package for the project template").
- Add `gh-babysitter --version`.

### C4. `CHANGELOG.md`

New file, Keep a Changelog format, one `1.0.0` entry grouped as Added / Changed / Fixed / Security,
crediting #21.

---

## Release gate

Taken from the issue, plus what this plan adds:

- [ ] Every P0/P1 resolved or explicitly documented.
- [ ] `mise run lint` and `mise run test-cov` green; coverage does not regress.
- [ ] Subprocess regression test proves a clean success-path teardown.
- [ ] `scripts/e2e.sh` passes against a live disposable repository.
- [ ] README install path reproduced from scratch on a clean machine.
