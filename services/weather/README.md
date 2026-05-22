# Weather Report Generator

Fetches current conditions, forecast, and active alerts for the DFW metroplex from the National Weather Service API every 10 minutes.  Writes plain-text reports to `/logs` and serves an HTML dashboard on port 9006.

## Quick start

```bash
docker-compose up -d --build
```

Dashboard: `http://YOUR_NAS_IP:9006`

## How it works

Every 600 seconds the service calls four NWS endpoints (grid point, observations, forecast, alerts), formats the data into a plain-text report, writes it to `/logs/{REGION}_{timestamp}.txt`, updates the HTML dashboard, and writes a status JSON for the central monitoring dashboard.

## Configuration

All settings are constants in `Config` at the top of `weather_report_generator.py`.

| Setting | Default | Description |
|---------|---------|-------------|
| `REGION_NAME` | `DFW` | Name prefix for log files |
| `LATITUDE` / `LONGITUDE` | `32.8968 / -97.0380` | Location for NWS lookup |
| `INTERVAL` | `600` | Seconds between reports |
| `LOG_DIR` | `/logs` | Plain-text report output |
| `DASH_DIR` | `/var/www/html` | HTML dashboard output |

## Volumes

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| weather-reports | `/logs` | Text report files |
| service-status | `/status` | Status JSON for dashboard |

## Files

| File | Purpose |
|------|---------|
| `weather_report_generator.py` | All application logic |
| `Dockerfile` | Container build |
| `docker-compose.yaml` | Orchestration |
