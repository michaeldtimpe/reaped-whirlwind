# reaped-whirlwind

A home-lab weather/radar pipeline for the DFW area — and an **experimental, research-grade**
attempt to flag tornado risk from radar with a small CNN.

> **Honesty note.** The ML layer is a research experiment, **not** a safety system. The
> authoritative tornado alert is the **National Weather Service**, which this stack ingests
> independently and unconditionally. The model stays permanently labeled *experimental* — its
> score is only ever an annotation on an NWS-triggered alert, never the alert itself. See
> `docs/MODEL_CARD.md` for what it can and cannot do.

## Status
Parts A–D are done and deployed on the **kappa** NAS: the unified compose stack (A), the
tornado CNN — data collection, training, held-out eval (B), and live inference + NWS-gated
multi-event alerting (C, D). See [`CLAUDE.md`](CLAUDE.md) for the full current-state summary,
resume commands, and operational gotchas — **start there** for a fresh session.

## Services
| Service (compose) | Port | Role |
|---|---|---|
| `screenshot` | — | Every 10 min, capture KFWS base **reflectivity + velocity** PNGs from radar.weather.gov |
| `processor` | 9005 | Convert radar PNGs → JSON value-grids (dBZ, knots), for human/dashboard use |
| `weather` | 9006 | Fetch NWS DFW bulletins (incl. active alerts) every 10 min |
| `dashboard` | 9007 | Monitor + control the stack |
| `inference` | 9008 | *(experimental)* Score live KFWS radar with the tornado CNN every 5 min |
| `alerting` | 9009 | *(experimental annotation)* NWS-gated email/SMS on severe-weather warnings |

## Quick start
```bash
cp .env.example .env          # set NWS_UA contact + SMTP creds
docker compose up -d --build
# dashboard http://<nas>:9007 · processor :9005 · weather :9006 · inference :9008 · alerting :9009
```
Backend/config changes then apply with `docker compose restart <service>` (code + config are
bind-mounted); image/dependency changes, or a new volume mount, need `up -d --build` / `up -d`.
See `docs/DEPLOY.md`.

## Tests
```bash
scripts/test.sh
```
Runs the pytest suite under `tests/` (shared `common/` helpers + per-service logic). Local/CI
only — not part of the deployed images.

## Layout
```
docker-compose.yml      # one project: reaped-whirlwind, 6 services
.env.example            # canonical list of every env var (.env is gitignored)
services/ screenshot/ processor/ weather/ dashboard/ inference/ alerting/
common/                 # shared status-file + NWS helpers (KFWS coords, default event allowlist)
tests/ scripts/test.sh  # pytest suite + entry point
ml/                     # CNN training/eval — runs on the analysis machine, not deployed
data-tools/             # radar/event downloader + batch converter
models/v1/              # canonical deployed model (model.pt + manifest + eval)
docs/                   # ARCHITECTURE, DEPLOY, DATA, MODEL_CARD, RETROSPECTIVE
```

## Docs
- [`CLAUDE.md`](CLAUDE.md) — orientation, current state, resume commands, gotchas
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the six services fit together
- [`docs/DEPLOY.md`](docs/DEPLOY.md) — deploying to kappa, restart-vs-rebuild, troubleshooting
- [`docs/DATA.md`](docs/DATA.md) — training data sources, pipeline, retention policy
- [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) — model intent, limitations, eval results
- [`docs/RETROSPECTIVE.md`](docs/RETROSPECTIVE.md) — post-deployment review

## Data (not in this repo)
Live captures, processed JSON, NWS reports, and training tensors live outside git — on the NAS
under `/volume1/docker/...` (bind-mounted) or on the analysis machine under `data/` (gitignored).
See `docs/DATA.md` for what's kept, what's superseded, and why.

## The project, honestly
Goal: collect radar (reflectivity **and** velocity) + NWS bulletins and see whether a small CNN
can distinguish tornadic from non-tornadic storms well enough to be interesting. Known hard
parts: limited/sparse velocity positives, train/serve domain matching, and rare-event
evaluation. An honest offline evaluation gated any production model work (`docs/MODEL_CARD.md`);
in production, an independent NWS gate keeps the model's false positives from ever reaching you
directly.
