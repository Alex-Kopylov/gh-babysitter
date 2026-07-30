# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-07-30

### Fixed in 1.1.0

- `listen --until` now polls GitHub periodically while its SSE connection
  remains healthy, bounding detection of a lost terminal event by the new
  `GH_BABYSITTER_UNTIL_POLL_INTERVAL` setting (300 seconds by default) instead
  of waiting until `--timeout`.

## [1.0.0] - 2026-07-27

This release resolves the black-box audit findings tracked in
[issue #21](https://github.com/Alex-Kopylov/gh-babysitter/issues/21).

### Added

- One organization webhook can fan out GitHub events to ephemeral,
  repository-authorized SSE subscriptions.
- `listen` supports repository, event, action, and object-number filters,
  JSON Lines or pretty output, and `--until`, `--count`, `--first-event`, and
  `--timeout` exit conditions.
- `setup` creates or updates the organization webhook, and `serve` runs the
  gateway from the same GitHub CLI extension.
- Queue-overrun accounting reports `matched`, `delivered`, and `dropped`
  webhook counts and sends an affected listener a `lag` event.

### Changed

- Authorization now distinguishes `allowed`, `denied`, and `unavailable`;
  transient GitHub failures return `503` on connect and do not close an
  established stream during periodic rechecks.
- SSE reads now use a configurable 90-second default timeout, and graceful
  server shutdown is bounded to five seconds.
- Reconnects now identify their cause, warn that events in the gap are lost,
  and preserve exponential backoff until a stream delivers `ready`.
- The delivery contract now explicitly treats GitHub redelivery as a possible
  source of duplicates and requires idempotent consumers.

### Fixed

- Non-retryable HTTP statuses were retried forever with no diagnostic output;
  permanent `4xx` responses now fail once and surface the server detail.
- Retryable HTTP responses reset backoff before a stream was established,
  causing a failing server to be retried once per second indefinitely.
- A transient GitHub `403`, rate limit, `5xx`, or transport failure was cached
  as an access denial for 300 seconds with no real retry.
- Queue-full events were counted as successfully matched deliveries, hiding
  event loss from both the webhook sender and the affected listener.
- An unbounded SSE read timeout could strand a listener forever after graceful
  server shutdown.
- Malformed JSON and non-object webhook bodies could escape request handling
  instead of returning a controlled `400` response.
- Successful exit after an SSE event printed a `RuntimeError: generator didn't
  stop after athrow()` traceback to stderr, from an upstream httpcore2
  async-generator finalization bug, which agents reading stderr could mistake
  for a failure. The traceback is now filtered; the exit code was always `0`.
- Malformed repositories, non-positive object numbers, empty actions, invalid
  server URLs, and non-positive timeouts were accepted locally instead of
  failing as usage errors before network activity.

### Security

- The CLI refuses to send a GitHub token over plain HTTP to a non-loopback
  server unless `GH_BABYSITTER_INSECURE=1` is explicitly set.
- `setup` no longer accepts secrets in argv. It reads a supplied secret from
  stdin or `GH_BABYSITTER_WEBHOOK_SECRET` without echoing it.
- Webhook bodies are parsed only after successful HMAC verification.

[1.1.0]: https://github.com/Alex-Kopylov/gh-babysitter/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Alex-Kopylov/gh-babysitter/releases/tag/v1.0.0
