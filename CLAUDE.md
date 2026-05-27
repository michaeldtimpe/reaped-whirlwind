# CLAUDE.md — reaped-whirlwind

Orientation for a fresh Claude Code session (e.g. resuming on the analysis machine). Read this
first; it captures the current state and how to continue.

## What this is
A home weather/radar pipeline for the DFW area, plus an **experimental, research-grade** CNN that
attempts to flag tornado risk from radar. **Honesty:** the National Weather Service is the real
alert — this model is a research layer and will not beat NWS. It stays permanently labeled
*experimental*.

## Current status (2026-05-27)
- **Part A — DONE & deployed.** The four services (screenshot / processor / weather / dashboard) are
  unified into ONE compose project **`reaped-whirlwind`**, live on the **kappa** NAS at
  `/volume1/docker/reaped-whirlwind` (ports 9005 processor / 9006 weather / 9007 dashboard).
  Code+config bind-mounted (backend change = `restart`, not rebuild). See `docs/DEPLOY.md`.
- **Part B — DONE.** Full data collection (15,861 scans / 3,198 events), training, and held-out
  evaluation are all complete. Canonical model lives at `models/v1/` (model.pt + manifest.json +
  run.json + eval.json + MANIFEST.md). Eval: PR-AUC 0.485 vs baselines 0.26-0.34; 5 % FP on
  `warning_no_tornado` (the hardest negative class); higher hail/wind FPs deliberately made
  operationally irrelevant by Part C's NWS gate. See `docs/MODEL_CARD.md`.
- **Part C — DONE & deployed.** Inference (9008) and alerting (9009) are live on kappa alongside the
  Part A stack.
  - `services/inference/` fetches IEM RIDGE KFWS N0B+N0S every 5 min, runs the canonical CNN,
    writes `service-status/inference_status.json`. Uses the *same* preprocess transform as training
    (`ml/preprocess.decode_crop`); the bit-equivalence regression test
    (`services/inference/test_tensor_equiv.py`) passes 5/5 against saved training tensors.
  - `services/alerting/` polls NWS `/alerts/active` every 5 min and emails/SMS-es each unseen
    warning whose `event` is in `ALLOWED_EVENTS` (default: Tornado Warning, Severe Thunderstorm
    Warning, Flash Flood Warning, Flood Warning, High Wind Warning, Winter Storm Warning, Ice Storm
    Warning, Extreme Wind Warning). Layered suppression: per-`alert_id` dedupe, per-event-type
    rolling 24 h cap (`DAILY_CAP_SECONDS`), global 30-min cool-off (`COOL_OFF_SECONDS`) — Tornado
    Warning bypasses cool-off (`COOL_OFF_BYPASS_EVENTS`). Tornado Warning carries the model score
    as a numeric annotation; other event types show "model readout N/A — CNN assesses tornado risk
    only." Two recipient lists: `ALERT_TO` (full body) + `ALERT_TO_SMS` (~140-char body for
    email→SMS gateway). **No NWS warning in `ALLOWED_EVENTS` ⇒ no email.**
  - Dashboard's SERVICES dict already references inference + alerting; both are visible at
    `http://kappa:9007/`.

## Verifying Part C without an actual tornado

```bash
# 1) Bit-equivalence regression (proves no train/serve skew)
.venv/bin/python services/inference/test_tensor_equiv.py --n 5

# 2) Email + SMS dry-run for any allowlisted event type (no SMTP traffic)
docker-compose -p reaped-whirlwind run --rm alerting \
  python /app/alert_service.py --test-email --dry-run \
  --event "Severe Thunderstorm Warning"

# 3) Live SMTP test (sends a clearly-marked TEST FIXTURE email to ALERT_TO)
docker-compose -p reaped-whirlwind run --rm alerting \
  python /app/alert_service.py --test-email --event "Tornado Warning"

# 4) Health checks on kappa
curl http://kappa:9008/health   # inference: status "running"
curl http://kappa:9009/health   # alerting:  status "running"; check active_warnings + allowed_events
```

## The Part-B gate verdict (where the soft-GO came from)
The CNN clears all three baselines (reflectivity intensity, velocity shear, majority) AND has a
low FP rate on `warning_no_tornado` (5 %, the hardest negative class). Absolute precision is
modest at higher recall, but that's collapsed by Part C's design — the model is never the alert,
only an annotation on an NWS-triggered alert. See `docs/MODEL_CARD.md` for the full results +
tuning notes.

## Repo layout
```
docker-compose.yml         # unified stack (kappa); now 6 services
services/ screenshot/ processor/ weather/ dashboard/ inference/ alerting/
data-tools/ collect.py iem.py run_collection.sh README.md      # Part B data collection
            (iem.py is shared with services/inference)
ml/ preprocess.py dataset.py model.py train.py evaluate.py run_training.sh
models/ v1/ {model.pt, manifest.json, run.json, eval.json, MANIFEST.md}  # canonical deploy
docs/ ARCHITECTURE.md DEPLOY.md DATA.md MODEL_CARD.md
```
Data (radar tensors, the ~389 GB positives archive, live captures) is **not** in the repo — it's
bind-mounted on kappa and/or generated locally under `data/` (gitignored). Secrets live in `.env`
(gitignored; copy from `.env.example`).

## Key gotchas (do not relearn these)
- **Training Python:** torch needs 3.11–3.13; default `python3` on recent Macs is 3.14. `ml/run_training.sh` auto-picks a compatible interpreter.
- **Radar data:** per-station IEM RIDGE **N0B** (reflectivity) + **N0S** (storm-relative velocity),
  2020–2025 era; PNGs are paletted (decode by index). Details + the era/product gotchas in
  `data-tools/README.md`.
- **kappa ops:** the mage-hands relay container is `restart=no` — **never bounce Docker / Container
  Manager via the relay** (it kills your own access). A CLI recreate leaves a `<id>_name` Container
  Manager ghost (clear via DSM Stop/Start, only after the new stack is verified healthy). Deploy via
  `git archive HEAD | ssh magehands@192.168.1.248 'cat > /tmp/x'` then relay-extract as root —
  **scp/SFTP is disabled** on kappa. See `docs/DEPLOY.md`.
- **Relay timeout:** `mcp__kappa__run` (the kappa MCP server) hard-caps every command at **300 s**.
  Docker builds longer than that (e.g. the inference image pulling the torch CPU wheel) will appear
  to "time out" while still running on kappa. Pattern: kick off via `nohup ... > /tmp/x.log 2>&1 &`,
  then poll `tail /tmp/x.log` + `mcp__kappa__list_containers` until done.
- **magehands docker access:** the `magehands` host user is now in the `docker` group, and
  `/usr/bin/docker` + `/usr/bin/docker-compose` symlink into `/usr/local/bin/...` so non-interactive
  ssh sessions (`ssh magehands@192.168.1.248 'docker ps'`) work without absolute paths. The relay is
  still the right tool for root-required work; magehands ssh is good for `docker ps/logs/exec`.

## Conventions
- Don't commit data or secrets (`.gitignore` covers `data/`, `.env`, `.venv*/`, `ml/runs/`, weights).
- Backend/config change on kappa → `docker-compose restart <svc>`; image/dep change → `up -d --build`.
- The model stays *experimental*; the NWS-warning alert path (Part C) is primary and independent.
