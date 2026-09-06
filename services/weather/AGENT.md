# services/weather — agent notes

**What it is.** Independent of screenshot/processor — talks directly to the NWS API, not to
radar images. Every `WEATHER_INTERVAL` seconds (default 600), fetches the forecast + active
alerts for `NWS_LAT,NWS_LON` and writes a text report plus a status JSON.

**Entry point.** `weather_report_generator.py` — single file. Config is env-driven via the
`Config` class: `NWS_REGION`, `NWS_LAT`, `NWS_LON`, `NWS_UA` (required contact string for
NWS's User-Agent policy), `WEATHER_INTERVAL` — all set in `docker-compose.yml`, no separate
config file.

**HTTP server.** Built-in stdlib `http.server` in a daemon thread on `:9006` (no Flask
dependency) — serves an auto-refreshing HTML view (`<meta http-equiv="refresh" content="60">`,
so updates lag up to 60s, not a websocket).

**Status file.** Writes `/status/weather_status.json` — schema
`{status, last_report_time, report_text, region}`; `report_text` is truncated to 4000 chars.
The dashboard's `SERVICES["weather-reporter"]` reads it — update its reader if you change
the schema.

**No dedicated `/health` route** distinct from `/` — the dashboard falls back to Docker
container state for this service.

**Gotchas.**
- The NWS API occasionally returns 500s or empty bodies; the service degrades to a partial
  report rather than crashing — don't assume every field is always present.
- Rate limits are unauthenticated (no API key) but NWS asks for an honest User-Agent
  (`NWS_UA`) — don't blank it out.

See `../../docs/ARCHITECTURE.md` for how this fits into the six-service pipeline.
