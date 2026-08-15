# gh-babysitter

Subscribe to filtered GitHub events from the command line. One organization
webhook feeds a small server; each consumer opens a `gh babysitter listen`
stream with its own filter — no per-developer hooks, no polling loops.

Current release: `1.1.0`.

## Why

GitHub webhooks must be configured in advance, require admin access, and are
limited in number per organization or repository. Their configuration filters
only by event type — not by repository, action, or issue/PR number.

gh-babysitter puts one webhook in front of all consumers. The server verifies
and normalizes each delivery, then fans it out over Server-Sent Events (SSE)
to every connected subscriber whose filter matches.

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

Supported events: `issues`, `pull_request`, `issue_comment`,
`pull_request_review`, and `release`. Events and subscriptions are memory-only;
a subscription exists for exactly as long as its `listen` connection.

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
uv tool install git+https://github.com/Alex-Kopylov/gh-babysitter.git@v1.1.0
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

## Waiting for a state

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
| --- | --- | --- |
| `merged` | `pull_request` | `closed` action with `payload.pull_request.merged` equal to `true` |
| `closed` | `pull_request` or `issues` | `closed` action |
| `approved` | `pull_request_review` | `submitted` action with review state `approved` |
| `changes_requested` | `pull_request_review` | `submitted` action with review state `changes_requested` |

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | An exit condition was met: `--until`, `--count`, or `--first-event` |
| `1` | Runtime failure, such as a rejected token, permanently refused subscription, or protocol error |
| `2` | Usage error, such as invalid flags, a malformed repository, or a non-positive number or timeout |
| `124` | `--timeout` expired |

Without an exit-condition flag, `listen` continues until interrupted or until a
runtime failure occurs.

## Good to know

Delivery is at most once: no replay, no durable queue, no event history.
Events received while a client is offline or reconnecting are lost, and GitHub
can redeliver a webhook, so consumers must be idempotent. Details in
[Delivery guarantees](docs/delivery-guarantees.md).

## Documentation

- [Configuration](docs/configuration.md) — all server and CLI environment
  variables, precedence rules, and timeout coupling
- [Delivery guarantees](docs/delivery-guarantees.md) — at-most-once semantics,
  slow-consumer queues, reconnects, and `--until` polling
- [Security](docs/security.md) — HMAC verification, token handling, and HTTPS
  enforcement
- [Known limitations and non-goals](docs/limitations.md) — what 1.1.0 does not
  do, and why
- [Live authorization E2E](docs/e2e.md) — running the live test suite against
  GitHub (contributors)

Design specifications (Russian):

1. [Architecture and data model](docs/specs/01-architecture.md)
2. [Event delivery](docs/specs/02-delivery.md)
3. [Authorization and security](docs/specs/03-auth.md)
4. [GitHub webhook configuration](docs/specs/04-github-webhook.md)
5. [CLI](docs/specs/05-cli.md)
6. [ADR: GitHub App](docs/specs/06-github-app.md)

## License

[Apache-2.0](LICENSE)
