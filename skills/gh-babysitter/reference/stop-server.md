# Stop the local server

Stop it only when the user explicitly asks.

`gh babysitter serve` can run as `gh`, then `uv`, then `python`. Killing the
PID from `$!` stops only `gh` and leaves the server running. Find the process
that listens on the port instead:

```sh
pid="$(lsof -tiTCP:8000 -sTCP:LISTEN)"
ps -o args= -p "$pid"
```

Kill it only when its arguments show `gh-babysitter serve`. Its `uv` and `gh`
parents exit with it:

```sh
kill "$pid"
```

Rerun the probe from SKILL.md step 1. `HTTP 000` confirms that the server
stopped.

Do not use `pkill -f`: the pattern also matches the command line of the shell
that runs it, so `pkill` kills that shell.
