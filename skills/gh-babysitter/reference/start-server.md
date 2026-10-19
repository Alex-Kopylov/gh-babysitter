# Start a local server

The probe targets `$server`, so when `GH_BABYSITTER_SERVER` names another host
or port, a local server on `127.0.0.1:8000` cannot answer it: match that host
and port, or unset the variable.

Redirect the output, so the background process does not hold the shell's
output open:

```sh
gh babysitter serve --host 127.0.0.1 --port 8000 \
  > "${TMPDIR:-/tmp}/gh-babysitter.log" 2>&1 &
```

Startup takes a few seconds. Rerun the probe from SKILL.md step 1, retrying
until it answers or ten seconds pass. Any status other than `000` means the
server is up. If it stays `000`, read the log, then [setup.md](setup.md).
