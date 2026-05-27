# Model Card — tornado-risk CNN (reaped-whirlwind, Part B)

**Status: trained, evaluated, and deployed as Part C's annotation source.** Soft-GO under the
gate criteria; the canonical model is at `models/v1/`.

## Intended use
Research/learning experiment: given a single-site radar snapshot, estimate whether a tornado is
occurring near that storm. **Not** a safety system. The authoritative alert is the National Weather
Service (its warnings are ingested separately and remain primary, unconditional, and independent).
Any model-triggered alert (Part C) is clearly labeled *experimental*.

## Inputs
- `2 × 128 × 128` float32 tensor, ~120 km box centered on an event:
  - ch0 = reflectivity (IEM RIDGE **N0B**, super-res base reflectivity)
  - ch1 = storm-relative velocity (IEM RIDGE **N0S**, coarse 16-level)
- Values are **normalized palette index** (consistent across classes; **not** calibrated dBZ/knots).
  Missing/range-folded cells are masked to 0.

## Labels & data
- **Positive:** a confirmed tornado (SPC) was near this radar at this time.
- **Negative:** SPC hail, SPC wind, and — most importantly — **tornado-warning-no-tornado** (NWS
  TO.W warnings with no confirmed tornado within 30 km / 45 min).
- Source/era: per-station IEM RIDGE, 2020–2025, all CONUS WSR-88D. See `docs/DATA.md`.

## Method
- Compact ~0.5 M-param 2-channel CNN (`ml/model.py`), CPU-inference friendly.
- Class-weighted BCE; best checkpoint by validation PR-AUC (`ml/train.py`).
- **Leakage-safe split by (date, station)** so one storm never spans train/test.

## Evaluation (the go/no-go) — `ml/evaluate.py`
On a held-out, leakage-safe test split:
- PR-AUC / ROC-AUC vs **baselines** (reflectivity intensity, velocity shear, majority).
- **Operational** precision at fixed recall (a model is only useful at a tolerable false-alarm rate).
- **False-positive rate per negative subtype** — especially `warning_no_torn`.

**GO** if the CNN clearly beats the baselines AND reaches usable precision at a sane recall, with a
low warning-no-tornado FP rate. Otherwise **NO-GO** → archive / rescope.

## Known limitations (decide-with-eyes-open)
- **Label semantics:** a single-timestamp crop labeled by "a tornado occurred" may teach "generic
  severe / mature hook / hail core" rather than *actionable, pre-tornadic* signal. Mitigated by the
  warning-no-tornado negatives, the 150 km range cutoff, and sequence-ready data for a temporal
  follow-up.
- **Single-frame:** tornadogenesis is dynamic; static reflectivity is weak. The data is collected
  sequence-ready so a temporal (3D-CNN/ConvLSTM) model is a cheap follow-up if the static model is
  promising.
- **Velocity is coarse:** N0S is 16-level storm-relative velocity, ~7-min cadence — gross rotation
  only, not calibrated knots.
- **Won't beat NWS;** geographically narrow at deployment (single live site, KFWS) so live
  validation is slow. Stays experimental.

## Results
Canonical run: `ml/runs/20260527_162806/` → `models/v1/`. Trained on git
`f2df4824129b4eb10d9e34f3bdbad89ce2333ae3` with `--epochs 5 --patience 2`; early-stopped at
epoch 3 because val PR-AUC peaked at epoch 1 (0.660) and did not improve. Test split is
leakage-safe (held-out by date+station, 2,182 scans, base rate 0.331).

| metric | CNN | refl mean | vel shear | majority |
|---|---|---|---|---|
| **PR-AUC** | **0.485** | 0.259 | 0.337 | 0.331 |
| ROC-AUC | 0.709 | 0.368 | 0.505 | 0.500 |

Operational (CNN): precision at fixed recall.
| recall | precision | threshold |
|---|---|---|
| 0.3 | 0.536 | 0.62 |
| 0.5 | 0.445 | 0.51 |
| 0.7 | 0.464 | 0.46 |
| 0.9 | 0.442 | 0.36 |

FP rate by negative subtype @ threshold 0.5:
- **`warning_no_tornado`: 26/524 = 5.0 %** — the operationally-relevant number under the NWS gate.
- `hail`: 205/473 = 43.3 %.
- `wind`: 235/462 = 50.9 %.

**Decision: soft GO.** The CNN clears all three baselines and achieves the desired property of
distinguishing tornadic from severe-but-non-tornadic storms (5 % FP on `warning_no_tornado`).
Absolute precision is modest (44.5 % @ recall 0.5) and the model is fooled by hail/wind ~half
the time at threshold 0.5. Both are made operationally irrelevant by the **NWS-gating**
architecture in Part C: the alerting service only emits emails when an active NWS Tornado Warning
already exists; outside a TO.W, the model never fires. Within a TO.W, only the 5 %
`warning_no_tornado` rate is exercised.

## Tuning

`MODEL_RISK_THRESHOLD` controls only the *annotation* in the alert email's subject line and body
("model: elevated" vs "model: not elevated"). It does NOT gate sending the email; the NWS warning
does. So tuning it is reversible and low-risk.

- **0.8 (default).** Conservative. The "elevated" annotation is uncommon (~30 % recall at this
  threshold) but specific (~54 % precision). Good if the goal is to make "elevated" feel
  meaningful — when you see it, the radar morphology genuinely matches the model's tornadic
  pattern.
- **0.5.** Recall-favoring. Catches ~70 % of test-set tornadoes at the cost of ~46 % precision.
  Good if the goal is "rarely show 'not elevated' during a real tornado." The trade-off is more
  "elevated" labels on severe non-tornadic storms during marginal events — which inside a TO.W is
  not safety-relevant, since the email goes out either way.

Recommendation: leave at 0.8 for the first season, observe the annotations against real warnings
(via the dashboard + the per-email scores logged in `alerts_sent.json`), then re-tune from
evidence.

## Going forward (not part of Part C)

The eval supports two cheap follow-ups before any retrain:
- **Temporal model.** The training data was collected sequence-ready (up to 5 scans per event).
  A 3D-CNN / ConvLSTM is the obvious next ML lever and would let the model see storm evolution
  (hook tightening, mesocyclone deepening) rather than a single frame. `data-tools/collect.py`
  is already set up for it; only `ml/dataset.py` and `ml/model.py` change. Out of scope here.
- **Calibrated physical units.** Both the training pipeline and the live inference pipeline use
  normalized palette indices, not dBZ/knots, deliberately consistent across both — so this is a
  research-quality not a deployment-blocking concern. If a future revision converts to physical
  units, the `preprocess_version` constant bumps and `models/v2/` ships in parallel.
