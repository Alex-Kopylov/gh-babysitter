# gh-babysitter

gh-babysitter is an implemented webhook gateway and command-line client for
filtered GitHub event streams. The current release is `1.0.0`.

## Problem

GitHub webhooks deliver to endpoints configured in advance. Creating those
hooks requires administrative access, and GitHub limits how many hooks can be
attached to an organization or repository. Giving every developer or agent a
separate hook does not scale, and webhook configuration can filter only by
event type, not by repository object number or action.

## Solution

An administrator configures one organization webhook. gh-babysitter verifies
and normalizes each delivery, matches it against connected subscribers, and
fans it out over Server-Sent Events (SSE). A subscriber can filter by
repository, event type, action, and issue or pull-request number.

The v1.0 event allowlist is `issues`, `pull_request`, `issue_comment`,
`pull_request_review`, and `release`.

```text
GitHub (one organization webhook, static event-type allowlist)
   │  POST /webhook + HMAC signature
   ▼
┌────────────────── FastAPI, one process ────────────────────┐
│  Ingress ── verify HMAC ── normalize(event, action, number)│
│     │                                                       │
│  Matcher ── in-memory subscription registry                │
│     │           (= open SSE connections)                   │
│  Dispatcher ── push to matching SSE connections            │
└─────────────────────────────────────────────────────────────┘
   ▲▼ GET /events/stream — filters in query parameters (SSE)
   └────────── CLI (gh extension), auth = gh auth token ─────┘
```

Events and subscriptions are memory-only. A subscription exists for exactly
as long as its `listen` connection.

## Install

Prerequisites:

