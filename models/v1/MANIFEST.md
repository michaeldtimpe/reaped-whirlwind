# models/v1 — canonical tornado-risk CNN

This directory is the **canonical model the production inference service
deploys**. Everything here — `manifest.json`, `run.json`, `eval.json`,
and the small `model.pt` weights file (~400 KB) — is committed so a
model swap is reviewable in `git` and the deploy archive
(`git archive HEAD | ssh kappa`) actually ships the binary. The
gitignore for `*.pt` is negated specifically for `models/**/model.pt`.

**Operational contract** (read at inference startup, see
`services/inference/inference_service.py`):

- `manifest.json:model_sha256` must match the on-disk `model.pt`. The
  startup chain refuses to launch on mismatch. The `MODEL_FINGERPRINT`
  environment variable, if set, overrides this manifest value.
- `manifest.json:preprocess_version` must match
  `ml/preprocess.PREPROCESS_VERSION`. Mismatch ⇒ refuse to start
  (catches a model trained against an older `decode_crop`).
- `manifest.json:threshold_recommended` is the default for
  `MODEL_RISK_THRESHOLD`. Annotation-only — see
  `docs/MODEL_CARD.md` "Tuning" for the recall-favoring 0.5 alternative.

To replace this model: train a new run, copy `model.pt`, `run.json`,
`eval.json` into `models/v2/`, write a new `manifest.json`, update the
`docker-compose.yml` bind-mount, redeploy. Both versions can coexist on
disk; the compose file picks the active one.
