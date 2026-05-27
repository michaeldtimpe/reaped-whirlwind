# inference

The live tornado-risk inference service for **reaped-whirlwind**.
Pulls KFWS N0B + N0S from the IEM RIDGE archive every 5 minutes, scores
through the canonical CNN at `models/v1/model.pt`, and publishes the
score to `service-status/inference_status.json`.

This service does **not** send alerts. The `alerting` service consumes
this score as an annotation when an NWS tornado warning is active.

## How to run locally
```
docker-compose -p reaped-whirlwind up --build inference
curl http://localhost:9008/health | jq
```

## One-shot smoke
```
docker-compose run --rm inference python /app/inference_service.py --once
```

Prints the resulting status JSON; exit 0 if scoring succeeded, 1 otherwise.

## Status JSON
Atomically written to `/status/inference_status.json`. Schema:

| field | description |
|---|---|
| `status` | `uninitialized` / `running` / `stale` / `error` |
| `last_score` | sigmoid CNN output in [0,1], or `null` |
| `last_score_time` | wall time we computed it (UTC ISO) |
| `last_scan_time` | timestamp of the N0B PNG (UTC ISO) |
| `scan_delta_seconds` | N0S time − N0B time, absolute |
| `pair_n0b_file`, `pair_n0s_file` | the actual PNGs used |
| `model_sha256` | SHA256 of the loaded model.pt |
| `preprocess_version` | the transform contract version |
| `threshold` | the annotation threshold from `MODEL_RISK_THRESHOLD` |
| `cycle_ms` | per-stage timings (fetch / preprocess / infer / write) |
| `errors` | ring buffer of last 5 errors with timestamps |

## Env
| var | default | purpose |
|---|---|---|
| `PORT` | 9008 | Flask port |
| `POLL_INTERVAL` | 300 | seconds between cycles |
| `MAX_PAIR_DELTA_SECONDS` | 300 | skip cycle if no N0S within this of latest N0B |
| `MAX_SCORE_AGE_SECONDS` | 1800 | /health marks stale beyond this |
| `KFWS_LAT`, `KFWS_LON` | KFWS coords | crop center |
| `MODEL_PATH` | `/model/model.pt` | bind-mounted from `models/v1/` |
| `MANIFEST_PATH` | `/model/manifest.json` | same |
| `MODEL_FINGERPRINT` | unset | optional override; if set, must match `model.pt`'s SHA256 |
| `MODEL_RISK_THRESHOLD` | from `.env` | annotation-only |
| `STATUS_PATH` | `/status/inference_status.json` | output |
| `STATE_DIR` | `/state` | writable cache for `KFWS.wld` and current scans |

See `AGENT.md` for the correctness invariants and `docs/MODEL_CARD.md`
for the model's eval and tuning notes.
