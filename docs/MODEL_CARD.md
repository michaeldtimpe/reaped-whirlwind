# Model Card — tornado-risk CNN (reaped-whirlwind, Part B)

**Status: SUPERSEDED (2026-09-10).** The Tornado Warning annotation is now the NOAA MRMS 0-2 km
azimuthal-shear readout from the `rotation` service — an operational NOAA product, not a learned
model — per `docs/MRMS_MIGRATION.md`. The inference service lives under the `cnn` compose profile
(not started by default) and `models/v1` stays in git for research. Nothing below is deployed.

**Prior status: INVALIDATED 2026-09-06 — annotation withdrawn from live alerts; retrain required.**
`models/v1/` is still what the inference service loads (so scores keep being logged for the
before/after comparison), but `MODEL_ANNOTATION=off` on kappa means no email or SMS carries
its readout. Everything below the next section describes the model as it was evaluated and is
retained as the record of what went wrong.

## Invalidation (2026-09-06)

**What was found.** The first end-to-end smoke test of the *deployed* path
(`services/inference/replay_smoke.py`: real KFWS N0B/N0S archive scans → the service's own
`build_tensor` → `models/v1`) scored confirmed tornadoes near the radar **lower** than quiet sky:

| case (SPC EF1+, distance from KFWS) | score at touchdown | −30 min |
|---|---|---|
| 2022-04-05 03:41Z EF2, 15 km | 0.168 | 0.210 |
| 2022-12-13 14:14Z EF1, 27 km | 0.280 | 0.314 |
| 2023-03-16 21:47Z EF1, 36 km | 0.296 | 0.325 |
| 2025-03-04 11:24Z EF1, 42 km | 0.108 | 0.130 |
| quiet 2023-08-15 21:00Z | 0.463 | |
| quiet 2024-01-20 06:00Z (near-empty scan) | **0.719** | |
| all-zero tensor | 0.651 | |

The same four tornado days scored 0.59-0.74 when sampled **six hours before** touchdown.

**Root cause: a time-zone bug in data collection.** SPC report times are CST (`tz=3` on every
row; SPC never observes DST). `data-tools/collect.py` parsed them as naive datetimes and used
them directly against the IEM archive, whose filenames are UTC. Every tornado, hail and wind
scan was therefore taken **6 h before the event** — typically pre-convective or clear sky. The
`warning_no_tornado` negatives come from IEM watchwarn, whose `ISSUED` field is already UTC, so
they were sampled correctly and show real storms.

**Why the eval looked fine.** The split was leakage-safe, but the *labels* carried the artifact:
"sparse/empty scan ⇒ tornado, storm ⇒ not tornado" is exactly what separates shifted positives
from correctly-timed warning negatives. That explains the whole results table — 5 % FP on
`warning_no_tornado` (real storms, easy to reject), ~45-50 % FP on hail/wind (same shift as the
positives, so a coin flip), PR-AUC 0.485 vs 0.33 base rate (the leak, not skill). None of the
three baselines could exploit the artifact the way a CNN could, so "beats the baselines" was
not evidence either.

**Operational impact.** None to the primary path: the NWS relay never depended on the model.
The annotation would have said "NOT ELEV" on genuine tornado warnings and "ELEVATED" on
quiet nights — a falsely reassuring signal, which is why it is withdrawn rather than left as
"experimental". No tornado warning covered the point while it was live (retrospective §2).

**Fix and path back.**
1. `collect.py` now shifts SPC times to UTC (`spc_local_to_utc`); `run_collection.sh` refuses
   to resume into a pre-fix tree and defaults to `data/full-v2`.
2. Re-collect (hours, network-bound), re-preprocess, retrain, re-evaluate as before.
3. **New gate:** `replay_smoke.py` must PASS (every tornado-time scan above every quiet
   control, ≥1 above threshold, no control above threshold) before `MODEL_ANNOTATION=on`.
   The old eval alone was not enough to catch this; the replay is now part of the gate.
4. The retrospective's score log (`inference-logs/scores-*.jsonl`) has been recording since
   2026-09-05 and gives a before/after series for the swap.

### v2 attempt (2026-09-06/07, m5) — GATE: FAIL, annotation stays withdrawn

Re-collected with the fixed collector (`data/full-v2`: 14,5xx scans, 2,910 events, same caps),
trained 30 epochs on MPS (~1 min), best val PR-AUC 0.566 at epoch 24 while train loss fell
0.77 → 0.33 (overfitting from ~epoch 10; `--patience 0`). Held-out test, base rate 0.367:

| model | PR-AUC | ROC-AUC |
|---|---|---|
| CNN v2 | 0.543 | 0.715 |
| refl mean | 0.536 | 0.682 |
| vel shear | 0.410 | 0.554 |

FP @ 0.5: hail 5 %, wind 13 %, `warning_no_torn` 14 %. Precision 0.575 at recall 0.3 needs
threshold **0.43**; at 0.8 recall is ~0.

Replay (same six cases): tornado-time scores 0.05-0.47, ±5 min swings of 0.3, Dec-13 outbreak
0.47-0.78, quiet August afternoon **0.000** (was 0.463 — the inversion is gone), but the
near-empty January night scan still **0.62**. Verdict: FAIL, 0/4 positives above every control.

Reading: with correct timing the CNN is no better than mean reflectivity — the leak was the
entire "skill" of v1. The empty-scan score is undefined behaviour: no training class contains
quiet sky. Nothing here is deployable; `MODEL_ANNOTATION` stays off.

---

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

## Post-deployment evaluation

See `docs/RETROSPECTIVE.md` for how the model and the NWS-gated alerting design have held up
against live operation on kappa.

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
