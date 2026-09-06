# services/dashboard — agent notes

**What it is.** Monitors and controls the other five services. Does not produce data. The
only service that mounts the Docker socket, and the only one that mounts the repo itself
(read-only, at `/project`) so it can drive `docker-compose` for rebuilds.

**Entry point.** `dashboard_server.py` (Flask) + `static/index.html` (single-file vanilla-JS
frontend, no build step). The `SERVICES` dict in `dashboard_server.py` is the registry: each
entry maps a dashboard key to its compose service name, container name(s), and status-file
name. Adding a seventh service means adding an entry here (and to the frontend's card
ordering).

**Container control is via the Docker Engine API, not the CLI.** `_docker_api()` talks to
`/var/run/docker.sock` directly (HTTP over a Unix socket, `DOCKER_API_VERSION` pinned via
env); `stop`/`start` call it directly. Only **rebuild** (`svc_update`) shells out to
`docker-compose -p reaped-whirlwind up -d --build <service>` via `_run_compose()`, run
against `COMPOSE_DIR` (`/project`, the read-only repo mount) with `DOCKER_HOST` pointed at
the same socket — 600s timeout, and `_cmd_lock` ensures only one compose command runs at a
time (a rebuild can take minutes; concurrent rebuilds would race).

**Status API.** `/api/status` merges each service's status JSON (from `/status`, read-only
mount shared with all services) with live Docker container state — if Docker can't find the
container but the status file looks fresh, it trusts the status file. `/api/debug` dumps the
raw inputs (socket state, container list, status files) when something looks wrong.

**No auth** — LAN-only by design.

**Gotchas.**
- This container only *talks to* the Docker daemon via the socket; it does not run
  `docker-compose` from inside its own filesystem for anything but the repo mounted at
  `/project`. Editing files under that mount from inside the dashboard container has no
  effect — the real repo lives on the host / other bind mounts.
- `up -d --build` needs the full build context (Dockerfile, source, requirements.txt)
  present under `/project/services/<name>` — that's the whole repo mount, not a
  per-service-only mount, so this still works even though it's one shared read-only volume.
- Adding a *new* bind mount to `docker-compose.yml` (e.g. `./common:/srv/reaped/common:ro`) requires
  `up -d` (container recreate) on the affected services, not `restart` — see
  `../../docs/DEPLOY.md`.

See `../../docs/ARCHITECTURE.md` for how this fits into the six-service pipeline.
