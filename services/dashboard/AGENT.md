# Pipeline Dashboard — Agent Guide

Instructions for AI agents making modifications to this service.

## Project context

This is the **fourth and final service** in the pipeline.  It does not produce data — it monitors and controls the other three services.  It's the only service that mounts the Docker socket.

## File inventory

| File | Role | Safe to edit? |
|------|------|--------------|
| `dashboard_server.py` | Flask backend: status reader, image server, Docker command proxy | Yes |
| `static/index.html` | Single-file frontend: HTML + CSS + JS | Yes |
| `Dockerfile` | Container build (just Flask) | Yes |
| `docker-compose.yaml` | Volume mounts — the most sensitive file | Careful |
| `README.md` | User docs | Yes |
| `ARCHITECTURE.md` | Technical design | Yes |

## Key design decisions

1. **Docker socket access** — the entire stop/start/update feature depends on `/var/run/docker.sock` being mounted.  Without it, only status reading works.
2. **Subprocess, not Docker SDK** — commands are run via `subprocess.run(["docker", "compose", ...])`.  This avoids adding the `docker` Python package as a dependency and matches exactly what the user would type in a terminal.
3. **Project directory mounts** — each service's compose dir is mounted into the dashboard container at `/projects/{name}`.  `docker compose up -d --build` runs with `cwd` set to that mount.  The directories are `:ro` because compose only reads from them.
4. **Command mutex** — `_cmd_lock` ensures only one Docker command runs at a time.  A rebuild can take minutes; concurrent rebuilds would be chaos.
5. **No auth** — designed for LAN.  If you need auth, add Flask-Login or a simple Bearer token check.
6. **Single HTML file** — the frontend is vanilla JS with no build step.  Keep it that way for simplicity.

## Common modifications

### Change the dashboard port
Edit `docker-compose.yaml` → `PORT` env var and the port mapping.

### Add a new service to monitor
1. Add an entry to the `SERVICES` dict in `dashboard_server.py`.
2. Mount its compose dir in `docker-compose.yaml`.
3. Add it to the `order` array in `renderSvcCards()` in `static/index.html`.
4. Handle its status fields in the appropriate renderer.

### Add authentication
Add `flask-httpauth` to `Dockerfile` pip install.  Wrap routes with `@auth.login_required`.  Store credentials as env vars.

### Add container resource usage (CPU/memory)
Use `docker stats --no-stream --format json {container}` in a helper function.  Call it from `/api/status`.

### Change polling interval
Edit `REFRESH` constant at the top of the `<script>` block in `static/index.html`.

### Add a "Logs" panel per service
The backend already has `GET /api/services/{name}/logs`.  Add a UI panel that fetches and displays it.  Consider a collapsible section under each service card.

### Make update use specific compose flags
Edit `svc_update()` in `dashboard_server.py`.  For example, add `--pull always` to force base image updates:
```python
_run_compose(dir, "up", "-d", "--build", "--pull", "always")
```

## Testing

```bash
# Build and run
docker-compose up --build

# Test status endpoint
curl http://localhost:9007/api/status | python -m json.tool

# Test service control (careful — this actually stops the service!)
curl -X POST http://localhost:9007/api/services/screenshot-service/stop

# Test logs
curl http://localhost:9007/api/services/radar-processor/logs?lines=20
```

## Gotchas

- **Docker-in-Docker** — this container runs Docker commands against the host daemon via the socket.  The paths in each service's `docker-compose.yaml` (volumes) are **host paths**, not paths inside this container.  The compose project dirs must be mounted so Docker can find `Dockerfile` and `docker-compose.yaml`, but the volume paths inside those files still reference the host filesystem.
- **Build context** — `docker compose up -d --build` needs the full build context (Dockerfile, source files, requirements.txt) to be present in the mounted compose dir.  If you only mount the `docker-compose.yaml` file instead of the whole directory, builds will fail.
- **Timeouts** — rebuilds can take 5+ minutes (especially if pulling large base images).  The default timeout for the `update` command is 600 seconds.  The frontend shows a spinner but there's no progress bar.
- **`:ro` mounts** — the compose project dirs are mounted read-only.  This means `docker compose` can read but not modify them.  If a service needs to write temp files during build, Docker handles that in the build context (which is copied, not used in-place).
