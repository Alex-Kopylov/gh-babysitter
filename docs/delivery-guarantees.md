# Delivery guarantees

Delivery is at most once inside gh-babysitter: there is no replay, durable
queue, or event history. Events received while a client is offline or
reconnecting are lost.

At most once does not mean that a GitHub activity is globally unique. GitHub
can redeliver a webhook, so the same activity can reach a consumer more than
once. Consumers must be idempotent.

## Slow consumers

Each subscriber has a bounded in-memory queue
([`GH_BABYSITTER_QUEUE_MAXSIZE`](configuration.md)). If a slow consumer
overruns it, the webhook response reports `matched`, `delivered`, and `dropped`
counts. When the consumer resumes, it receives a `lag` notice with its exact
dropped-event count; dropped events are not replayed.

## Reconnects and `--until` polling

The CLI prints a warning before every reconnect, including the cause, delay,
and reminder that events in the gap are lost. With `--until`, it also polls
GitHub at startup, before each reconnect, and periodically while the stream
remains connected. The periodic interval defaults to 300 seconds and is
configured with `GH_BABYSITTER_UNTIL_POLL_INTERVAL`. These checks catch a
terminal state reached before the stream opened, during the reconnect gap, or
after its terminal event was lost on an otherwise healthy connection.
