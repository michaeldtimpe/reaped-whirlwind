# Pipeline Dashboard — Architecture

## System position

```
┌───────────────┐  ┌────────────────┐  ┌─────────────────┐
│  Screenshot   │  │  Radar         │  │  Weather        │
│  Service      │  │  Processor     │  │  Reporter       │
│               │  │                │  │                 │
│ writes:       │  │ writes:        │  │ writes:         │
│ screenshot_   │  │ processor_     │  │ weather_        │
│ status.json   │  │ status.json    │  │ status.json     │
│ + PNGs        │  │ + JSONs        │  │ + text reports  │
└───────┬───────┘  └───────┬────────┘  └────────┬────────┘
        │                  │                     │
        ▼                  ▼                     ▼
   /volume1/docker/service-status/  (shared volume)
   /volume1/docker/weather-screenshots/
   /volume1/docker/processed-weather-screenshots/
        │                  │                     │
        └──────────────────┼─────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  Dashboard  │
                    │  :9007      │
                    │             │
                    │  reads:     │
                    │  - status/* │
                    │  - PNGs     │
                    │  - JSONs    │
                    │             │
                    │  controls:  │
                    │  - docker   │
                    │    socket   │
                    └─────────────┘
```

## Internal architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    dashboard_server.py                        │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Status       │  │ Image        │  │ Service Control    │  │
│  │ Reader       │  │ Server       │  │                    │  │
│  │              │  │              │  │ POST /stop         │  │
│  │ reads JSON   │  │ serves       │  │ POST /start        │  │
│  │ from /status │  │ latest PNG   │  │ POST /update       │  │
│  │              │  │ from disk    │  │                    │  │
│  └──────┬───────┘  └──────┬───────┘  │ runs:             │  │
│         │                 │          │ docker compose     │  │
│         │                 │          │ stop/start/up -d   │  │
│         │                 │          │ --build            │  │
│         │                 │          └─────────┬──────────┘  │
│         │                 │                    │             │
│         ▼                 ▼                    ▼             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                   Flask (threaded)                    │   │
│  └──────────────────────────────────────────────────────┘   │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │            static/index.html                          │   │
│  │                                                       │   │
│  │  Polls /api/status every 10s                          │   │
│  │  Renders service cards, metrics, previews             │   │
│  │  Sends POST commands for stop/start/update            │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

## Data flow

### Status polling (every 10 s)
1. Frontend `fetch('/api/status')`.
2. Backend reads `screenshot_status.json`, `processor_status.json`, `weather_status.json`.
3. Backend runs `docker inspect` on each container for live state.
4. Backend stat()s screenshot and processed directories for latest files and folder sizes.
5. Returns aggregated JSON to frontend.

### Service command (user clicks Stop/Start/Update)
1. Frontend `POST /api/services/{name}/{action}`.
2. Backend acquires a mutex (one command at a time).
3. Backend runs `docker compose {stop|start|up -d --build}` in the service's project directory.
4. Returns stdout/stderr/exit-code to frontend.
5. Frontend shows a toast notification and triggers an immediate re-poll.

### Image preview
1. Frontend sets `<img src="/api/screenshots/latest?t=...">`.
2. Backend finds the newest `radar_*.png` in `/screenshots` by mtime.
3. Returns it as `image/png`.

## Security model

- The Docker socket grants **root-equivalent** access to the host Docker daemon.
- The service project directories are mounted **read-only** — `docker compose` reads `docker-compose.yaml` and `Dockerfile` from them but doesn't write.
- The `_cmd_lock` mutex serialises Docker commands so two concurrent rebuilds can't conflict.
- No authentication is built in.  Intended for LAN-only access.

## Error handling

- Status file missing/corrupt → service shows as "OFFLINE" with null data.
- Docker inspect failure → state shows as "unknown".
- Compose command failure → error returned to frontend, shown in toast.
- Network poll failure → silently retries on next interval.
