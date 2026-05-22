# Screenshot Service

Headless-browser service that captures weather radar screenshots on a configurable interval and writes them as timestamped PNGs.

## Quick start

```bash
docker-compose up -d --build
```

## How it works

Every `interval_seconds` (default 600 s / 10 min) the service launches a headless Chromium browser via Playwright, visits each URL in `config.yaml`, waits for content to render, and saves a full-viewport screenshot to `/screenshots`.

Files are named `{prefix}_{YYYYMMDD_HHMMSS_UTC}.png`.

A small JSON status file is written to `/status/screenshot_status.json` after every cycle so the monitoring dashboard can report health.

## Configuration

All settings live in **`config.yaml`** (mounted read-only into the container).

| Key | Description | Default |
|-----|-------------|---------|
| `output_dir` | Container path for screenshots | `/screenshots` |
| `interval_seconds` | Seconds between capture cycles | `600` |
| `screenshot.timeout` | Page-load timeout (s) | `30` |
| `screenshot.wait_time` | Extra render wait (s) | `5` |
| `screenshot.viewport_width` | Browser width (px) | `1920` |
| `screenshot.viewport_height` | Browser height (px) | `1080` |
| `urls[].url` | Page to screenshot | — |
| `urls[].prefix` | Filename prefix | — |
| `logging.level` | Python log level | `INFO` |

## Volumes

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `/volume1/docker/weather-screenshots` | `/screenshots` | Output PNGs |
| `/volume1/docker/service-status` | `/status` | Status JSON for dashboard |

## Files

| File | Purpose |
|------|---------|
| `screenshot_service.py` | Service entry point |
| `config.yaml` | Configuration |
| `Dockerfile` | Container build |
| `docker-compose.yaml` | Orchestration |
| `requirements.txt` | Python deps |
