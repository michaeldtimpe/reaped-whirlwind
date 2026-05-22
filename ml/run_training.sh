#!/usr/bin/env bash
#
# reaped-whirlwind — train + evaluate the tornado-risk CNN on collected tensors.
#   ./run_training.sh                 # uses data/full, 30 epochs
#   ./run_training.sh data/full --epochs 50
#
# torch has no Python 3.14 wheels yet, so we pick a 3.11-3.13 interpreter for a
# dedicated .venv-train (separate from the collection venv).
set -euo pipefail
cd "$(dirname "$0")/.."             # repo root
DATA="${1:-data/full}"; shift || true

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
../.venv-train/bin/python train.py --data "../$DATA" "$@"
RUN=$(cat runs/LATEST)
echo "evaluating $RUN"
../.venv-train/bin/python evaluate.py --data "../$DATA" --model "$RUN/model.pt"
