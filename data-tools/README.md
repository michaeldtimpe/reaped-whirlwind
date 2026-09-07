# data-tools — Part B data collection

Builds the **domain-matched** training set for the tornado-risk experiment by pulling
per-station IEM RIDGE radar for confirmed events. Designed to run on a spare machine
(it's network-bound, light on CPU).

## What it collects
- **Positives:** SPC confirmed tornadoes (EF1+).
- **Negatives (all three):**
  - SPC **hail** reports (severe, non-tornadic)
  - SPC **wind** reports (severe, non-tornadic)
  - **tornado-WARNINGS with no confirmed tornado** — the hardest/most valuable negatives
    (NWS TO.W warnings, excluding any within 30 km / 45 min of a confirmed tornado)
  - **quiet sky** — random station/time with no SPC report (any type, incl. EF0) and no TO.W
    within 200 km / ±6 h. Added 2026-09-07: without it the model's output on an empty scan is
    undefined (v1 and the v2 retrain both scored a near-empty night ≥0.6).
- Products: per-station **N0B** (super-res reflectivity) + **N0S** (storm-relative velocity) —
  the consistent 2020-2025 / live-era pair (`~1 km/px`, station-centered).
- Stations: all CONUS WSR-88D (fetched live from IEM; embedded fallback).
- Each scan → georeferenced 120 km crop → `2×128×128` float32 tensor (refl, vel).

## Run it (e.g. on the M4 Pro)
```bash
git clone git@github.com:michaeldtimpe/reaped-whirlwind.git
cd reaped-whirlwind/data-tools

# quick smoke test first (~10 min, 1 recent year):
./run_collection.sh --sample 30

# then the full run (a few hours, ~few GB, throttled to be polite to IEM/SPC):
./run_collection.sh
```
Needs Python 3.10+ and internet. The script creates `.venv` and installs
`requests numpy pillow pyshp`. Defaults (since 2026-09-07): years 2020-2025, ALL EF1+ tornado
events + 2000 each of hail/wind/warning-no-tornado + 1500 quiet, 5 scans/event, 4 concurrent
PNG fetches per event. Tune with `--cap-pos` (0 = all), `--cap-neg-each`, `--cap-quiet`,
`--max-scans`, `--years`. Subsampling is a seeded shuffle prefix, so a larger cap is a
superset of a smaller one and resuming after raising a cap only fetches the new events.
**SPC times are CST and are shifted to UTC** (`spc_local_to_utc`) — see docs/DATA.md.

## Output
```
data/full/raw/...                 # downloaded PNGs (can delete after preprocess)
data/full/wld/<station>.wld       # georeference per station
data/full/manifest.csv            # every scan: event, class, subtype, station, paths
data/full/tensors/*.npy           # 2x128x128 float32 model inputs
data/full/tensors_manifest.csv    # tensor path + label/subtype/event/date/station
```

## After it finishes — train + evaluate
Copy `data/full/` back to the training machine, or train in place (the M4 Pro is great for it):
```bash
./ml/run_training.sh data/full          # or: ./ml/run_training.sh data/full --epochs 50
```
This builds a torch venv (auto-picks Python 3.11-3.13 — torch has no 3.14 wheels), trains the
compact CNN with a **leakage-safe split (by date+station)**, then runs `evaluate.py` = the
**go/no-go**: PR-AUC + ROC-AUC vs baselines (refl intensity, velocity shear, majority),
operational precision-at-recall, and per-negative-subtype false-positive rates.

## Notes
- `tensors_manifest.csv` `subtype` lets evaluation break down performance vs each negative
  type (a model that beats hail/wind but not warning-no-tornado is the realistic worry).
- Velocity (N0S) is a coarse 16-level storm-relative product — usable for gross rotation,
  not calibrated knots. Values are normalized palette index (consistent across classes).
