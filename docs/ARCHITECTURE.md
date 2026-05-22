# Architecture

## Overview
Four long-running services plus (Part C) an inference service, all in one compose project
(`reaped-whirlwind`). Services do not call each other over the network — they coordinate through
**shared host volumes** under `/volume1/docker/` and small status JSONs.

```
screenshot ──PNG──> weather-screenshots/ ──> processor ──JSON──> processed-weather-screenshots/
                                                                       │
weather ──txt──> weather-reports/  (NWS bulletins + alerts)            │
   all services ──status JSON──> service-status/  <── dashboard reads ─┘
                                                  dashboard also controls via docker.sock
(Part C) inference reads processed-weather-screenshots/ ──> service-status/inference_status.json
         alerting reads NWS alerts + inference score ──> email
```

## Services
- **screenshot** (Playwright, Python): every 10 min, headless-captures KFWS/DFW **base reflectivity**
  and **base velocity** from radar.weather.gov (1920×1080 PNG) → `weather-screenshots/`.
- **processor** (Python, PIL + KD-tree color→value): converts PNG → JSON value-grids (dBZ / knots,
  sampled) → `processed-weather-screenshots/`. Exposes `/health` (9005). Velocity is the key tornado
  signal; reflectivity alone is weak.
- **weather** (Python, NWS API): every 10 min writes a DFW text report (incl. active alerts) →
  `weather-reports/`. Config (region/lat/lon/UA/interval) via env.
- **dashboard** (Flask): reads status JSONs + Docker API; serves UI (9007); can stop/start (Docker
  API) and rebuild (`docker-compose -p reaped-whirlwind up -d --build <service>`) each service.

## Shared host volumes (bind-mounted, not in repo)
| Host path | Writer → Readers |
|---|---|
| `weather-screenshots/` | screenshot → processor (also holds the tornado-positives archive) |
| `processed-weather-screenshots/` | processor → dashboard, inference |
| `weather-reports/` | weather → (alerting) |
| `service-status/` | all → dashboard |
| `radar-image-processor-logs/` | processor |

## ML (Parts B/C — research)
- **Data**: confirmed-tornado positives (NOAA SPC + radar archive) and collected hard negatives
  (severe-but-non-tornadic, esp. tornado-warning-without-tornado), converted to a canonical
  2-channel (reflectivity, velocity) grid, sequence-ready for a later temporal model.
- **Model**: a small CNN, trained on the Mac, evaluated with leakage-safe splits and **operational**
  metrics (precision at a fixed alert budget) — this gate decides whether Part C proceeds.
- **Inference/alerting** (Part C): inference runs on the NAS CPU; email alerts are sent with the
  **NWS warning path primary/unconditional** and the model path clearly labeled *experimental*.

## Deploy
Code + config are bind-mounted, so backend changes apply via `restart`; only image/frontend changes
need `--build`. Deployed to the NAS via the mage-hands relay. See `docs/DEPLOY.md`.
