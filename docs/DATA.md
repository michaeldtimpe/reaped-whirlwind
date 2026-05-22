# Data

Where the training data comes from and what it looks like. Collection code +
the era/product gotchas are in [`data-tools/README.md`](../data-tools/README.md);
this is the summary.

## Not in the repo
All datasets are **gitignored** and live outside git:
- on **kappa**: live captures + processed JSON under `/volume1/docker/...`, and the ~389 GB
  historical tornado-positives archive (`weather-screenshots/postive-tornado-images-11-mar-2026/`,
  CONUS-mosaic reflectivity — superseded by the per-station re-collection below).
- on the **analysis machine**: generated under `data/full/` by `data-tools/run_collection.sh`.

## Sources
| Class | Source |
|---|---|
| tornado (positive) | SPC `1950-2025_actual_tornadoes.csv` (EF1+) |
| hail (negative) | SPC per-year `{year}_hail.csv` |
| wind (negative) | SPC per-year `{year}_wind.csv` |
| warning-no-tornado (hard negative) | IEM watchwarn shapefile (TO.W), excluding any within 30 km / 45 min of a confirmed tornado |
| radar imagery | IEM RIDGE per-station **N0B** (reflectivity) + **N0S** (storm-relative velocity), 2020–2025 |
| station coords | IEM `NEXRAD.geojson` (144 CONUS WSR-88D) |

## Pipeline
`collect.py` lists each product's archive directory and pairs the nearest-available N0B/N0S frames
to each event time (products have different cadences), within 150 km of a station. `preprocess.py`
decodes the paletted PNGs **by index** (0 = missing; N0S 15 = range-folded), georeferences via the
`.wld`, crops a ~120 km box around the event, and resizes to a **2 × 128 × 128** float32 tensor
(channels: reflectivity, velocity), normalized palette index in [0, 1].

## Manifest
`data/full/tensors_manifest.csv`: one row per scan — `tensor` path, `label` (1 tornado / 0),
`subtype` (tornado / hail / wind / warning_no_torn), `event_id`, `date`, `station`, `dist_km`,
`has_velocity`. `subtype` lets `evaluate.py` break down false positives per negative type.

## Splits
Leakage-safe by **(date, station)** — every scan of one storm-day-radar stays in a single split, so
the same mesocyclone can't appear in both train and test (`ml/dataset.py`).

## Caveats
- Values are normalized palette **index**, not calibrated dBZ/knots — consistent across classes
  (fine for an offline classifier); calibrate in Part C if the model passes.
- N0S velocity is coarse (16 levels, ~7-min cadence).
- Data is collected **sequence-ready** (per-event ordered frames) so a temporal model needs no
  re-collection.
