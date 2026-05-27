# Architecture

## Overview
Six long-running services in one compose project (`reaped-whirlwind`). Services do not call each
other over the network — they coordinate through **shared host volumes** under `/volume1/docker/`
and small status JSONs.

```
                                  screenshot+processor produce a human-readable
                                  dashboard view; they do NOT feed the model.
screenshot ──PNG──> weather-screenshots/ ──> processor ──JSON──> processed-weather-screenshots/
                                                                       │
weather ──txt──> weather-reports/  (NWS bulletins + alerts)            │
   all services ──status JSON──> service-status/  <── dashboard reads ─┘
                                                  dashboard also controls via docker.sock

                                  inference fetches its own IEM data so the live
                                  pipeline matches the training pipeline byte-for-byte.
IEM RIDGE KFWS N0B+N0S ─────► inference ──► service-status/inference_status.json
                                                                                │
                                                                                ▼
NWS /alerts/active ─────► alerting ──► email (SMTP) ──► you
                          NWS warning is the alert authority; the model score is
                          appended as a numeric annotation only.
```

## Services
- **screenshot** (Playwright, Python): every 10 min, headless-captures KFWS/DFW **base reflectivity**
  and **base velocity** from radar.weather.gov (1920×1080 PNG) → `weather-screenshots/`.
- **processor** (Python, PIL + KD-tree color→value): converts PNG → JSON value-grids (dBZ / knots,
  sampled) → `processed-weather-screenshots/`. Exposes `/health` (9005). For human/dashboard use;
  not consumed by the ML inference path (different palette/encoding from training data — see below).
- **weather** (Python, NWS API): every 10 min writes a DFW text report (incl. active alerts) →
  `weather-reports/`. Config (region/lat/lon/UA/interval) via env.
- **dashboard** (Flask): reads status JSONs + Docker API; serves UI (9007); can stop/start (Docker
  API) and rebuild (`docker-compose -p reaped-whirlwind up -d --build <service>`) each service.
- **inference** (Python, torch CPU, port 9008): every 5 min, fetches the latest KFWS N0B + N0S
  from IEM RIDGE directly, applies the **same** `ml/preprocess.decode_crop` used in training, runs
  the CNN at `models/v1/model.pt`, writes `service-status/inference_status.json`. Refuses to start
  on model SHA / `preprocess_version` mismatch. Does NOT alert.
- **alerting** (Python, port 9009): every 5 min, GETs NWS `/alerts/active` for the KFWS point,
  filters to active Tornado Warnings, composes one email per unseen warning with the NWS text first
  and a small model-readout annotation below, sends via SMTP. Dedupes by `alert_id` in
  `service-status/alerts_sent.json`. **No NWS warning ⇒ no email.**

## Shared host volumes (bind-mounted, not in repo)
| Host path | Writer → Readers |
|---|---|
| `weather-screenshots/` | screenshot → processor (also holds the tornado-positives archive) |
| `processed-weather-screenshots/` | processor → dashboard (NOT inference) |
| `weather-reports/` | weather → human read |
| `service-status/` | all → dashboard; inference → alerting (the score) |
| `radar-image-processor-logs/` | processor |
| `inference-state/` | inference (writable; cached KFWS.wld + per-cycle PNGs) |
| `inference-logs/`, `alerting-logs/` | per-service logs |

## ML pipeline (Parts B + C)

**Data** (Part B, done): confirmed-tornado positives (NOAA SPC, 2020–2025) + hard negatives
(hail, wind, tornado-warning-without-tornado) collected per-station from **IEM RIDGE N0B**
(super-res reflectivity) + **N0S** (storm-relative velocity). 15,861 scans / 3,198 events. Decoded
by palette **index** (not RGB color match), cropped to a ~120 km box, resized to 128×128, stacked
2-channel (refl, vel). See `data-tools/collect.py`, `ml/preprocess.py`, `docs/DATA.md`.

**Model** (Part B, done): a compact ~0.5 M-param CNN (`ml/model.py`). Trained on M1 MPS with
`--patience 2` early-stopping. Best val PR-AUC 0.660 at epoch 1; test PR-AUC 0.485. The crucial
operational number is the **5% FP rate on `warning_no_tornado`** — the model is good at
distinguishing tornadic from severe-but-non-tornadic *within* a TO.W. Hail/wind FP rates are
much higher (43-51%), which is why the alerting path is NWS-gated.

**Inference** (Part C, done): the live container fetches its own IEM data so it consumes the
**same source + same transform** the training did — no train/serve skew. The
`services/inference/test_tensor_equiv.py` regression asserts bit-equivalence against saved
training tensors. Inference imports `decode_crop`, `load_wld`, `REFL`, `VEL`, `PREPROCESS_VERSION`
directly from `ml/preprocess.py`; the model is loaded with `weights_only=True` from
`models/v1/model.pt`.

**Alerting** (Part C, done): NWS warning is primary, unconditional, independent. The model is
permanently experimental; its score is a numeric annotation on emails that the NWS warning
triggers. Email subject is `Tornado Warning: <area> (model: elevated|not elevated|unavailable)`.
Body has the full NWS text first and the model readout below in mechanical language ("score below
threshold", "radar features did not reach cutoff") — no "agrees/disagrees/sees" phrasing.

## Why doesn't inference reuse the screenshot/processor pipeline?
The screenshot service captures `radar.weather.gov` (a *visualization* — palette + style + chrome)
and the processor decodes RGB → value via KD-tree nearest-color into dBZ/knots. The model was
trained on IEM RIDGE PNGs decoded by palette **index** (normalized to [0, 1] for refl / [0, 1]
across 16 levels for vel). The two paths are in a different value domain. Mixing them would
introduce train/serve skew; instead the inference service fetches IEM directly so the
distribution is identical.

## Deploy
Code + config are bind-mounted, so backend changes apply via `restart`; only image/frontend
changes need `--build`. Deployed to the NAS via the mage-hands relay. See `docs/DEPLOY.md`.
