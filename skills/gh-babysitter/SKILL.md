---
name: gh-babysitter
description: Use when asked to babysit a GitHub PR, monitor its reviews or comments, or wait for approval, changes requested, closure, or merge.
---

# GitHub PR Babysitter

Use `gh babysitter` to wait for GitHub events while managing the requested PR.
Resolve the repository, PR number, desired outcome, and time budget from the
request and current context. For an unspecified budget, use a bounded wait
such as one hour and state that choice.

## Start the listener

1. Check whether the configured server responds. A status other than `000`
   means that a server is reachable, even when authentication rejects the
   request:

   ```sh
   server="${GH_BABYSITTER_SERVER:-http://localhost:8000}"
   curl -sS -o /dev/null -w 'HTTP %{http_code}\n' --max-time 2 \
     "$server/events/stream"
   ```

2. If the command reports `HTTP 000`, start the local server:

   ```sh
   gh babysitter serve --host 127.0.0.1 --port 8000 &
   ```

3. If the user explicitly asks to configure the `gh-babysitter` server,
   webhook, HTTPS, or secrets, follow the
   [setup guide](https://github.com/Alex-Kopylov/gh-babysitter#quickstart).
   Do not provision infrastructure or change organization webhooks for a
   monitoring request alone.

Check `gh babysitter --help` and `gh auth status`. If the extension is missing,
install it with `gh extension install Alex-Kopylov/gh-babysitter`.
The Python installation exposes the same commands as `gh-babysitter`.

## Wait for a state

Read the current PR state first with `gh pr view NUMBER --repo OWNER/REPO`.
If the requested outcome already holds, report it without starting a listener.
Otherwise substitute the actual repository, number, and budget:

```sh
gh babysitter listen -R OWNER/REPO -n NUMBER --until merged --timeout 1h
```

Supported targets are `merged`, `closed`, `approved`, and `changes_requested`.
`--until` selects the required event types automatically and checks GitHub at
startup, before reconnecting, and periodically during the connection.

An approval event or `--until approved` success does not prove merge readiness.
The review poll accepts any matching review in its response, including an older
review. Recheck current review decisions, required checks, and the PR head before
claiming readiness or taking an authorized merge action.
If a PR closes without merging, stop a merge-wait task and report that outcome.

## Watch and respond

For ongoing babysitting, keep one listener running in the available background
process facility and consume its JSON lines:

```sh
gh babysitter listen -R OWNER/REPO -n NUMBER \
  -E pull_request,pull_request_review,issue_comment --timeout 1h
```

Each line contains `repo`, `event`, `action`, `number`, `ts`, and `payload`.
Inspect the current PR after relevant events, then perform the fixes or review
responses the user requested. A monitoring request alone does not authorize
posting comments, pushing commits, approving, or merging.

The stream supports `issues`, `pull_request`, `issue_comment`,
`pull_request_review`, and `release`. It does not deliver CI events or inline
`pull_request_review_comment` events. Inspect checks and inline review threads
through GitHub CLI/API when they are part of the task. Use bounded check polling
when waiting for CI; do not invent a `--until checks_passed` target.

Events can be lost during disconnects or queue overflow, and GitHub can send
duplicates. Reconcile current GitHub state after reconnect or lag notices and
before acting; avoid repeating a response for the same activity. For a plain
listener, also reconcile periodically because it has no `--until` state polling.

## Completion

Keep waits within the overall budget, including retries. Stop the listener when
the target is reached, the PR is closed, the budget expires, or user action is
required. Do not leave an orphan process or claim monitoring continues after
the process stops.

Exit code `0` means the configured exit condition was met; with `--first-event`
or `--count`, it does not imply PR completion. Code `124` means timeout, `1`
means runtime failure, and `2` means invalid usage. Verify current GitHub state
before reporting the outcome, and distinguish a timeout from success.
