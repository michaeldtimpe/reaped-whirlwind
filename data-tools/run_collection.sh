#!/usr/bin/env bash
#
# reaped-whirlwind — full Part-B data collection (run on any Mac/Linux box with internet).
# Builds a venv, downloads domain-matched per-station radar for tornado positives and
# hail+wind+warning-no-tornado negatives (2020-2025), and preprocesses to model tensors.
#
#   ./run_collection.sh                # full run (a few hours, ~few GB)
#   ./run_collection.sh --sample 30    # quick smoke test (1 recent year, ~10 min)
#   ./run_collection.sh --cap-pos 1500 --cap-neg-each 700   # smaller full run
#
# RESUME-SAFE: re-running the same command after a crash/kill skips all events whose
# manifest rows are already committed (manifest rows are written only AFTER all PNGs
# are validated on disk, so resume self-heals partial events). Per-PNG revalidation
# catches truncated files from prior hard kills.
#
# Output: data/full/tensors/*.npy  +  data/full/tensors_manifest.csv
# Log:    data/full/logs/collect_<timestamp>.log
#
# On macOS, this script wraps its Python under `caffeinate -ims` so a multi-hour run
# isn't interrupted by idle/system sleep on AC power. Display is allowed to sleep.
set -euo pipefail
cd "$(dirname "$0")/.."                      # repo root
OUT="data/full"
PY="${PYTHON:-python3}"

# --- runner-owned logging: tee everything (stdout+stderr) to a timestamped log file ---
TS="$(date +%Y%m%d_%H%M%S)"
LOGDIR="$OUT/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/collect_${TS}.log"
exec > >(tee -a "$LOG") 2>&1
echo "[log] $LOG"

CAF=""
if command -v caffeinate >/dev/null 2>&1; then
  CAF="caffeinate -ims"
fi

echo "[1/3] venv + deps..."
$PY -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r data-tools/requirements.txt

echo "[2/3] collecting radar (downloads to $OUT; throttled, be patient)..."
$CAF .venv/bin/python data-tools/collect.py --out "$OUT" "$@"

echo "[3/3] preprocessing to canonical 2x128x128 tensors..."
$CAF .venv/bin/python ml/preprocess.py --data "$OUT"

echo
echo "DONE."
echo "  tensors:  $OUT/tensors/            (2x128x128 float32 .npy)"
echo "  manifest: $OUT/tensors_manifest.csv"
echo "  log:      $LOG"
echo "Next: train here, or copy '$OUT' to your training machine."
