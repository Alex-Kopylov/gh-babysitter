# Configuration

All configuration is read from environment variables. An explicit CLI flag,
such as `--api-url`, takes precedence over the corresponding variable.

## Server environment variables

| Variable | Default | Purpose |
| --- | ---: | --- |
| `GH_BABYSITTER_WEBHOOK_SECRET` | unset | HMAC secret shared with the GitHub organization webhook; required to accept deliveries |
| `GH_BABYSITTER_GITHUB_API_URL` | `https://api.github.com` | GitHub API base URL used to verify subscriber identity and repository access |
| `GH_BABYSITTER_AUTH_CACHE_TTL` | `300` | Seconds to cache allowed or denied authorization results |
| `GH_BABYSITTER_RECHECK_INTERVAL` | `300` | Seconds between access checks for an open stream |
| `GH_BABYSITTER_PING_INTERVAL` | `30` | Seconds between SSE keepalive comments; must stay well below the client's `GH_BABYSITTER_STREAM_TIMEOUT` |
| `GH_BABYSITTER_QUEUE_MAXSIZE` | `256` | Maximum queued events per subscriber before new events are dropped |

## CLI environment variables

| Variable | Default | Purpose |
| --- | ---: | --- |
| `GH_BABYSITTER_SERVER` | `http://localhost:8000` | Base URL for the gh-babysitter server |
| `GH_BABYSITTER_WEBHOOK_SECRET` | unset | Secret reused by `setup` when `--secret-stdin` is not supplied |
| `GH_BABYSITTER_GITHUB_API_URL` | unset | First environment override for the GitHub API base URL |
| `GITHUB_API_URL` | unset | GitHub API base URL used when the gh-babysitter-specific value is unset |
| `GH_HOST` | unset | GitHub CLI host from which an API URL is derived when neither API URL variable is set |
| `GH_TOKEN` | unset | First environment source for the GitHub token |
| `GITHUB_TOKEN` | unset | GitHub token used when `GH_TOKEN` is unset |
| `GH_BABYSITTER_SERVER_TIMEOUT` | `10` | Connect, write, and pool timeout in seconds for server requests |
| `GH_BABYSITTER_STREAM_TIMEOUT` | `90` | Read timeout in seconds for the SSE stream; must exceed the server's `GH_BABYSITTER_PING_INTERVAL` |
| `GH_BABYSITTER_GITHUB_TIMEOUT` | `10` | Timeout in seconds for GitHub API requests |
| `GH_BABYSITTER_UNTIL_POLL_INTERVAL` | `300` | Seconds between GitHub state checks while `listen --until` remains connected |
| `GH_BABYSITTER_INSECURE` | `0` | Set to `1` to allow sending a GitHub token over plain HTTP to a non-loopback server |

## Precedence notes

- An explicit `--api-url` takes precedence over API URL environment variables.
- Without a token variable, the CLI runs `gh auth token`.
- The server reads only `GH_BABYSITTER_GITHUB_API_URL`; it does not inherit
  `GITHUB_API_URL` or `GH_HOST`.

## Keepalive and stream timeout coupling

The stream read timeout and the server keepalive are coupled. A listener treats
silence longer than `GH_BABYSITTER_STREAM_TIMEOUT` as a dead connection and
reconnects, so that value must comfortably exceed
`GH_BABYSITTER_PING_INTERVAL`; the defaults tolerate three missed pings. Raising
the ping interval above the stream timeout, or terminating the stream behind a
proxy that strips SSE comments, makes listeners reconnect on a fixed cycle and
opens a blind window each time.
