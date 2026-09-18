# Delivery, warnings, and reconciliation

## The contract

Delivery is at most once. There is no replay, durable queue, or history:
anything that arrives while a client is offline or reconnecting is gone. At the
same time GitHub can redeliver a webhook, so the same activity can reach the
stream twice. Consume events idempotently, and treat every line as a hint to
look at GitHub rather than as the truth about it.

## Warnings on stderr

| Line | Meaning |
| --- | --- |
| `warning: disconnected (<reason>); events during the gap are lost; reconnecting in 1.0s` | Transport error, read timeout, or a retryable status (`408`, `425`, `429`, any `5xx`); backoff is exponential and resets only after the server sends `ready` |
| `warning: server dropped N events (consumer too slow)` | The subscriber queue overflowed; N events were dropped and are not replayed |

Neither line ends the process, and neither appears in stdout. Both mean the
same thing for the task: state may have moved without a line to show it.

## Silence

A stream that never yields a line is not evidence that nothing happened. Check,
in order: the PR really is open and matches `-R`/`-n`; the event type is in
`-E`; the server the client reached is the one the organization webhook targets
(see [setup.md](setup.md)); the activity is a type the stream carries at all
(see [events.md](events.md)).

A renamed or transferred repository silently stops matching, because
subscriptions match `repository.full_name` as GitHub delivers it. Restart
`listen` against the new name.

## Reconciliation

`--until` polls GitHub on its own schedule (see
[listen-options.md](listen-options.md)), so a lost terminal event delays a
correct exit by at most one poll interval, not until `--timeout`. This observes
current state; it does not add replay.

A plain `-E` listener has no such poll. Reconcile it yourself:

- after any warning above;
- before acting on an event, so the action fits current state rather than the
  state at delivery time;
- periodically during a long watch, on an interval that fits the budget;
- before reporting an outcome.

```sh
gh pr view NUMBER --repo OWNER/REPO \
  --json state,isDraft,mergedAt,reviewDecision,headRefOid,statusCheckRollup
```

Deduplicate against something stable in that state — the head SHA, a review id,
a comment id — not against the count of lines seen.
