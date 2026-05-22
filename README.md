# reaped-whirlwind

A home-lab weather/radar pipeline for the DFW area — and an **experimental, research-grade**
attempt to flag tornado risk from radar with a small CNN. Four services that used to be separate
Docker projects, now unified into one compose stack with one-command deploy.

> **Honesty note.** The ML layer is a research experiment, **not** a safety system. The
> authoritative tornado alert is the **National Weather Service**, which this stack already
> ingests. The model (Part B/C) will stay permanently labeled *experimental*. See
> `docs/MODEL_CARD.md` for what it can and cannot do.

## Services
| Service (compose) | Container | Port | Role |
|---|---|---|---|
| `screenshot` | screenshot-service | — | Every 10 min, capture KFWS/DFW base **reflectivity + velocity** PNGs from radar.weather.gov |
| `processor` | radar-image-processor | 9005 | Convert radar PNGs → JSON value-grids (dBZ, knots) |
| `weather` | weather-reporter | 9006 | Fetch NWS DFW bulletins (incl. active alerts) every 10 min |
| `dashboard` | pipeline-dashboard | 9007 | Monitor + control the stack |

(`inference`, added in Part C, runs the CNN and raises email alerts.)

## Quick start
```bash
cp .env.example .env          # set NWS_UA contact + (later) SMTP creds
docker compose up -d --build
# dashboard http://<nas>:9007 · processor http://<nas>:9005 · weather http://<nas>:9006
```
Backend/config/studio-list-style tweaks then apply with `docker compose restart <service>` (the
service code + config are bind-mounted); only frontend/image changes need `--build`. See
`docs/DEPLOY.md`.

## Layout
```
docker-compose.yml      # one project: reaped-whirlwind
.env.example            # canonical list of every env var (.env is gitignored)
services/ screenshot/ processor/ weather/ dashboard/ inference/(Part C)
ml/                     # CNN training/eval — runs on the Mac, not deployed
data-tools/             # radar/event downloader + batch converter
alerting/               # shared email-alert helper (Part C)
docs/                   # ARCHITECTURE, DEPLOY, DATA, MODEL_CARD
```

## Data (not in this repo)
Live captures, processed JSON, NWS reports, and the ~389 GB tornado-positives archive live on the
NAS under `/volume1/docker/...` and are **bind-mounted**, not committed. The compose file documents
the host paths; `.gitignore` keeps the data out of git.

## The project, honestly
Goal: collect radar (reflectivity **and** velocity) + NWS bulletins and see whether a small CNN can
distinguish tornadic from non-tornadic storms well enough to be interesting. Known hard parts:
limited/sparse velocity positives, train/serve domain matching, and rare-event evaluation. The build
is staged so an **honest offline evaluation gates** any production model work (`docs/MODEL_CARD.md`).
