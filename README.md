# gh-babysitter

gh-babysitter streams filtered GitHub events to your command line. One webhook
for your organization feeds one small server. Each client opens a
`gh babysitter listen` stream with its own filter. You need no hook per
developer, and no poll loop.

Current release: `1.1.0`.

## Why you need this

AI-assisted development runs through separate steps on GitHub: commits, pull
requests, CI runs, and reviews. Most AI agents poll GitHub in a loop. They ask
GitHub again and again whether a new event appeared.

A poll loop costs the agent time:

- An event, such as a failed CI test, reaches the agent at the next poll, not
  at the moment GitHub creates it.
- One pull request loses seconds. A month of work loses hours.

gh-babysitter removes that wait. The server pushes each matching event to the
agent as soon as GitHub delivers it.

## How it works

A plain GitHub webhook has three limits:

- You must configure the webhook in advance, and you need admin access.
- GitHub caps how many webhooks one organization or one repository can have.
- The webhook filters only by event type. It cannot filter by repository, by
  action, or by issue number.

gh-babysitter puts one webhook in front of every client. The server checks the
signature of each delivery and normalizes it. The server then pushes the event
to every client whose filter matches, over Server-Sent Events (SSE).

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

The server supports five event types: `issues`, `pull_request`,
`issue_comment`, `pull_request_review`, and `release`.

The server holds events and subscriptions in memory only. A subscription lives
for exactly as long as its `listen` connection.

## Install

Get these three items first:

| Item | Note |
| --- | --- |
| [uv](https://docs.astral.sh/uv/) | Installs and runs the Python code |
| Python 3.14 | uv can install the interpreter for you |
| [GitHub CLI](https://cli.github.com/) | Run `gh auth login` before you start |

Install gh-babysitter as a GitHub CLI extension:

```console
gh extension install Alex-Kopylov/gh-babysitter
gh babysitter --help
```

You can also install the Python command on its own:

```console
uv tool install git+https://github.com/Alex-Kopylov/gh-babysitter.git@v1.1.0
gh-babysitter --help
```

The examples below use `gh babysitter`. If you installed the Python command,
use `gh-babysitter` instead.

## Quickstart

GitHub must reach your server at an HTTPS URL. Configure DNS and TLS first, so
that `https://hooks.example.com` reaches port 8000 on your server.

In the steps below, replace `hooks.example.com`, `my-org`, and `api` with your
own names.

**Step 1. Create one webhook secret.** The `serve` and `setup` commands both
read this value from the environment.

```console
export GH_BABYSITTER_WEBHOOK_SECRET="$(
  uv run python -c 'import secrets; print(secrets.token_hex(32))'
)"
```

**Step 2. Start the server.**

```console
gh babysitter serve --host 0.0.0.0 --port 8000 &
```

**Step 3. Register the webhook with GitHub.**

```console
gh babysitter setup --org my-org --url https://hooks.example.com/webhook
```

**Step 4. Point the client at the server.**

```console
export GH_BABYSITTER_SERVER=https://hooks.example.com
```

**Step 5. Start a subscription.**

```console
gh babysitter listen -R my-org/api -E issues
```

For each matching event, the client prints one JSON object on one line. To end
the subscription, stop the process.

Each JSON object holds the normalized fields and the complete GitHub payload:

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

The `action` and `number` fields are `null` when the GitHub event does not
provide them.

To get one short line per event instead, add `--format pretty`.

## Waiting for a state

The `--until` flag makes the client exit when one pull request or one issue
reaches a state. Use it when an agent must wait for a result, then continue.

This example waits for pull request 24 to merge, and gives up after 12 hours:

```console
gh babysitter listen \
  -R my-org/web \
  -n 24 \
  --until merged \
  --timeout 12h
```

Two rules apply to `--until`:

- `--until` requires `-n`, the issue or pull request number.
- `--until` subscribes to the event types that its target state needs, so you
  do not need `-E`.

| `--until` value | Event type | The client exits when |
| --- | --- | --- |
| `merged` | `pull_request` | GitHub sends the `closed` action, and `payload.pull_request.merged` is `true` |
| `closed` | `pull_request` or `issues` | GitHub sends the `closed` action |
| `approved` | `pull_request_review` | GitHub sends the `submitted` action with the review state `approved` |
| `changes_requested` | `pull_request_review` | GitHub sends the `submitted` action with the review state `changes_requested` |

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | The client met an exit condition: `--until`, `--count`, or `--first-event` |
| `1` | The client hit a runtime failure. For example, GitHub rejected the token, the server refused the subscription, or the protocol broke |
| `2` | You made a usage error. For example, an invalid flag, a malformed repository name, or a number or timeout that is not positive |
| `124` | The `--timeout` period expired |

Without an exit-condition flag, the client runs until you interrupt it, or
until a runtime failure occurs.

## Good to know

gh-babysitter delivers each event at most once. It has no replay, no durable
queue, and no event history.

Two results follow from this design:

- The client loses the events that arrive while it is offline or while it
  reconnects.
- GitHub can send the same webhook twice, so your consumer must be idempotent.

For the details, read [Delivery guarantees](docs/delivery-guarantees.md).

## Documentation

- [Configuration](docs/configuration.md) — all server and CLI environment
  variables, precedence rules, and timeout coupling
- [Delivery guarantees](docs/delivery-guarantees.md) — at-most-once semantics,
  slow-consumer queues, reconnects, and `--until` polling
- [Security](docs/security.md) — HMAC verification, token handling, and HTTPS
  enforcement
- [Known limitations and non-goals](docs/limitations.md) — what 1.1.0 does not
  do, and why
- [Live authorization E2E](docs/e2e.md) — how contributors run the live test
  suite against GitHub

Design specifications (Russian):

1. [Architecture and data model](docs/specs/01-architecture.md)
2. [Event delivery](docs/specs/02-delivery.md)
3. [Authorization and security](docs/specs/03-auth.md)
4. [GitHub webhook configuration](docs/specs/04-github-webhook.md)
5. [CLI](docs/specs/05-cli.md)
6. [ADR: GitHub App](docs/specs/06-github-app.md)

## License

[Apache-2.0](LICENSE)
