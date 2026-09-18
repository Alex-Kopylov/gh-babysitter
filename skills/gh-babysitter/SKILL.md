---
name: gh-babysitter
description: Use when asked to babysit a GitHub PR, monitor its reviews or comments, or wait for approval, changes requested, closure, or merge.
---

# GitHub PR Babysitter

`gh babysitter listen` blocks until GitHub delivers a matching event, so a wait
costs no poll loop. Resolve the repository, PR number, desired outcome, and time
budget from the request and current context. For an unspecified budget, use a
bounded wait such as one hour and state that choice.

## 1. Reach a server

Any status other than `000` means a server is reachable, even when it rejects
the request:

```sh
server="${GH_BABYSITTER_SERVER:-http://localhost:8000}"
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' --max-time 2 \
  "$server/events/stream"
```

On `HTTP 000`, start a local one with
`gh babysitter serve --host 127.0.0.1 --port 8000 &`.

Read [reference/setup.md](reference/setup.md) when the extension is missing, the
server stays unreachable, no event ever arrives, or the user asks about the
server, webhook, HTTPS, or secrets. A monitoring request does not authorize
provisioning infrastructure or changing organization webhooks.

## 2. Wait for a state

Read the PR first with `gh pr view NUMBER --repo OWNER/REPO`; if the outcome
already holds, report it without starting a listener. Otherwise:

```sh
gh babysitter listen -R OWNER/REPO -n NUMBER --until merged --timeout 1h
```

Targets are `merged`, `closed`, `approved`, and `changes_requested`. Each
subscribes to the event types it needs and polls GitHub in the background, so
`-E` is unnecessary. An `approved` exit does not prove merge readiness: recheck
the review decision, required checks, and head SHA before claiming it or taking
an authorized merge action.

Other flags (`--count`, `--first-event`, `--action`, `--format`) and each
target's exact condition are in
[reference/listen-options.md](reference/listen-options.md).

## 3. Watch and respond

For open-ended babysitting, keep one listener running in the background and
consume its JSON lines:

```sh
gh babysitter listen -R OWNER/REPO -n NUMBER \
  -E pull_request,pull_request_review,issue_comment --timeout 1h
```

Each line carries `repo`, `event`, `action`, `number`, `ts`, and `payload`.
Inspect the PR after a relevant event, then make the fixes or review responses
the user asked for. A monitoring request alone does not authorize posting
comments, pushing commits, approving, or merging.

Reconcile live GitHub state before acting on an event and before reporting:
delivery is at most once, GitHub can redeliver, and a plain `-E` listener polls
nothing by itself. [reference/reliability.md](reference/reliability.md) covers
reconnect and lag warnings, silence, and deduplication.

The stream carries `issues`, `pull_request`, `issue_comment`,
`pull_request_review`, and `release` only; CI results and inline review comments
never appear on it. [reference/events.md](reference/events.md) has the line
schema and how to reach what the stream omits.

## 4. Finish

Keep every wait, retries included, inside the budget. Stop the listener when the
target is reached, the PR closes without merging, the budget expires, or user
action is required; leave no orphan process and do not claim monitoring
continues after it stops.

Exit `0` means the configured condition was met and `124` means the timeout
expired; treat anything else as failure and check
[reference/listen-options.md](reference/listen-options.md). Verify current
GitHub state before reporting, and never present a timeout as success.
