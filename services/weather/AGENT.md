# Weather Reporter — Agent Guide

Instructions for AI agents making modifications to this service.

## Project context

Third service in a weather-data pipeline. This service is independent of the other two (Screenshot and Radar Processor) — it talks directly to the NWS API, not to radar images.  The central Dashboard reads `/status/weather_status.json` to show the latest weather report.

## File inventory

| File | Role | Safe to edit? |
|------|------|--------------|
| `weather_report_generator.py` | All logic (single file) | Yes |
| `Dockerfile` | Container build | Careful |
| `docker-compose.yaml` | Volumes, ports | Careful |

## Key design decisions

1. **Single-file service** — all logic in one file.  Keep it that way.
2. **No external config file** — settings are `Config` class constants.  For Docker overrides, use env vars (you'd need to add `os.getenv` calls).
3. **Built-in HTTP server** — uses stdlib `http.server` in a daemon thread.  No Flask dependency.
4. **Status JSON contract** — `/status/weather_status.json` is read by the central dashboard.  Schema: `{status, last_report_time, report_text, region}`.
5. **NWS API rate limits** — the API has no key but requests a User-Agent string.  The 10-minute interval is well within their rate limits.

## Common modifications

### Change location
Edit `Config.LATITUDE`, `Config.LONGITUDE`, and `Config.REGION_NAME`.

### Change report interval
Edit `Config.INTERVAL` (seconds).

### Add a new data source (e.g., radar text products)
Add a new method to `Fetcher`, call it in `Generator._cycle()`, and add a new formatter function.

### Add hourly forecast
The NWS `/forecast/hourly` endpoint returns more granular data.  Add `Fetcher.hourly_forecast()` and a `format_hourly()` function.

### Modify the status JSON schema
Update `_write_status()` in the generator.  Then update the dashboard's reader.

## Testing

```bash
# Local test (no Docker)
pip install requests
python weather_report_generator.py

# Docker
docker-compose up --build
curl http://localhost:9006
cat /volume1/docker/service-status/weather_status.json
```

## Gotchas

- The NWS API occasionally returns 500s or empty responses.  The service handles this gracefully (partial reports) but don't assume every field will always be present.
- `report_text` in the status JSON is truncated to 4000 chars.  If you add verbose data, the dashboard may not see all of it.
- The HTML dashboard uses `<meta http-equiv="refresh" content="60">` for auto-refresh.  It's not a websocket — there's a 60-second lag.
