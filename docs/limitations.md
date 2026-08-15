# Known limitations and non-goals

## Known limitations in 1.1.0

**Lost terminal events are recovered by polling, not replay.** With `--until`,
a terminal event lost while the SSE connection stays healthy can delay
successful completion until the next
`GH_BABYSITTER_UNTIL_POLL_INTERVAL` (300 seconds by default), plus the GitHub
request time. This observes the current GitHub state; it does not add replay or
change the at-most-once delivery contract.

**Successful stream teardown depends on a narrow httpcore2 workaround.**
Abandoning an active SSE stream triggers an upstream async-generator
finalization bug. gh-babysitter filters that exact traceback signature from
stderr while preserving the correct exit code. If a future upstream change
alters the signature, the traceback will reappear until the workaround is
updated or removed. Tracked in
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

## Non-goals

- Guaranteed delivery, replay, or offline catch-up
- Horizontal scaling or a multi-process shared registry
- Persistent subscriptions or server-side subscription management
- A separate user database, OAuth flow, or gh-babysitter-issued credentials
- A web interface
