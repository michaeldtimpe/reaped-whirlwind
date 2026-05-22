# Pipeline Dashboard

Central monitoring and control panel for the weather-data pipeline.  Shows service health, latest screenshots, processed data metadata, the current weather report, and lets you **stop**, **start**, and **rebuild** each service from the browser.

## Quick start

```bash
docker-compose up -d --build
```

Dashboard: `http://YOUR_NAS_IP:9007`

## What it does

The dashboard is a lightweight Flask app that:

1. **Reads status JSONs** from the shared `/status` volume (written by each service every cycle).
2. **Queries the Docker daemon** (via the mounted socket) for each container's running state.
3. **Serves the latest screenshot** as a live image preview.
4. **Reads processed-JSON metadata** (radar type, dimensions, file size) without loading the full data array.
5. **Displays the latest weather report** text from the weather-reporter status file.
6. **Executes Docker Compose commands** when you click Stop / Start / Update.

### Update button

Clicking **Update** on a service runs the equivalent of:

```bash
cd /path/to/service
docker compose up -d --build
```

This pulls fresh base images, installs updated packages, rebuilds the container, and restarts it — all from the browser.

## Prerequisites

The dashboard container needs:

- **Docker socket** mounted at `/var/run/docker.sock` — this is what allows it to control other containers.
- **Each service's compose project directory** mounted read-only — so it can run `docker compose up -d --build` inside them.
- **Shared volumes** for status files, screenshots, and processed data (read-only).

## Configuration

All paths are configurable via environment variables in `docker-compose.yaml`:

| Env var | Default | Description |
|---------|---------|-------------|
| `PORT` | `9007` | Dashboard port |
| `STATUS_DIR` | `/status` | Shared status JSON directory |
| `SCREENSHOTS_DIR` | `/screenshots` | Raw screenshot PNGs |
| `PROCESSED_DIR` | `/processed` | Processed JSON files |
| `SS_COMPOSE_DIR` | `/projects/screenshot-service` | Screenshot service compose dir |
| `RP_COMPOSE_DIR` | `/projects/radar-processor` | Radar processor compose dir |
| `WR_COMPOSE_DIR` | `/projects/weather-reporter` | Weather reporter compose dir |

## Volume mapping

You **must** adjust the host-side paths in `docker-compose.yaml` to match where each service lives on your NAS.  The critical mounts are:

```yaml
volumes:
  # Docker socket
  - /var/run/docker.sock:/var/run/docker.sock:rw

  # Status, screenshots, processed (match your other services)
  - /volume1/docker/service-status:/status:ro
  - /volume1/docker/weather-screenshots:/screenshots:ro
  - /volume1/docker/processed-weather-screenshots:/processed:ro

  # Service project directories (where each docker-compose.yaml lives)
  - /volume1/docker/screenshot-service:/projects/screenshot-service:ro
  - /volume1/docker/radar-processor:/projects/radar-processor:ro
  - /volume1/docker/weather-reporter:/projects/weather-reporter:ro
```

## Security note

Mounting the Docker socket gives this container **full control over the Docker daemon**.  This is necessary for the start/stop/rebuild functionality.  Only expose the dashboard on your local network.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/status` | Aggregated status JSON |
| GET | `/api/screenshots/latest` | Latest raw screenshot PNG |
| GET | `/api/processed/latest` | Latest processed JSON metadata |
| POST | `/api/services/{name}/stop` | Stop a service |
| POST | `/api/services/{name}/start` | Start a service |
| POST | `/api/services/{name}/update` | Rebuild and restart a service |
| GET | `/api/services/{name}/logs?lines=80` | Container logs |
| GET | `/health` | Health check |

Service names: `screenshot-service`, `radar-processor`, `weather-reporter`

## Files

| File | Purpose |
|------|---------|
| `dashboard_server.py` | Flask backend |
| `static/index.html` | Frontend (single HTML file) |
| `Dockerfile` | Container build |
| `docker-compose.yaml` | Orchestration + volume mounts |
