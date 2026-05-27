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
- **Part C — code DONE + locally verified, kappa deploy PENDING.** Two new services:
  - `services/inference/` (port 9008) fetches IEM RIDGE KFWS N0B+N0S every 5 min, runs the
    canonical CNN, writes `service-status/inference_status.json`. Uses the *same* preprocess
    transform as training (`ml/preprocess.decode_crop`); the bit-equivalence regression test
    (`services/inference/test_tensor_equiv.py`) passes 5/5 against saved training tensors.
  - `services/alerting/` (port 9009) polls NWS `/alerts/active` every 5 min; sends one email per
    active Tornado Warning with the NWS text first and the model's score as a numeric annotation
    below. **No NWS warning ⇒ no email.** Dedup by NWS alert id; SMTP timeout=10; cap 5 per cycle.
    Multi-recipient: `ALERT_TO` (full body) + `ALERT_TO_SMS` (~140-char body for email→SMS
    gateway). Live SMTP smoke verified end-to-end via Gmail App Password to both michaeldtimpe@gmail.com
    AND a Google Fi SMS gateway.
  - Compose + dashboard wiring is in. `.env` lives at the repo root (gitignored) with all
    SMTP / NWS / KFWS / threshold values. Both arrive at gmail + SMS in <60s when sent.
  - Not yet pushed to kappa. The next session is the kappa deploy — user will provide MCP
    tools for kappa so the deploy can run remotely from Claude.

Full design rationale + review history lived in a local plan file
(`~/.claude/plans/eager-cooking-hollerith.md`) during development; the essentials are captured
in `docs/`.

## Resume here (next session): kappa deploy

The user is bringing up MCP tools for kappa so this can be driven from Claude.
Once those tools are available, the deploy sequence is:

```bash
# 1) pre-flight: confirm 9008/9009 free on kappa
ssh magehands@192.168.1.248 'sudo ss -tlnp | grep -E ":900[89]"' || true

# 2) push code + model. .env on kappa lives at /volume1/docker/reaped-whirlwind/.env;
#    if it doesn't exist yet, scp/cat the local .env there (same values, same path).
git archive HEAD | ssh magehands@192.168.1.248 'cat > /tmp/rw.tgz'
# (relay-extract per docs/DEPLOY.md — never bounce Docker via the relay)

# 3) bring up the new services
ssh magehands@192.168.1.248 'cd /volume1/docker/reaped-whirlwind \
  && docker-compose -p reaped-whirlwind up -d --build inference alerting dashboard'

# 4) verify on kappa
curl http://kappa:9008/health   # inference: expect status "running" within ~5 min of first cycle
curl http://kappa:9009/health   # alerting:  expect status "running"
# active_tornado_warnings should be 0 unless there's a real warning right now.
```

**Expectations on first kappa build:** torch CPU wheel pull is ~200 MB, build takes 5-10 min.
After that, restarts are instant. Once both services are up, the dashboard at 9007 will
discover them automatically (SERVICES dict entries are already deployed).

## How to verify Part C without an actual tornado
```bash
# 1) Bit-equivalence regression (mandatory — proves no train/serve skew)
.venv/bin/python services/inference/test_tensor_equiv.py --n 5

# 2) Inference one-shot smoke against live IEM
docker-compose -p reaped-whirlwind run --rm inference python /app/inference_service.py --once

# 3) Email pipeline dry-run (no SMTP traffic)
docker-compose -p reaped-whirlwind run --rm alerting \
  python /app/alert_service.py --test-email --dry-run

# 4) Real SMTP test (send to ALERT_TO)
docker-compose -p reaped-whirlwind run --rm alerting \
  python /app/alert_service.py --test-email
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

## Conventions
- Don't commit data or secrets (`.gitignore` covers `data/`, `.env`, `.venv*/`, `ml/runs/`, weights).
- Backend/config change on kappa → `docker-compose restart <svc>`; image/dep change → `up -d --build`.
- The model stays *experimental*; the NWS-warning alert path (Part C) is primary and independent.
