# Stream contents

## Line format

One matching event is one JSON object on one line:

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

`payload` is the complete GitHub webhook payload. `action` and `number` are
`null` when the event does not carry them; `number` exists only for `issues`,
`issue_comment`, `pull_request`, and `pull_request_review`. `--format pretty`
replaces the object with one short human-readable line and is for people, not
for parsing.

## Event types

| `-E` value | Covers | Useful actions |
| --- | --- | --- |
| `pull_request` | PR lifecycle | `opened`, `synchronize`, `ready_for_review`, `closed`, `reopened` |
| `pull_request_review` | Submitted reviews | `submitted`, `edited`, `dismissed` |
| `issue_comment` | Comments on issues **and** PRs | `created`, `edited`, `deleted` |
| `issues` | Issue lifecycle | `opened`, `closed`, `labeled`, `assigned` |
| `release` | Releases | `published`, `created` |

An `issue_comment` on a PR arrives with the PR number in `number`; the
`payload.issue.pull_request` key is what separates it from a plain issue
comment.

`--action` accepts one action and drops everything else, which is narrower than
it looks: `--action closed` on a merge wait hides the `synchronize` events that
show the branch is still moving. Prefer filtering in your own handling of the
lines.

## What the stream never delivers

CI and check events (`check_run`, `check_suite`, `status`, `workflow_run`) and
inline review comments (`pull_request_review_comment`) are outside the
allowlist. There is no `--until checks_passed`; do not invent one.

Get those through the GitHub CLI instead, inside the same time budget:

```sh
# blocking wait on checks, exits non-zero on failure
gh pr checks NUMBER --repo OWNER/REPO --watch --fail-fast

# current rollup without waiting
gh pr view NUMBER --repo OWNER/REPO --json statusCheckRollup,reviewDecision

# inline review comments
gh api repos/OWNER/REPO/pulls/NUMBER/comments
```

A practical pattern is to keep a `pull_request_review` listener for human
decisions and query checks only when an event or a bounded poll suggests they
moved.
