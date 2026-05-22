# Screenshot Service — Agent Guide

Instructions for AI agents making modifications to this service.

## Project context

This is one of three services in a weather-data pipeline running on a Synology NAS via Docker. The screenshot service is the **first stage**: it captures radar images that are later processed by the Radar Processor service.

## File inventory

| File | Role | Safe to edit? |
|------|------|--------------|
| `screenshot_service.py` | All application logic (single file) | Yes |
| `config.yaml` | Runtime config — URLs, intervals, viewport | Yes |
| `Dockerfile` | Container build — base image includes Playwright | Careful |
| `docker-compose.yaml` | Volume mounts and env vars | Careful |
| `requirements.txt` | Python dependencies (only pyyaml) | Yes |

## Key design decisions

1. **Single-file service** — all logic is in `screenshot_service.py`. Keep it that way unless complexity demands a split.
2. **Status JSON** — the file `/status/screenshot_status.json` is read by the central dashboard. If you change its schema, also update the dashboard's `status_reader.py`.
3. **Playwright base image** — the Dockerfile uses Microsoft's official Playwright image. Changing the base image will break browser automation.
4. **No web server** — this service intentionally has no HTTP endpoints. Monitoring is done via the status JSON file and the central dashboard.

## Common modifications

### Add a new URL to capture
Edit `config.yaml` → `urls` list. No code changes needed.

### Change capture interval
Edit `config.yaml` → `interval_seconds`. No code changes needed.

### Change viewport / resolution
Edit `config.yaml` → `screenshot.viewport_width` and `viewport_height`.

### Add an HTTP health endpoint
Would require adding Flask/FastAPI to `requirements.txt` and a background thread in `screenshot_service.py`. Consider whether the status JSON file is sufficient first.

### Modify the status JSON schema
Update `self._stats` dict in `ScreenshotService.__init__` and `_write_status`. Then update the dashboard's reader to match.

## Testing

There are no unit tests. To validate changes:

```bash
# Build and run
docker-compose up --build

# Watch logs
docker-compose logs -f

# Check output
ls -la /volume1/docker/weather-screenshots/

# Check status
cat /volume1/docker/service-status/screenshot_status.json
```

## Gotchas

- Playwright needs the Microsoft base image — `pip install playwright` alone won't work because system-level browser binaries are required.
- The service runs as `root` inside the container because Playwright's default `pwuser` can conflict with Synology's PUID/PGID volume permissions.
- Screenshots are full-viewport only (no `full_page=True`). Radar sites often have fixed-size canvases so full-page mode produces blank space.
