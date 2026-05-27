# services/inference — agent notes

**What it is.** Live tornado-risk inference. Every 5 minutes, fetches the
most recent KFWS N0B (reflectivity) + N0S (storm-relative velocity) PNGs
from the IEM RIDGE archive, runs them through the canonical CNN at
`/model/model.pt`, and atomically rewrites
`/status/inference_status.json`. The score is *never* an alert on its
own — the alerting service gates email on an active NWS tornado warning
and uses this score only as an annotation.

**The single load-bearing correctness property.** The transform from raw
PNG to model input *must* be identical to the training transform.
`inference_service.py` enforces this by importing `decode_crop`,
`load_wld`, `REFL`, `VEL`, and `PREPROCESS_VERSION` from
`ml/preprocess.py` — no reimplementation. The regression test
`test_tensor_equiv.py` (in this dir) checks bit-equivalence against a
saved training tensor before each deploy.

**Startup chain (refuses to start on any mismatch).**
1. `MODEL_PATH` exists.
2. `MANIFEST_PATH` (`/model/manifest.json`) exists and contains
   `model_sha256`, `preprocess_version`, `threshold_recommended`.
3. `manifest.preprocess_version == ml/preprocess.PREPROCESS_VERSION`.
4. Hash of `MODEL_PATH` matches `MODEL_FINGERPRINT` env (if set) or
   `manifest.model_sha256` (env wins).
5. `model.pt` is loaded with `weights_only=True` (it's a pure
   `state_dict`, not the resume `last.pt`).

**Status JSON.** See `inference_service.py` near `class State` for the
exact shape. Key fields: `last_score`, `last_score_time`,
`last_scan_time`, `scan_delta_seconds` (N0B↔N0S Δ), `model_sha256`,
`threshold`. `errors` is a ring buffer of the last 5 issues.

**Things to be careful about.**
- `weights_only=True` is enforced. If a future model retrain saves
  anything other than a pure `state_dict`, the loader will refuse it —
  which is the desired loud-failure.
- If IEM has no N0S within `MAX_PAIR_DELTA_SECONDS` of the latest N0B,
  the cycle skips scoring and marks `status=stale`. Do NOT fall back
  to an out-of-window velocity.
- The cycle deadline is 60s; if exceeded, the error is recorded and
  the status reflects the partial state. The poll loop keeps running.
- Memory: `gc.collect()` runs after each cycle to keep CPU torch
  bounded under the 1g compose limit.

**CLI.**
- `python inference_service.py` — service loop.
- `python inference_service.py --once` — run one cycle synchronously,
  print the resulting status JSON to stdout, exit 0 on success or
  1 on any non-`running` final state. Used by smoke tests and by
  alerting's `--test-email`.

**Files.**
- `inference_service.py` — main module.
- `Dockerfile`, `requirements.txt` — CPU torch from the PyTorch CPU index.
- `test_tensor_equiv.py` — load-bearing regression check.
- `README.md` — operator-facing summary.
