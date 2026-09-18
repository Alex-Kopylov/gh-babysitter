# Setup

Monitoring a PR does not authorize provisioning infrastructure or changing an
organization webhook.

## Client

```sh
gh extension install Alex-Kopylov/gh-babysitter
gh babysitter --help
gh auth status
```

A standalone Python installation exposes the same commands as `gh-babysitter`:

```sh
uv tool install git+https://github.com/Alex-Kopylov/gh-babysitter.git
```

The token comes from `GH_TOKEN`, then `GITHUB_TOKEN`, then `gh auth token`. The
client refuses to send it over `http://` to a non-loopback host; `localhost`,
`127.0.0.0/8`, and `::1` are exempt, everything else needs HTTPS or an explicit
`GH_BABYSITTER_INSECURE=1`.

## Server

One `serve` process serves every client. It only receives what the organization
webhook sends it, so a local server whose webhook points elsewhere leaves
`listen` connected and silent forever. Reach for a tunnel to the loopback port,
or for the deployed server, instead of waiting.

## Organization webhook (admin, one time)

GitHub must reach the server at an HTTPS URL, so configure DNS and TLS first.

1. Create the shared secret. `serve` and `setup` both read it from the
   environment:

   ```sh
   export GH_BABYSITTER_WEBHOOK_SECRET="$(
     uv run python -c 'import secrets; print(secrets.token_hex(32))'
   )"
   ```

2. Start the server on a reachable interface:

   ```sh
   gh babysitter serve --host 0.0.0.0 --port 8000 &
   ```

3. Register the webhook with an org-admin token:

   ```sh
   gh babysitter setup --org ORG --url https://hooks.example.com/webhook
   ```

   The secret is taken from `--secret-stdin`, then
   `GH_BABYSITTER_WEBHOOK_SECRET`, then a fresh random value printed once.
   There is no `--secret` flag, because argv is visible through `ps`.

4. Point clients at it:

   ```sh
   export GH_BABYSITTER_SERVER=https://hooks.example.com
   ```

Timeouts, ping and poll intervals, queue size, auth cache, and GitHub
Enterprise API precedence have defaults that a monitoring task never needs to
change. When one must change, read
[configuration](https://github.com/Alex-Kopylov/gh-babysitter/blob/main/docs/configuration.md)
and [security](https://github.com/Alex-Kopylov/gh-babysitter/blob/main/docs/security.md)
rather than guessing.
