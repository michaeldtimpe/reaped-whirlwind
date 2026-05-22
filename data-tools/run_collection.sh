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
# Output: data/full/tensors/*.npy  +  data/full/tensors_manifest.csv
set -euo pipefail
cd "$(dirname "$0")/.."                      # repo root
OUT="data/full"
PY="${PYTHON:-python3}"

echo "[1/3] venv + deps..."
$PY -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r data-tools/requirements.txt

echo "[2/3] collecting radar (downloads to $OUT; throttled, be patient)..."
.venv/bin/python data-tools/collect.py --out "$OUT" "$@"

echo "[3/3] preprocessing to canonical 2x128x128 tensors..."
.venv/bin/python ml/preprocess.py --data "$OUT"

echo
echo "DONE."
echo "  tensors:  $OUT/tensors/            (2x128x128 float32 .npy)"
echo "  manifest: $OUT/tensors_manifest.csv"
echo "Next: train here, or copy '$OUT' to your training machine."
