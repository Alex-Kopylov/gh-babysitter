# Design — configurable GitHub API host in the CLI — 2026-07-26

The CLI hardcodes `https://api.github.com` in two places, so GitHub Enterprise
Server (GHES) installs cannot be served: `listen` validates tokens and polls
`--until` state against public GitHub, and `setup` would create the org webhook
on the wrong host. The server already reads `GH_BABYSITTER_GITHUB_API_URL`
([server/config.py](../../../../src/gh_babysitter/server/config.py)); this design gives
the CLI an equivalent, `gh`-compatible resolution path.

## Decisions

| Question | Decision |
|---|---|
| Env vars honored by the CLI | `GH_BABYSITTER_GITHUB_API_URL`, `GITHUB_API_URL`, `GH_HOST` |
| Flag | `--api-url` on `listen` and `setup`, taking a full API URL |
| Flag name | `--api-url`, not `gh`'s `--hostname`, so flag and env vars carry the same value type |
| Server env sources | Unchanged: `GH_BABYSITTER_GITHUB_API_URL` only, no ambient `GH_HOST` pickup |
| Where resolution lives | The two `Settings` classes, as Pydantic annotated types and a computed field — no new module |
| URL validation | None beyond normalization — a bad host fails at connect time |

`GH_HOST` holds a **hostname** (`github.acme.com`), not a URL; `gh` derives the
API base from it. `GITHUB_API_URL` holds a **full URL** and is set by the GitHub
Actions runner. Honoring `GH_HOST` therefore needs a derivation rule, which is why
it is a separate field rather than another `AliasChoices` entry.

## Precedence

| # | Source | Type | Rationale |
|---|---|---|---|
| 1 | `--api-url` | full URL | Per-invocation, so it beats ambient configuration |
| 2 | `GH_BABYSITTER_GITHUB_API_URL` | full URL | Project's own var; an explicit setting is not overridden by an ambient one |
| 3 | `GITHUB_API_URL` | full URL | Actions runner convention |
| 4 | `GH_HOST` | hostname | `gh`'s own var; most indirect, so lowest |
| 5 | `https://api.github.com` | — | Default |

Steps 2 and 3 are one field with `AliasChoices` in that order. Step 4 is a second
field consulted only when the first is unset.

### Blank values

A var that is set but blank (or whitespace, or only slashes) counts as **unset**
on the CLI side and falls through to the next source. This matters: some CI
environments export `GITHUB_API_URL=` empty, and collapsing that to the default
would silently swallow a valid `GH_HOST` and route an enterprise user to public
GitHub — the exact failure this change exists to fix.

The server has no chain to fall through to, so a blank value there resolves to the
default. One shared normalizer, two annotated types.

### Host derivation (`GH_HOST` only)

A leading scheme is stripped, so `https://github.acme.com` and `github.acme.com`
behave identically. Comparison is case-insensitive.

| Host | API base | Note |
|---|---|---|
| `github.com` | `https://api.github.com` | Matches `gh` |
| `api.github.com` | `https://api.github.com` | Guard: the naive rule would yield `https://api.github.com/api/v3` |
| `*.ghe.com` | `https://api.<host>` | Enterprise Cloud with data residency; matches `gh` |
| anything else | `https://<host>/api/v3` | GHES |

## Components

### `src/gh_babysitter/server/config.py`

Gains the shared vocabulary. It is the natural owner: `github_api_url` already
lives here, so this module already defines what a GitHub API base URL is.

```python
DEFAULT_GITHUB_API_URL = "https://api.github.com"


def _normalize_api_url(value: str | None) -> str | None:
    """Strip surrounding space and trailing slashes; a blank value becomes ``None``."""
    return (value or "").strip().rstrip("/") or None


def _normalize_or_default(value: str | None) -> str:
    return _normalize_api_url(value) or DEFAULT_GITHUB_API_URL


GitHubApiUrl = Annotated[str, BeforeValidator(_normalize_or_default)]
OptionalGitHubApiUrl = Annotated[str | None, BeforeValidator(_normalize_api_url)]
```

The field becomes `github_api_url: GitHubApiUrl = DEFAULT_GITHUB_API_URL`. Source
list unchanged — no `GH_HOST`, no `GITHUB_API_URL`. One observable behavior change:
a trailing slash is now stripped. `GitHubAuthenticator.__init__` keeps its own
`.rstrip("/")` — idempotent, and it preserves the class's standalone contract.

