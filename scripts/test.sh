#!/usr/bin/env bash
#
# reaped-whirlwind — run the offline test suite.
#   ./scripts/test.sh                 # whole suite
#   ./scripts/test.sh tests/test_nws.py -k url
#
# Every test runs offline: no NWS/IEM calls, no SMTP, no docker socket.
#
# torch has no Python 3.14 wheels yet, so we pick a 3.11-3.13 interpreter for
# .venv — same detection as ml/run_training.sh (which uses its own .venv-train).
set -euo pipefail
cd "$(dirname "$0")/.."             # repo root

PY=""
for c in python3.12 python3.11 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print(sys.version_info[:2]>=(3,11) and sys.version_info[:2]<=(3,13))')
    [ "$v" = "True" ] && PY="$c" && break
  fi
done
[ -z "$PY" ] && { echo "ERROR: need Python 3.11-3.13 for torch (found none). Install one (e.g. brew install python@3.12)."; exit 1; }
echo "using $PY ($($PY --version))"

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-dev.txt

.venv/bin/python -m pytest -q "$@"
