# Implementation plan — 2026-07-20

Implements [docs/specs](../specs/01-architecture.md). This file resolves the spec's
open questions with the drafts and pins module interfaces. Specs win on conflict,
except where a draft is explicitly promoted to a decision here.

## Decisions (open questions resolved)

| Question | Decision |
|---|---|
| Event envelope | Draft from [delivery](../specs/02-delivery.md#формат-события) as-is: `{ts, repo, event, action, number, payload}` |
| Event menu | `issues, pull_request, issue_comment, pull_request_review, release` |
| `--until` matrix / exit codes | Draft from [cli](../specs/05-cli.md#exit-условия) as-is |
| Recheck period | 300 s default, env-configurable; recheck bypasses the auth cache (fresh GitHub call) and refreshes it |
| `serve` in the same extension | Yes: `gh-babysitter serve` runs uvicorn — one distribution, three commands |
| CLI finds the server via | `--server URL` flag, env `GH_BABYSITTER_SERVER`, default `http://localhost:8000` |

## Dependencies

Runtime: `fastapi`, `sse-starlette`, `uvicorn`, `httpx`, `typer`.
Dev (add to `dev` group): `pytest-asyncio`, `respx`, `anyio`.

## Server — `src/gh_babysitter/server/`

- `config.py` — frozen `Settings` dataclass + `Settings.from_env()`. Env vars
  (prefix `GH_BABYSITTER_`): `WEBHOOK_SECRET` (no default), `GITHUB_API_URL`
  (`https://api.github.com`), `AUTH_CACHE_TTL` (300), `RECHECK_INTERVAL` (300),
  `PING_INTERVAL` (30), `QUEUE_MAXSIZE` (256).
- `events.py` — `EVENT_MENU: frozenset[str]` (menu above) and number extraction
  per [normalization table](../specs/01-architecture.md#нормализация-payload):
  `issues`/`issue_comment` → `payload.issue.number`, `pull_request` /
  `pull_request_review` → `payload.pull_request.number`, else `None`.
- `signature.py` — `verify_signature(secret: str, body: bytes, header: str | None) -> bool`.
  Header format `sha256=<hex>`; `hmac.compare_digest`; missing/malformed → False.
- `normalize.py` — `Normalized(repo, event, action, number)` frozen dataclass;
  `normalize(event: str, payload: dict) -> Normalized | None` (None when
  `repository.full_name` is absent). `action` = `payload.get("action")`.
- `registry.py` — `Filter(repo, event, action, number)` frozen dataclass
  (`action`/`number` optional). `Registry` (plain dicts, single event loop, no
  locks): `connections: dict[int, Connection]` (login, filters, `asyncio.Queue`),
  `index: dict[tuple[str, str], set[int]]`. Methods `register`, `unregister`
  (idempotent), `match(norm) -> list[asyncio.Queue]` — deduped per connection: a
  filter matches when repo and event equal and each of action/number is `None`
  in the filter or equal.
- `auth.py` — `GitHubAuthenticator(api_url, cache_ttl, client: httpx.AsyncClient)`:
  `verify(token, repo) -> str | None` (login on success). `GET /user` for
  identity, `GET /repos/{repo}` for visibility; any non-200 → None. TTL cache
  keyed `(sha256(token), repo)`; `verify(..., fresh=True)` bypasses and refreshes
  it (used by the recheck loop). Send `Accept: application/vnd.github+json`.
- `app.py` — `create_app(settings, registry=None, authenticator=None) -> FastAPI`
  (injectable for tests; authenticator's httpx client managed via lifespan).
  - `POST /webhook`: raw body → HMAC check (secret unset ⇒ always 401) else 401
    without parsing. `X-GitHub-Event: ping` → 200 `{"ok": true}`. Normalize;
    no repo → 204. Match → `queue.put_nowait` each, `QueueFull` ⇒ drop that
    connection's event (at-most-once). Reply 202 `{"matched": n}`.
  - `GET /events/stream?repo=&events=&number=&action=`: Bearer token required
    (401). `repo` must be `owner/name`, `events` comma-list ⊆ `EVENT_MENU`,
    non-empty (422 otherwise; FastAPI validation). `verify(token, repo)` → None ⇒
    403. Register one `Filter` per event type (shared repo/action/number), one
    `asyncio.Queue(maxsize)` per connection.
  - Response: `sse_starlette.EventSourceResponse`, `ping=settings.ping_interval`.
    First message: `event: ready`, data `{"filters": [...]}` (lets the client log
    "subscribed" and lets tests synchronize). Then envelope messages (default
    event type), `data` = JSON envelope.
  - Recheck: generator loop `asyncio.wait_for(queue.get(), timeout=<time to next
    recheck>)`; on timeout when recheck due → `verify(token, repo, fresh=True)`;
    None ⇒ close stream. Unregister in `finally`.
- `main.py` (replace template stub) — `run(host, port)`: build settings from env,
  warn to stderr when `WEBHOOK_SECRET` unset, `uvicorn.run`.

## CLI — `src/gh_babysitter/cli/`

- `token.py` — `resolve_token()`: `GH_TOKEN` → `GITHUB_TOKEN` → `gh auth token`
  (subprocess). None ⇒ typer.Exit(1) with a hint to run `gh auth login`.
- `durations.py` — `parse_duration("12h" | "90m" | "45s" | "1h30m" | "300") -> float`
  seconds; bare number = seconds; invalid ⇒ usage error.
- `sse.py` — minimal SSE parser over an async byte-line iterator → yields
  `(event_type, data_str)`; handles multi-line `data:`, `event:`, comments,
  blank-line dispatch. No external SSE client dep.
- `until.py` — `UNTIL_MATRIX` per the [spec draft](../specs/05-cli.md#матрица---until-черновик):
  - required subscription events: `merged` → `{pull_request}`, `closed` →
    `{pull_request, issues}`, `approved`/`changes_requested` → `{pull_request_review}`.
  - `satisfied_by_event(until, envelope) -> bool` — exact draft matrix.
  - `satisfied_by_poll(until, client, repo, number) -> bool` — boundary poll with
    the user's token against GitHub API: `merged` → `GET /repos/{r}/pulls/{n}`
    `.merged`; `closed` → `GET /repos/{r}/issues/{n}` `.state == "closed"` (works
    for PRs too); `approved`/`changes_requested` → `GET /repos/{r}/pulls/{n}/reviews`
    contains a review with state `APPROVED`/`CHANGES_REQUESTED`.
- `listen.py` — core logic `async listen(opts, client_factory) -> int` returning
  the exit code; the Typer command wraps it in `asyncio.run`. `client_factory`
  is injectable (tests pass `httpx.ASGITransport`-backed clients).
  - Validation: `--until` requires `-n`; `--until` auto-adds its required events
    to `-E`; without `--until`, `-E` is required; `--first-event` = `--count 1`
    (error if both). `--action`, `--number` optional pass-through.
  - Flow: deadline = now + timeout (if any); whole run wrapped in
    `asyncio.timeout(remaining)` ⇒ exit 124. If `--until`: poll first — satisfied
    ⇒ exit 0. Connect to `{server}/events/stream` with Bearer token, stream SSE:
    `ready` → stderr note; envelope → print compact JSON line to stdout, flush;
    evaluate `--until` / decrement `--count` ⇒ exit 0 when met. Disconnect ⇒
    re-poll (if `--until`), reconnect with exponential backoff (1 s → cap 30 s,
    ±20 % jitter), resetting backoff after a successful connect. HTTP 401/403
    from server ⇒ fatal message to stderr, exit 1 (no retry).
  - `--format pretty` ⇒ `"{ts} {repo} {event}.{action} #{number}"` to stdout
    instead of JSON.
- `setup.py` — `setup --org X --url URL [--events a,b] [--secret S]`: admin's own
  token via `resolve_token()`. `GET /orgs/{org}/hooks` (paginate) → hook with the
  same `config.url` exists ⇒ `PATCH`, else `POST`. Body: `name: "web"`, `active:
  true`, allowlist events, `config: {url, content_type: "json", secret}`. Secret
  default `secrets.token_hex(32)`; printed exactly once with instructions to set
  `GH_BABYSITTER_WEBHOOK_SECRET` on the server. Token is used and forgotten.
- `main.py` — Typer app: `listen`, `setup`, `serve` (thin uvicorn wrapper over
  `server.main.run`). `[project.scripts] gh-babysitter = "gh_babysitter.cli.main:main"`.

## gh extension entrypoint

Executable `gh-babysitter` (bash) at repo root: resolve own directory, `exec uv
run --directory <dir> gh-babysitter "$@"`; clear error if `uv` is missing.

## Tests (template conventions: `tests/unit/gh_babysitter/…`, coverage ≥ 80)

Add `asyncio_mode = auto` for pytest-asyncio. Unit: signature (good/bad/missing/
malformed header), normalize (all 5 menu events incl. issue_comment on a PR,
release without number, missing action, missing repository), registry (match by
repo/event/action/number, dedupe across overlapping filters, unregister,
QueueFull dropping), auth (respx: 200/401/404 paths, cache hit avoids second
call, TTL expiry, `fresh=True` bypass), durations, until (event matrix + poll
paths via respx), SSE parser (multi-line data, comments, event types).
Integration (`tests/integration/`, httpx `ASGITransport` + anyio task groups, fake
authenticator injected): webhook HMAC 401 / ping 200 / 202 matched; stream 401
without token, 403 unknown repo, 422 bad events; end-to-end webhook→SSE delivery
with action/number filtering; one delivery for overlapping filters; recheck
closing the stream after access revocation (short intervals via settings).
CLI: validation rules, `listen` core against the real app via ASGITransport
(count/first-event/until/timeout-124 paths), pretty format, setup create+update
paths via respx.

## E2E (live, not in CI) — `scripts/e2e.sh`

Needs: network, `gh` auth, `gh webhook` extension. Creates a temp **private**
repo `gh-babysitter-e2e-<epoch>` under the current user; starts `serve` on a
random port with a generated secret; `gh webhook forward --repo=… --events=<menu>
--url=http://127.0.0.1:<port>/webhook --secret=…`; then:
1. `listen -E issues --count 1 --timeout 120` + create an issue via `gh api` ⇒
   exit 0, stdout JSON has `event=issues`, `action=opened`.
2. `listen -n <issue> --until closed --timeout 120` + close the issue ⇒ exit 0.
3. Boundary poll: same `--until closed` on the already-closed issue ⇒ exit 0
   with no stream event needed.
Cleanup always: kill children, `gh repo delete --yes` (may fail without the
`delete_repo` scope — then print a manual-cleanup warning). Fail loudly on any
step; print a PASS/FAIL summary.
