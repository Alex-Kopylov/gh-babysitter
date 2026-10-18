# Start a local server

Redirect the output, so the background process does not hold the shell's
output open:

```sh
gh babysitter serve --host 127.0.0.1 --port 8000 \
  > "${TMPDIR:-/tmp}/gh-babysitter.log" 2>&1 &
```

Rerun the probe from SKILL.md step 1. Any status other than `000` means the
server is up. If it stays `000`, read the log, then [setup.md](setup.md).
