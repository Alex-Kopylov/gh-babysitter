# Live authorization E2E

The release fixture uses two private repositories and two fine-grained personal
access tokens. Each token must be selected for exactly one fixture repository,
so the live suite proves both allowed and denied access against GitHub.

## Local run

Copy `.env.e2e.example` to the ignored `.env`, replace both token
placeholders, and run:

```console
mise run e2e-local
```

After the local matrix passes, upload the same values to the current GitHub
repository without exposing them in process arguments:

```console
mise run e2e-configure-secrets
```

This command validates the live access matrix first, writes repository names as
Actions variables, and sends both token values to `gh secret set` over stdin.

## Token permissions

The primary token needs `Webhooks` and `Issues` write permissions on its
selected repository. The secondary token needs only `Metadata` read access on
its selected repository. The harness first requires the GitHub access matrix
`A→A=200`, `A→B=404`, `B→A=404`, `B→B=200`; it then proves that both denied
repository/token pairings produce a prompt server-side `403` without opening
an event stream.

## CI

The manual and internal pull-request GitHub Actions workflow reads the
repository variables `E2E_PRIMARY_REPO` and `E2E_SECONDARY_REPO`, plus the
encrypted secrets `E2E_PRIMARY_TOKEN` and `E2E_SECONDARY_TOKEN`. CI sets
`GH_BABYSITTER_E2E_REQUIRE_NEGATIVE=1`, so missing credentials fail instead of
silently skipping the security gate. Pull requests from forks do not receive
the secrets and skip this live job.
