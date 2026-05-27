#!/usr/bin/env bash
#
# reaped-whirlwind — train + evaluate the tornado-risk CNN on collected tensors.
#   ./run_training.sh                              # uses data/full, 30 epochs
#   ./run_training.sh data/full --epochs 50
#   ./run_training.sh data/full --resume ml/runs/LATEST_CONTENTS   # continue an interrupted run
#
# RESUME-SAFE: each epoch persists last.pt (model+opt+epoch+best+RNG) and atomically
# rewrites run.json. To pick up a crashed/killed run, pass --resume <runpath> (the path
# in ml/runs/LATEST). Best-PR-AUC weights stay in model.pt across resumes.
#
# torch has no Python 3.14 wheels yet, so we pick a 3.11-3.13 interpreter for a
# dedicated .venv-train (separate from the collection venv).
#
# On macOS, training is wrapped under `caffeinate -ims` so MPS isn't paused mid-run.
set -euo pipefail
cd "$(dirname "$0")/.."             # repo root
DATA="${1:-data/full}"; shift || true

# --- runner-owned logging ---
TS="$(date +%Y%m%d_%H%M%S)"
LOGDIR="ml/runs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/train_${TS}.log"
exec > >(tee -a "$LOG") 2>&1
echo "[log] $LOG"

CAF=""
if command -v caffeinate >/dev/null 2>&1; then
  CAF="caffeinate -ims"
fi

PY=""
for c in python3.12 python3.11 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print(sys.version_info[:2]>=(3,11) and sys.version_info[:2]<=(3,13))')
    [ "$v" = "True" ] && PY="$c" && break
  fi
done
[ -z "$PY" ] && { echo "ERROR: need Python 3.11-3.13 for torch (found none). Install one (e.g. brew install python@3.12)."; exit 1; }
echo "using $PY ($($PY --version))"

[ -d .venv-train ] || "$PY" -m venv .venv-train
.venv-train/bin/pip install -q --upgrade pip
.venv-train/bin/pip install -q -r ml/requirements.txt

cd ml
$CAF ../.venv-train/bin/python train.py --data "../$DATA" "$@"
RUN=$(cat runs/LATEST)
echo "evaluating $RUN"
$CAF ../.venv-train/bin/python evaluate.py --data "../$DATA" --model "$RUN/model.pt"
echo "  log: $LOG"