- [uv](https://docs.astral.sh/uv/)
- Python 3.14; uv can provision the interpreter
- [GitHub CLI](https://cli.github.com/) authenticated with `gh auth login`

Install as a GitHub CLI extension:

```console
gh extension install Alex-Kopylov/gh-babysitter
gh babysitter --help
```

Alternatively, install the Python command directly:

```console
uv tool install git+https://github.com/Alex-Kopylov/gh-babysitter.git@v1.0.0
gh-babysitter --help
```

The examples below use `gh babysitter`. If you used `uv tool install`, run the
same subcommands with `gh-babysitter` instead.

## Quickstart

The server must be reachable by GitHub at an HTTPS URL. Configure DNS and TLS
so that `https://hooks.example.com` reaches port 8000 on the server, then
replace the example organization and repository names below.

Export one webhook secret. Both `serve` and `setup` read the same value from
the environment:

```console
export GH_BABYSITTER_WEBHOOK_SECRET="$(
  uv run python -c 'import secrets; print(secrets.token_hex(32))'
)"
gh babysitter serve --host 0.0.0.0 --port 8000 &
gh babysitter setup \
  --org my-org \
  --url https://hooks.example.com/webhook
```

Point the client at the server and start a subscription:

```console
export GH_BABYSITTER_SERVER=https://hooks.example.com
gh babysitter listen -R my-org/api -E issues
```

Each matching webhook is printed as one JSON object per line on stdout. Stop
the process to remove the subscription. Use `--format pretty` for a
human-readable stream.

The JSON object contains normalized fields and the complete GitHub payload:

```json
{
  "ts": "2026-07-27T12:00:00Z",
  "repo": "my-org/api",
  "event": "issues",
  "action": "opened",
  "number": 42,
  "payload": {}
}
```

`action` and `number` are `null` when the GitHub event does not provide them.

## Agent scenario

An agent can wait for a specific pull request to merge, but stop after twelve
hours:

```console
gh babysitter listen \
  -R my-org/web \
  -n 24 \
  --until merged \
  --timeout 12h
```

`--until` requires `-n` and automatically subscribes to the event types
required for its target state, so `-E` is not needed in this example.

| `--until` value | Required event | Exit condition |
|---|---|---|
| `merged` | `pull_request` | `closed` action with `payload.pull_request.merged` equal to `true` |
| `closed` | `pull_request` or `issues` | `closed` action |
| `approved` | `pull_request_review` | `submitted` action with review state `approved` |
| `changes_requested` | `pull_request_review` | `submitted` action with review state `changes_requested` |

## Configuration

### Server environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `GH_BABYSITTER_WEBHOOK_SECRET` | unset | HMAC secret shared with the GitHub organization webhook; required to accept deliveries |
| `GH_BABYSITTER_GITHUB_API_URL` | `https://api.github.com` | GitHub API base URL used to verify subscriber identity and repository access |
| `GH_BABYSITTER_AUTH_CACHE_TTL` | `300` | Seconds to cache allowed or denied authorization results |
| `GH_BABYSITTER_RECHECK_INTERVAL` | `300` | Seconds between access checks for an open stream |
| `GH_BABYSITTER_PING_INTERVAL` | `30` | Seconds between SSE keepalive comments; must stay well below the client's `GH_BABYSITTER_STREAM_TIMEOUT` |
| `GH_BABYSITTER_QUEUE_MAXSIZE` | `256` | Maximum queued events per subscriber before new events are dropped |

### CLI environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `GH_BABYSITTER_SERVER` | `http://localhost:8000` | Base URL for the gh-babysitter server |
| `GH_BABYSITTER_WEBHOOK_SECRET` | unset | Secret reused by `setup` when `--secret-stdin` is not supplied |
| `GH_BABYSITTER_GITHUB_API_URL` | unset | First environment override for the GitHub API base URL |
| `GITHUB_API_URL` | unset | GitHub API base URL used when the gh-babysitter-specific value is unset |
| `GH_HOST` | unset | GitHub CLI host from which an API URL is derived when neither API URL variable is set |
| `GH_TOKEN` | unset | First environment source for the GitHub token |
| `GITHUB_TOKEN` | unset | GitHub token used when `GH_TOKEN` is unset |
| `GH_BABYSITTER_SERVER_TIMEOUT` | `10` | Connect, write, and pool timeout in seconds for server requests |
| `GH_BABYSITTER_STREAM_TIMEOUT` | `90` | Read timeout in seconds for the SSE stream; must exceed the server's `GH_BABYSITTER_PING_INTERVAL` |
| `GH_BABYSITTER_GITHUB_TIMEOUT` | `10` | Timeout in seconds for GitHub API requests |
| `GH_BABYSITTER_UNTIL_POLL_INTERVAL` | `300` | Seconds between GitHub state checks while `listen --until` remains connected |
| `GH_BABYSITTER_INSECURE` | `0` | Set to `1` to allow sending a GitHub token over plain HTTP to a non-loopback server |

An explicit `--api-url` takes precedence over API URL environment variables.
Without a token variable, the CLI runs `gh auth token`. The server reads only
`GH_BABYSITTER_GITHUB_API_URL`; it does not inherit `GITHUB_API_URL` or
`GH_HOST`.

The stream read timeout and the server keepalive are coupled. A listener treats
silence longer than `GH_BABYSITTER_STREAM_TIMEOUT` as a dead connection and
reconnects, so that value must comfortably exceed
`GH_BABYSITTER_PING_INTERVAL`; the defaults tolerate three missed pings. Raising
the ping interval above the stream timeout, or terminating the stream behind a
proxy that strips SSE comments, makes listeners reconnect on a fixed cycle and
opens a blind window each time.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | An exit condition was met: `--until`, `--count`, or `--first-event` |
| `1` | Runtime failure, such as a rejected token, permanently refused subscription, or protocol error |
| `2` | Usage error, such as invalid flags, a malformed repository, or a non-positive number or timeout |
| `124` | `--timeout` expired |

Without an exit-condition flag, `listen` continues until interrupted or until a
runtime failure occurs.

## Delivery guarantees

Delivery is at most once inside gh-babysitter: there is no replay, durable
queue, or event history. Events received while a client is offline or
reconnecting are lost.

At most once does not mean that a GitHub activity is globally unique. GitHub
can redeliver a webhook, so the same activity can reach a consumer more than
once. Consumers must be idempotent.

Each subscriber has a bounded in-memory queue. If a slow consumer overruns it,
the webhook response reports `matched`, `delivered`, and `dropped` counts. When
the consumer resumes, it receives a `lag` notice with its exact dropped-event
count; dropped events are not replayed.

The CLI prints a warning before every reconnect, including the cause, delay,
and reminder that events in the gap are lost. With `--until`, it also polls
GitHub at startup, before each reconnect, and periodically while the stream
remains connected. The periodic interval defaults to 300 seconds and is
configured with `GH_BABYSITTER_UNTIL_POLL_INTERVAL`. These checks catch a
terminal state reached before the stream opened, during the reconnect gap, or
after its terminal event was lost on an otherwise healthy connection.

## Security

- `/webhook` verifies the `X-Hub-Signature-256` HMAC before parsing the body.
- The CLI uses `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token`. Subscriber
  tokens remain in memory and are never written to disk by gh-babysitter.
- The server verifies repository access through GitHub. A genuine denial
  returns `403`; a rate-limited or unavailable GitHub API returns `503` and is
  not cached as a denial.
- The CLI refuses to send a GitHub token over plain HTTP to a non-loopback
  host. Loopback HTTP remains available for development. Set
  `GH_BABYSITTER_INSECURE=1` only for an explicitly accepted insecure
  deployment.
- `setup` reads a secret from `--secret-stdin`, then
  `GH_BABYSITTER_WEBHOOK_SECRET`, or generates one. A supplied secret is never
  echoed. A generated secret is printed once because no other copy exists.

## Non-goals

- Guaranteed delivery, replay, or offline catch-up
- Horizontal scaling or a multi-process shared registry
- Persistent subscriptions or server-side subscription management
- A separate user database, OAuth flow, or gh-babysitter-issued credentials
- A web interface

## Known limitations in 1.0.0

These were reproduced and tracked. They remain listed here so that 1.0.0
behavior and any later fixes are not left to be rediscovered.

**`--until` recovery from a lost terminal event is bounded by polling.**
In 1.0.0, terminal-state polls ran only at connection boundaries, so a terminal
event lost while the connection stayed healthy could leave `listen` waiting
until `--timeout`. Starting in 1.1.0, `listen --until` also polls GitHub while
connected, after each `GH_BABYSITTER_UNTIL_POLL_INTERVAL` (300 seconds by
default). A lost terminal event can therefore delay successful completion by
at most one interval plus the GitHub request time, rather than the full
`--timeout`. This observes current state; it does not add replay or change the
at-most-once delivery contract. Fixed in
[#42](https://github.com/Alex-Kopylov/gh-babysitter/issues/42).

**A successful exit may print an httpcore2 traceback.** Abandoning the SSE
stream on a successful exit triggers an upstream async-generator finalization
bug. gh-babysitter filters that exact signature from stderr, so it should not
be visible; if a future upstream change alters the message it will reappear.
The exit code is unaffected and is always correct. Tracked in
[#34](https://github.com/Alex-Kopylov/gh-babysitter/issues/34).

**Repository rename, transfer, and deletion are unsupported.** A subscription
matches on `repository.full_name` as delivered by GitHub. If a repository is
renamed or transferred mid-subscription, the running `listen` stops matching
and does not report why. Restart it against the new name.

**Installing from a private repository needs credentials.** While this
repository is private, `uv tool install git+https://...` fails with
`could not read Username for 'https://github.com'` unless git is configured
with credentials. `gh extension install` is unaffected, because `gh` is already
authenticated.

## Live authorization E2E

The release fixture uses two private repositories and two fine-grained personal
access tokens. Each token must be selected for exactly one fixture repository,
so the live suite proves both allowed and denied access against GitHub.

For a local run, copy `.env.e2e.example` to the ignored `.env`, replace both
token placeholders, and run:

```console
mise run e2e-local
```

After the local matrix passes, upload the same values to the current GitHub
repository without exposing them in process arguments:

```console
mise run e2e-configure-secrets
```

This command validates the live access matrix first, writes repository names as
Actions variables, and sends both token values to `gh secret set` over stdin.

The primary token needs `Webhooks` and `Issues` write permissions on its
selected repository. The secondary token needs only `Metadata` read access on
its selected repository. The harness first requires the GitHub access matrix
`A→A=200`, `A→B=404`, `B→A=404`, `B→B=200`; it then proves that both denied
repository/token pairings produce a prompt server-side `403` without opening
an event stream.

The manual and internal pull-request GitHub Actions workflow reads the
repository variables `E2E_PRIMARY_REPO` and `E2E_SECONDARY_REPO`, plus the
encrypted secrets `E2E_PRIMARY_TOKEN` and `E2E_SECONDARY_TOKEN`. CI sets
`GH_BABYSITTER_E2E_REQUIRE_NEGATIVE=1`, so missing credentials fail instead of
silently skipping the security gate. Pull requests from forks do not receive
the secrets and skip this live job.

## Design documentation

The design specifications remain in Russian:

1. [Architecture and data model](docs/specs/01-architecture.md)
2. [Event delivery](docs/specs/02-delivery.md)
3. [Authorization and security](docs/specs/03-auth.md)
4. [GitHub webhook configuration](docs/specs/04-github-webhook.md)
5. [CLI](docs/specs/05-cli.md)
6. [ADR: GitHub App](docs/specs/06-github-app.md)
