# Model Card — tornado-risk CNN (reaped-whirlwind, Part B)

**Status: not yet trained on the full dataset.** This card is the go/no-go artifact; the Results
section is filled in after the first full run on the analysis machine.

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
_TBD — paste the `ml/runs/<ts>/eval.json` summary here after the first full run._
- base rate (test positives): …
- CNN PR-AUC: … (refl … / vel-shear … / majority …)
- precision @ recall 0.5: …
- FP rate by subtype — hail … / wind … / warning_no_torn …
- **Decision: GO / NO-GO** — …
