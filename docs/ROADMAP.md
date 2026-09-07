# Roadmap — making the tornado annotation rock solid

**Written 2026-09-07**, after `models/v1` was found to have learned a 6-hour time-zone artifact
and the first honest retrain (v2) came out no better than mean reflectivity. This is the plan to
get from "withdrawn" to an annotation that is trustworthy enough to print on a Tornado Warning.

The NWS relay is the product. The annotation is a second opinion. Nothing below changes that
ordering, and every phase ends with an automated gate that decides whether the next one starts.

## Success criteria (decide these first, then build to them)

The annotation is "rock solid" when all of the following hold on data the model has never seen:

| # | Criterion | Measured by |
|---|---|---|
| S1 | **Never elevated on quiet sky.** 0 of N quiet controls (N ≥ 30, all seasons, day + night) at or above threshold. | replay gate |
| S2 | **Elevated on most real tornadoes at KFWS.** ≥ 60 % of tornado-time scans (every EF1+ within 100 km, 2021-2025) at or above threshold. | replay gate |
| S3 | **Rarely elevated on severe-but-not-tornadic storms.** ≤ 20 % of SV.W-day and hail-day control scans at or above threshold. | replay gate + held-out eval by subtype |
| S4 | **Beats the honest baselines by a margin.** PR-AUC ≥ best hand-crafted baseline + 0.10 on a **held-out year** (train 2020-2024, test 2025). | `ml/evaluate.py` |
| S5 | **Stable.** Median absolute score change between consecutive scans ≤ 0.10; no ±0.3 swings inside a storm. | replay gate |
| S6 | **Calibrated.** On the validation split, "0.8" means ≥ 70 % of such scans are tornadic. | calibration step in training |
| S7 | **Live-verified.** One full severe season in shadow mode with the score log analysed against NWS warnings before the annotation is switched on. | retrospective script, monthly |

Until S1-S6 pass, `MODEL_ANNOTATION` stays off. S7 gates the flip.

## Phase 0 — running now (2026-09-07)

Extended collection into `data/full-v2`: all 3,982 EF1+ tornadoes, 2,000 each hail / wind /
warning-no-tornado, **1,500 quiet-sky** negatives; 4-way parallel fetch; chained
`train_and_gate.sh --epochs 40 --patience 6`. Expected: the empty-scan behaviour is fixed (S1),
PR-AUC moves little (the model still has no shape prior). This run is diagnostic, not a candidate.

## Phase 1 — data integrity (1-2 days; must finish before any model is trusted)

The v1 bug was invisible to every check we had. Add the checks that would have caught it.

1. **Label-sanity report in the collector.** For every committed event, measure reflectivity
   inside a 20 km box around the event location on the chosen scan. Report the fraction of
   tornado / hail / wind events whose scans show echo there (expect > 90 %); print the worst
   offenders. A 6-hour shift would have shown ~30 %. Fail the run below 80 %.
2. **Independent time cross-check.** For 50 random tornado events, compare our chosen scan time
   with the NWS TO.W polygon that contains the event (watchwarn `ISSUED`/`EXPIRED`, already UTC).
   The scan must fall inside the warning window. This ties SPC time to an independent UTC source.
3. **Held-out year split.** Add `--test-years 2025` to `ml/dataset.py`: train 2020-2024,
   validate on a (date, station) slice of those, test on all of 2025. Temporal holdout is the
   only split that mimics deployment.
4. **KFWS holdout view.** Report eval metrics on the KFWS-station subset separately (n is small,
   but it is the station we deploy on and beam geometry differs per site).
5. **Severe-thunderstorm negatives.** Add SV.W-with-no-tornado events from watchwarn
   (`phenomena=SV`). At KFWS the frequent live case is an SVR day, not a TO.W day; the
   retrospective's nine SV.W events are exactly the scans we most need the model to reject.
6. **Manifest provenance.** Write `collection.json` next to the manifest: collector git commit,
   caps, seed, class counts, label-sanity numbers. `train.py` copies it into `run.json`.

Gate to Phase 2: label-sanity > 90 % for all event classes; cross-check passes 50/50.

## Phase 2 — the right model (1 week of evenings)

**2026-09-07: `docs/MRMS_MIGRATION.md` supersedes Phase 2.1's hand-rolled azimuthal-shear
baseline below.** NOAA already publishes the quantity 2.1 was going to approximate
(`MergedAzShear_0-2kmAGL`, multi-radar, 2-min cadence, masked below 20 dBZ) — use that instead of
reimplementing it from the velocity PNGs. See that doc for the full plan; Phase 2.2-2.4 (CNN,
calibration, sequence input) are unaffected and still apply if a CNN is revisited later.

The question is not "can a CNN learn tornadoes" but "what is the strongest thing that passes
S1-S6". Build the ladder and stop at the highest rung that passes.

1. **Hand-crafted couplet baseline (do this first).** What a forecaster looks at: azimuthal
   shear in the storm-relative velocity field. Compute, per scan, the maximum local
   inbound/outbound difference over a ~5 km window, co-located with reflectivity ≥ 35 dBZ,
   within the crop. Feed {max shear, max reflectivity, echo area, shear-refl co-location} to a
   logistic regression. This is cheap, interpretable, and has 30 years of operational precedent.
   **If the CNN cannot beat this by S4's margin, ship this instead.** It also cannot score empty
   sky high, by construction.
