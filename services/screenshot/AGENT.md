# services/screenshot — agent notes

**What it is.** First stage of the pipeline. Every `interval_seconds` (config.yaml, default
600s), uses Playwright (headless Chrome) to load two `radar.weather.gov` URLs (KFWS base
reflectivity + base velocity, baked into `config.yaml: urls`) and screenshots the viewport,
writing `radar_base_reflectivity_*.png` / `radar_base_velocity_*.png` to `/screenshots`
(bind-mounted to `/volume1/docker/weather-screenshots` on kappa, shared with the `processor`
service as its input).

**Entry point.** `screenshot_service.py` — single file, no HTTP server. `config.yaml` holds
URLs, interval, and viewport size; edits apply on `docker-compose restart screenshot` (no
rebuild — see `../../docs/DEPLOY.md`).

**Status file.** Writes `/status/screenshot_status.json` (`STATUS_FILE` constant) via
`_write_status()`; the dashboard's `SERVICES["screenshot-service"]` reads it. If you change
the schema, update the dashboard's status reader too.

**No health endpoint.** This service has no HTTP surface — dashboard monitoring is purely
via the status JSON and Docker container state (`compose ps`).

**Gotchas.**
- Runs as `root` in-container: Playwright's default `pwuser` conflicts with Synology
  PUID/PGID volume permissions.
- Full-viewport screenshots only (no `full_page=True`) — the radar.weather.gov canvas is
  fixed-size; full-page mode would capture blank space around it.
- The capture URLs encode the map center/zoom/layer as a base64 `settings=v1_...` query
  param — if radar.weather.gov changes its settings schema, these URLs need regenerating
  from the site UI, not hand-edited.

See `../../docs/ARCHITECTURE.md` for how this fits into the six-service pipeline.
