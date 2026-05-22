# Weather Reporter — Architecture

## Overview

Single-process Python service with two threads: the main report loop and a background HTTP server.

```
┌──────────────────────────────────────────────────────┐
│              weather_report_generator.py              │
│                                                      │
│  ┌────────────┐    ┌──────────┐    ┌──────────────┐  │
│  │  Generator  │───▶│  Logger  │───▶│ /logs/*.txt  │  │
│  │  (10-min   │    └──────────┘    └──────────────┘  │
│  │   loop)    │    ┌──────────┐    ┌──────────────┐  │
│  │            │───▶│DashWriter│───▶│ index.html   │  │
│  │            │    └──────────┘    └──────┬───────┘  │
│  │            │                          │           │
│  │            │───▶ /status/weather_status.json      │
│  └────────────┘                          │           │
│                    ┌──────────┐          │           │
│                    │ HTTP Srv │◀─────────┘           │
│                    │ :9006    │  serves index.html    │
│                    └──────────┘                       │
└──────────────────────────────────────────────────────┘
         │
         ▼  (outbound)
   NWS API (api.weather.gov)
     /points → /stations → /observations/latest
     /forecast
     /alerts/active
```

## Report cycle

1. `Fetcher.grid()` → resolve lat/lon to NWS grid.
2. `Fetcher.observations()` → current temp, wind, humidity, etc.
3. `Fetcher.forecast()` → 5-period text forecast.
4. `Fetcher.alerts()` → active weather alerts for the point.
5. Format everything into a single plain-text report.
6. Write to `/logs/{REGION}_{timestamp}.txt`.
7. Write HTML dashboard to `/var/www/html/index.html`.
8. Write status JSON to `/status/weather_status.json`.

## Status file schema

```json
{
  "status": "running",
  "last_report_time": "2026-02-18T12:00:05+00:00",
  "report_text": "Weather Report Generated: …",
  "region": "DFW"
}
```

## Error handling

- Any NWS API failure → partial report written with "Failed to fetch" note.
- Full cycle exception → caught, logged, service continues after interval.
- Status/dashboard write failure → non-fatal.

## Dependencies

- **requests** — HTTP client for NWS API.
- Python stdlib (`http.server`, `json`, `html`, `threading`, `pathlib`, `datetime`).
