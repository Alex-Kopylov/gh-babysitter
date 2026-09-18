# listen options and exit codes

## Flags

| Flag | Meaning |
| --- | --- |
| `-R, --repo OWNER/NAME` | Repository; exactly one `/`, both parts non-empty, `[A-Za-z0-9._-]` only |
| `-E, --events a,b,c` | Comma-separated event types; required unless `--until` supplies them |
| `-n, --number N` | One issue or PR; required by `--until` |
| `--action opened` | Keep only events with this action |
| `--until STATE` | Exit when the object reaches the state |
| `--timeout 2h` | Give up after the duration and exit `124` |
| `--count N` | Exit after N events |
| `--first-event` | Exit after the first event; cannot combine with `--count` |
| `--format json\|pretty` | JSON lines (default) or one short human line per event |
| `--server URL` | Override `GH_BABYSITTER_SERVER` |
| `--api-url URL` | Override every GitHub API base variable |

One connection covers one repository, optionally one number, and any number of
event types. A second repository or number needs a second `listen` process;
there are deliberately no repeatable grouping flags.

Durations are bare seconds (`90`) or `h`/`m`/`s` parts (`1h`, `90m`, `1h30m`).
Zero and negative values are rejected.

Without any exit flag, `listen` streams until it is stopped. Flags combine:
`--until merged --timeout 12h` means "wait for the merge, but not past twelve
hours".

## `--until` matrix

| `--until` | Event type | Exit condition |
| --- | --- | --- |
| `merged` | `pull_request` | `action == "closed"` and `payload.pull_request.merged == true` |
| `closed` | `pull_request`, `issues` | `action == "closed"`, merged PRs included |
| `approved` | `pull_request_review` | `action == "submitted"` and `payload.review.state == "approved"` |
| `changes_requested` | `pull_request_review` | `action == "submitted"` and `payload.review.state == "changes_requested"` |

`--until` requires `-n`, adds its own event type to the subscription, and polls
GitHub directly at startup, before each reconnect, and every
`GH_BABYSITTER_UNTIL_POLL_INTERVAL` seconds (300 by default) while connected.

The `approved` poll matches any approving review in the response, including a
stale one submitted before the latest push, so recheck before acting:

```sh
gh pr view NUMBER --repo OWNER/REPO \
  --json reviewDecision,mergeStateStatus,headRefOid,statusCheckRollup
```

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | The configured `--until`, `--count`, or `--first-event` condition was met |
| `1` | Runtime failure: token rejected, subscription permanently refused, protocol error |
| `2` | Usage error: invalid flag, repository, number, duration, event type, or format |
| `124` | `--timeout` expired |

With `--first-event` or `--count`, code `0` says only that N events arrived; it
implies nothing about the PR. Code `2` is raised before any token is read or
socket opened, so it always means the command line needs fixing. A `401` or
`403` from the server, like any non-retryable `4xx`, prints its `detail` field
to stderr and exits `1` after a single attempt.