### `src/gh_babysitter/cli/config.py`

Raw fields keep their env-var names; the resolved value is a computed field named
for the flag, so `get_settings().api_url` reads exactly like `get_settings().server`.

```python
github_api_url: OptionalGitHubApiUrl = Field(
    default=None,
    validation_alias=AliasChoices("GH_BABYSITTER_GITHUB_API_URL", "GITHUB_API_URL"),
)
gh_host: str | None = Field(default=None, validation_alias="GH_HOST")

@computed_field
@property
def api_url(self) -> str:
    """GitHub API base URL, derived from ``GH_HOST`` when not set directly."""
    return self.github_api_url or _api_url_from_host(self.gh_host) or DEFAULT_GITHUB_API_URL
```

`_api_url_from_host` is a module-level helper here rather than in `server/config.py`,
which is what "the server opts out of `GH_HOST`" looks like structurally.

Note: `computed_field` means `model_dump()` now includes `api_url`. Nothing
serializes CLI settings today, so this is free discoverability.

### `src/gh_babysitter/cli/main.py`

`--api-url` on both commands, using the same `default_factory` idiom as `--server`
one line above it:

```python
api_url: Annotated[str, typer.Option("--api-url", default_factory=lambda: get_settings().api_url)]
```

The env chain resolves into the flag's default, so `--help` shows the effective
value and downstream code never handles `None`. The parameter has no Python-level
default, so it sits beside `server`, ahead of the `= None` options.

### `src/gh_babysitter/cli/listen.py`

Drop `_GITHUB_API_URL`. `ListenOptions` gains `api_url: str = DEFAULT_GITHUB_API_URL`;
the GitHub client is built with `base_url=options.api_url`.

### `src/gh_babysitter/cli/setup.py`

Drop `_GITHUB_API_URL`. `setup_webhook` gains `api_url: str = DEFAULT_GITHUB_API_URL`
and passes it as `base_url`. Pagination is unaffected: the `Link` header yields
absolute URLs, which bypass `base_url` and so already point at the right host.

## Error handling

Nothing new. A wrong or unreachable host surfaces through the existing paths: the
`httpx2.HTTPError` handler in `listen`, and `raise_for_status()` in `setup`. No URL
syntax validation is added — a malformed value produces a clearer connect-time
error than a bespoke validator would.

## Testing

TDD: for each unit, write its tests, run them red, implement to green, refactor.
One atomic commit per row. Coverage must stay at or above ~95%.

| # | Tests | Implementation |
|---|---|---|
| 1 | `server/test_config.py`: trailing slash and surrounding space normalized away; blank value falls back to the default; bare `GITHUB_API_URL` and `GH_HOST` do not leak into server settings | shared normalizer + annotated types, `Settings.github_api_url` |
| 2 | `cli/test_config.py`: add the three vars to the autouse `delenv` list; default; each precedence step in isolation and in conflict; blank value falls through to the next source; four host derivations; scheme tolerance | CLI fields, `_api_url_from_host`, `api_url` computed field |
| 3 | `cli/test_listen.py`: GitHub client constructed with `base_url=options.api_url`, extending the factory-kwarg capture in `test_listen_github_client_keeps_finite_timeout` | `listen.py` |
| 4 | `cli/test_setup.py`: with a GHES `api_url`, requests land on `https://github.acme.com/api/v3/orgs/acme/hooks` | `setup.py` |
| 5 | `cli/test_main.py`: `--api-url` maps through; env reaches the command; existing `ListenOptions` and setup-kwargs assertions updated | `main.py` |
| 6 | — | Precedence table into [cli spec](../../../specs/cli/spec.md) under `## Конвенции флагов`, in Russian to match the file |

Because resolution happens at the Typer layer (as it already does for `--server`),
the end-to-end guarantee is two linked facts: env → flag default in
`cli/test_main.py`, and flag → `base_url` in `cli/test_listen.py` and
`cli/test_setup.py`.

## Out of scope

- Renaming `--api-url` to `gh`'s `--hostname`, or accepting both.
- Reading the host from `gh config` via a subprocess: it would add a second failure
  mode alongside the existing `gh auth token` call.
- Per-host token selection (`GH_ENTERPRISE_TOKEN` / `GITHUB_ENTERPRISE_TOKEN`).
  `resolve_token` is unchanged; `gh auth token` already returns the token for the
  active host.