2. **CNN, fixed properly.** Early stopping (done), small translation augmentation only (no
   flips: a mirrored couplet is an anticyclonic mesocyclone, a different thing), dropout, and
   the velocity channel encoded as signed radial velocity about zero rather than a 0-1 palette
   index so shear is linear in the input. Report mean ± sd over 3 seeds; a model whose seed
   variance exceeds its margin over baseline has no margin.
3. **Calibration + threshold selection.** Temperature-scale on validation; then pick the
   deployed threshold from the validation precision-recall curve to hit S6, not the hard-coded
   0.8. Write both into the manifest. The threshold is a model artifact, not an env default.
4. **Sequence input (only if 1-2 plateau).** The collector already stores ordered frames per
   event. A 3-frame input (t−10, t−5, t) lets the model see rotation persist, which is what
   separates a tornadic mesocyclone from transient shear. Larger change; do it only with
   evidence that single-frame has hit its ceiling.

Gate to Phase 3: a candidate passes S4 and S6 on the held-out year.

## Phase 3 — serve it as a detector, not a snapshot (2-3 evenings)

The live service crops 120 km around the radar site; training crops are centred on the storm.
A tornado 40 km out sits off-centre in a box the model never saw it in.

1. **Sliding window.** Tile the KFWS image (out to ~150 km) with overlapping 120 km crops,
   score each, report the max and its bearing/range. The annotation becomes "elevated, 38 km
   NNE" — more useful and geometrically consistent with training.
2. **Persistence.** Report the median of the last three scans' max scores, and keep the raw
   per-tile scores in the JSONL. This is S5 at serving time.
3. **Trend in the email.** Show the last 30 minutes of scores, not one number. A rising series is
   informative; a single 0.83 is not.
4. **Shadow mode.** `MODEL_ANNOTATION=shadow`: score and log the candidate alongside the current
   model, annotate nothing. Every candidate runs one severe season in shadow (S7).

## Phase 4 — gates that cannot be talked past (in parallel with 2-3)

1. **Bigger replay set.** Extend `replay_smoke.py` from 6 to every EF1+ tornado within 100 km
   of KFWS 2021-2025 (≈ 15 events, ~75 scans with offsets), plus ≥ 30 quiet controls sampled
   across seasons and hours, plus the retrospective's SV.W-day scans as severe-not-tornadic
   controls. Compute a replay AUC and the S1/S2/S3/S5 numbers directly. Cache the PNGs in the
   repo's data dir so the gate is reproducible offline.
2. **One command, one verdict.** `train_and_gate.sh` prints a table of S1-S6 with PASS/FAIL
   per row and writes it into the run's manifest. `models/vN/` may only be created from a
   manifest whose gate block is all-PASS; add a test that asserts this for whatever is in
   `models/v1`.
3. **Unit tests for the leak class.** A test that feeds the collector a synthetic SPC row with
   `tz=3` and asserts the chosen scan is 6 h later than the naive time. A test that the quiet
   sampler never lands within the exclusion zone of a known event.
4. **Promotion checklist in `docs/DEPLOY.md`.** Gate table → `models/vN` → model card section →
   `MODEL_ANNOTATION=shadow` deploy → season → `on`. No step may be skipped, and the checklist
   is copied into the promotion commit message.

## Phase 5 — operate it (ongoing)

1. **Monthly retrospective.** Schedule `analysis/retrospective.py --fetch` monthly; it now has
   the score JSONL and decision JSONL it lacked before. Track: score distribution on quiet days
   vs SVR days vs TO.W days at KFWS; alert delay; suppression outcomes.
2. **Drift alarms.** Inference `/health` reports 7-day score p50/p95; alerting refuses to
   annotate (falls back to "unavailable") if p95 on non-warning days exceeds the threshold —
   i.e. the model has started crying wolf and nobody has looked.
3. **Retrain cadence.** Once a year after the SPC final tornado data for the previous season is
   published (spring); rerun the full pipeline and the gate; promote only on PASS.

## What this does not promise

A single-site radar snapshot cannot see every tornado (many form below the beam or between
volume scans), and SPC ground truth is itself incomplete. The ceiling for S2 is probably
70-80 %, not 100 %. The plan's job is to make the annotation *honest and stable*, so that
"elevated" is worth reading and "not elevated" is never taken as reassurance. That last point is
already handled in the email text and must stay.

## Effort summary

| Phase | Calendar | Gate |
|---|---|---|
| 0 running | today | diagnostic only |
| 1 data integrity | 1-2 days | label-sanity > 90 %, cross-check 50/50 |
| 2 model ladder | ~1 week | S4 + S6 on held-out 2025 |
| 3 detector serving | 2-3 evenings | shadow deploy on kappa |
| 4 gates | alongside 2-3 | one-command S1-S6 table |
| 5 operate | ongoing | one shadow season → `on` |

Realistic path to `MODEL_ANNOTATION=on`: candidate passing S1-S6 within a few weeks; live
switch after the next spring severe season, i.e. ~mid-2027.
