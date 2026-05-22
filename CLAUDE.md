# CLAUDE.md — reaped-whirlwind

Orientation for a fresh Claude Code session (e.g. resuming on the analysis machine). Read this
first; it captures the current state and how to continue.

## What this is
A home weather/radar pipeline for the DFW area, plus an **experimental, research-grade** CNN that
attempts to flag tornado risk from radar. **Honesty:** the National Weather Service is the real
alert — this model is a research layer and will not beat NWS. It stays permanently labeled
*experimental*.

## Current status (2026-05-22)
- **Part A — DONE & deployed.** The four services (screenshot / processor / weather / dashboard) are
  unified into ONE compose project **`reaped-whirlwind`**, live on the **kappa** NAS at
  `/volume1/docker/reaped-whirlwind` (ports 9005 processor / 9006 weather / 9007 dashboard).
  Code+config bind-mounted (backend change = `restart`, not rebuild). See `docs/DEPLOY.md`.
- **Part B — tooling DONE & smoke-tested; the experiment itself is the NEXT action.** Collection,
  preprocessing, training, and evaluation code are written and validated end-to-end on a tiny set.
  **The full data collection + CNN training + go/no-go evaluation have NOT been run yet** — that is
  what to do next, on this analysis machine.
- **Part C — productionize (live inference + email alerts) — NOT started; GATED on a passing Part-B
  evaluation.** (`services/inference/`, `alerting/` don't exist yet.)

Full design rationale + review history isn't in this repo (it was in a local plan file); the
essentials are captured here and in `docs/`.

## Resume here  (run on the analysis machine)
Prereqs: internet; **Python 3.11–3.13** for training (torch has no 3.14 wheels — the runner
auto-detects); a few GB free disk.
```bash
# 1) collect the domain-matched dataset (smoke test first, then full ~hours run)
cd data-tools
./run_collection.sh --sample 30        # ~10 min sanity check (1 recent year)
./run_collection.sh                    # full run -> ../data/full/tensors/

# 2) train + evaluate (THE go/no-go)
cd ..
./ml/run_training.sh data/full         # builds torch venv, trains CNN, runs evaluate.py
```
Then read `evaluate.py`'s output (saved to `ml/runs/<ts>/eval.json`) — that is the decision.

## The gate (Part-B go/no-go)
**GO** only if the CNN clearly beats all baselines (reflectivity intensity, velocity shear,
majority) **and** reaches usable precision at a tolerable false-alarm rate — in particular a low
false-positive rate on **warning-no-tornado** storms (telling tornadic from severe-but-non-tornadic
is the actual hard problem). Otherwise it's an honest **NO-GO** → archive / rescope. See
`docs/MODEL_CARD.md`.

## Repo layout
```
docker-compose.yml         # Part A: unified stack (deployed on kappa)
services/ screenshot/ processor/ weather/ dashboard/      # (inference/ = Part C, not yet)
data-tools/ collect.py run_collection.sh README.md        # Part B data collection
ml/ preprocess.py dataset.py model.py train.py evaluate.py run_training.sh
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
