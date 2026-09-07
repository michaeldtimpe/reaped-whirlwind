#!/usr/bin/env bash
#
# reaped-whirlwind — train + evaluate + REPLAY GATE, in one go.
#
#   ./scripts/train_and_gate.sh data/full-v2            # train on a collection
#   ./scripts/train_and_gate.sh data/full-v2 --epochs 50
#   SKIP_TRAIN=1 ./scripts/train_and_gate.sh          # re-gate ml/runs/LATEST only
#
# Runs ml/run_training.sh (train → held-out eval), then writes the run's
# manifest.json (sha256 + preprocess_version + threshold) so the inference
# service's own loader accepts it, then runs services/inference/replay_smoke.py
# against real KFWS archive scans of confirmed tornadoes vs quiet sky.
#
# The replay is THE gate (docs/MODEL_CARD.md "Invalidation"): the offline eval
# alone passed a model that scored tornadoes below clear sky. Promote a run to
# models/v1 only if this script ends with "GATE: PASS".
#
# Outputs, all under ml/<run>/: model.pt, run.json, manifest.json, replay.json.
# Log: ml/runs/gate_<ts>.log (the training log is separate, see run_training.sh).
set -euo pipefail
cd "$(dirname "$0")/.."             # repo root
DATA="${1:-data/full-v2}"; shift || true
TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p ml/runs
LOG="ml/runs/gate_${TS}.log"
exec > >(tee -a "$LOG") 2>&1
echo "[log] $LOG"

if [ "${SKIP_TRAIN:-0}" = "1" ]; then
  echo "[1/3] SKIP_TRAIN=1 — gating the existing run in ml/runs/LATEST"
else
  echo "[1/3] train + eval on $DATA"
  ./ml/run_training.sh "$DATA" "$@"
fi

RUN="ml/$(cat ml/runs/LATEST)"      # LATEST is relative to ml/
echo "[2/3] manifest for $RUN"
# The replay imports the inference service module (flask) and ml/preprocess
# (pillow); run_training's venv only has torch + numpy.
.venv-train/bin/pip install -q flask pillow requests
.venv-train/bin/python - "$RUN" "$DATA" <<'EOF'
import hashlib, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
run, data = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, "ml")
from preprocess import PREPROCESS_VERSION
sha = hashlib.sha256((run / "model.pt").read_bytes()).hexdigest()
try:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
except Exception:
    commit = None
run_json = json.loads((run / "run.json").read_text()) if (run / "run.json").exists() else {}
manifest = {
    "model_sha256": sha,
    "training_run": run.name,
    "git_commit": commit,
    "preprocess_version": PREPROCESS_VERSION,
    "threshold_recommended": 0.8,
    "data_dir": data,
    "training_summary": run_json,
    "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "replay_gate": "pending — see replay.json",
}
(run / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"  sha256 {sha[:12]}…  preprocess {PREPROCESS_VERSION}")
EOF

echo "[3/3] replay gate (real KFWS tornado scans vs quiet sky)"
set +e
MODEL_PATH="$RUN/model.pt" MANIFEST_PATH="$RUN/manifest.json" \
  .venv-train/bin/python services/inference/replay_smoke.py --json "$RUN/replay.json"
rc=$?
set -e
case $rc in
  0) verdict="PASS" ;;
  1) verdict="FAIL" ;;
  *) verdict="NO VERDICT (archive unreachable, rc=$rc)" ;;
esac
.venv-train/bin/python - "$RUN" "$verdict" <<'EOF'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]) / "manifest.json"
m = json.loads(p.read_text()); m["replay_gate"] = sys.argv[2]
p.write_text(json.dumps(m, indent=2) + "\n")
EOF
echo
echo "GATE: $verdict   ($RUN)"
if [ "$verdict" = "PASS" ]; then
  echo "Promote with: cp $RUN/model.pt models/v1/model.pt && cp $RUN/manifest.json models/v1/manifest.json"
  echo "  (then update models/v1/{run,eval}.json + MANIFEST.md, set MODEL_ANNOTATION=on, deploy)"
fi
exit $rc
