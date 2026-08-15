# Security

- `/webhook` verifies the `X-Hub-Signature-256` HMAC before parsing the body.
- The CLI uses `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token`. Subscriber
  tokens remain in memory and are never written to disk by gh-babysitter.
- The server verifies repository access through GitHub. A genuine denial
  returns `403`; a rate-limited or unavailable GitHub API returns `503` and is
  not cached as a denial.
- The CLI refuses to send a GitHub token over plain HTTP to a non-loopback
  host. Loopback HTTP remains available for development. Set
  `GH_BABYSITTER_INSECURE=1` only for an explicitly accepted insecure
  deployment.
- `setup` reads a secret from `--secret-stdin`, then
  `GH_BABYSITTER_WEBHOOK_SECRET`, or generates one. A supplied secret is never
  echoed. A generated secret is printed once because no other copy exists.

See [Authorization and security](specs/03-auth.md) for the full design
specification (Russian).
