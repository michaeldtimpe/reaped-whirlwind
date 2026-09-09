# Migrating the tornado-warning annotation to MRMS azimuthal shear

**Written 2026-09-07.** Implementation plan, executed phase by phase. Supersedes
`docs/ROADMAP.md` Phase 2.1's hand-rolled azimuthal-shear baseline: NOAA already publishes the
quantity that baseline was going to approximate, at 2-min cadence, multi-radar, with the
empty-sky failure mode designed out. This plan ships that product as the annotation instead of
building a homegrown version of it.

## 0. Why

The CNN (`models/v1`, and the v2 retrain in `docs/MODEL_CARD.md` "Invalidation") is invalidated:
v2 with correct timing is statistically indistinguishable from a mean-reflectivity baseline
(PR-AUC 0.543 vs 0.536) and still scores a near-empty night scan 0.62. The annotation is
withdrawn (`MODEL_ANNOTATION=off`). NOAA's MRMS `MergedAzShear_0-2kmAGL` is the operational,
multi-radar, physically-defined quantity the CNN was implicitly trying to learn from raw N0B/N0S
PNGs: low-level rotation in s⁻¹, 2-min cadence, 0.005° grid (~500 m), and — unlike the CNN —
values are already masked where reflectivity < 20 dBZ, so it cannot score empty sky high by
construction. `docs/ROADMAP.md` Phase 2.1 already names an azimuthal-shear baseline as the
fallback ("if the CNN cannot beat this, ship this instead"); this plan ships the NOAA-computed
version of that baseline rather than reimplementing it from the velocity PNGs. It is served as a
diagnostic readout on NWS Tornado Warnings, never as the alert. NWS stays primary and
independent; nothing in the alert path changes except the annotation text.

## 1. Product facts

| Product | Cadence | Meaning |
|---|---|---|
| `MergedAzShear_0-2kmAGL` | 2 min | Instantaneous low-level (0-2 km) azimuthal shear — the low-level mesocyclone signature |
| `MergedAzShear_3-6kmAGL` | 2 min | Mid-level azimuthal shear |
| `RotationTrack30min` | 2 min | Trailing 30-min max of 0-2 km AzShear |
| `RotationTrackML30min` | 2 min | Trailing 30-min max of 3-6 km AzShear (ML = Mid-Level, **not** machine learning) |

- Unit in the GRIB2 file is 0.001 s⁻¹ — multiply by 0.001 to get s⁻¹.
  Source: https://raw.githubusercontent.com/NOAA-National-Severe-Storms-Laboratory/mrms-support/main/GRIB2_TABLES/UserTable_MRMS_v12.2.csv
- Grid: 0.005° (finer than the 0.01° reflectivity grid), CONUS 20-55N, 130-60W.
  Source: https://vlab.noaa.gov/web/wdtd/-/azimuthal-she-2
- Real-time: `https://mrms.ncep.noaa.gov/2D/MergedAzShear_0-2kmAGL/` (Apache index). Files
  `MRMS_MergedAzShear_0-2kmAGL_00.50_YYYYMMDD-HHMMSS.grib2.gz` (~0.4-1.2 MB), plus a
  `MRMS_MergedAzShear_0-2kmAGL.latest.grib2.gz` symlink. Retention observed ~26.5 h — not a
  documented SLA. Latency observed ~2-3 min after valid time.
- Archive + mirror: `s3://noaa-mrms-pds/CONUS/MergedAzShear_0-2kmAGL_00.50/YYYYMMDD/<same filename>`,
  anonymous (`--no-sign-request`); also mirrors real-time; history from 2020-10-14. Same layout
  for `RotationTrack30min`. Registry: https://registry.opendata.aws/noaa-mrms-pds/
- Decode: gzip'd GRIB2, one field per file. `pip install eccodes` now pulls `eccodeslib`
  manylinux wheels with the C library bundled (no `apt libeccodes0` needed). Use eccodes
  directly (`codes_grib_new_from_file` + `codes_get_values`) or cfgrib+xarray. Avoid `grib2io`
  (source-only distribution) and `pygrib`. No pure-Python GRIB2 decoder exists.
- **GOTCHA — Missing = 0 and No-Coverage = 0.** Unlike reflectivity's -99/-999 sentinels, a calm
  sky and a radar outage look identical: all zeros. Mitigation in §2.1.
- **Range artifact.** LLSD shear "breaks down within 5 km of a radar site." KFWS
  (32.5728, -97.3031) is inside our domain — mask a 5 km disc around KFWS. Other WSR-88Ds
  (KGRK/KDYX/KFDR/KTLX/KSRX) are all >100 km away; verify this in Phase 0 and re-check only if
  the domain radius ever grows. Note: the 5 km disc covers Burleson's centre (3.8 km from KFWS),
  so a max cell cannot be reported "near Burleson" — see the corrected §2.2 example.
- **Interpretation.** No official NSSL "tornadic" threshold exists. Case literature cites
  ~0.010 s⁻¹ in 0-2 km AzShear as strong low-level rotation, with tornadic cases ramping
  0.008 → 0.012+. This is a diagnostic, not a tornado observation.
  Sources: https://vlab.noaa.gov/web/wdtd/-/azimuthal-she-2 , https://vlab.noaa.gov/web/wdtd/-/rotation-tracks

## 2. Design

### 2.1 New service `services/rotation/`

Same skeleton as `services/inference/inference_service.py`: module-level `State` under a lock,
Flask `/health`, background poll thread, `common.oplog.setup_logging`,
`common.status.atomic_write_json`, `common.jsonlog.append_jsonl`. Port 9010. `--once` mode
prints one cycle as JSON, matching inference's CLI convention.

Cycle every `POLL_INTERVAL` (default 120 s), `CYCLE_DEADLINE_SEC` 60:

1. Fetch the NCEP directory index, regex all filenames, pick the newest valid time. If unchanged
   from the last cycle, log "no new file" and return — do not refetch or reprocess. Fallback if
   the NCEP index fetch fails: list today's S3 prefix
   (`noaa-mrms-pds/CONUS/MergedAzShear_0-2kmAGL_00.50/YYYYMMDD/`) via plain HTTPS
   `?list-type=2&prefix=` — no `boto3` dependency.
2. Download the file (gzip), decode with eccodes: values array + grid definition (Ni/Nj, first
   lat/lon, di/dj). Do **not** build a full lat/lon meshgrid every cycle — compute the index
   slice for the domain bounding box once, cache it, and slice.
3. Domain: everything within `DOMAIN_RADIUS_KM` (default 100, matches ROADMAP S2) of KFWS.
   Precompute a boolean disc mask on the sliced sub-grid once, plus the 5 km radar-site
   exclusion, at startup.
4. Multiply by 0.001. Compute:
   - `max_azshear` (s⁻¹) and its location (lat, lon, km + bearing from KFWS)
   - `cells_ge_threshold` (count)
   - `coverage_nonzero_fraction` (fraction of domain cells ≠ 0 — used only for the all-zero
     diagnostic in §2.1 below)
   - `top_cells`: up to 5 local maxima ≥ 0.5×threshold, each
     `{lat, lon, value, km_from_kfws, bearing, near}`, `near` = nearest name from a small static
     ~25-town DFW-area table in `common/places.py`, deduplicating within 10 km.
5. Optionally (env `ROTATION_TRACK=1`, default on) repeat for `RotationTrack30min` →
   `max_track30`. Second fetch, same code path.
6. Write `/status/rotation_status.json`; append `rotation-YYYYMM.jsonl` (month-rotated via
   `common.jsonlog`, same pattern as inference's `SCORE_LOG_PATH`).

**Status file contract.** Keys marked ★ intentionally keep inference's existing names so
alerting's `classify_model_state` (`services/alerting/alert_service.py:237`) needs no key
changes:

| Key | Notes |
|---|---|
| `status` ★ | `uninitialized` \| `running` \| `stale` \| `error` |
| `last_score` ★ | = `max_azshear`, s⁻¹, float, 4 dp, or `null` |
| `last_score_time` ★ | ISO Z, = product **valid time**, not fetch time |
| `threshold` ★ | s⁻¹ |
| `product`, `product_valid_time`, `fetched_at`, `source` (`ncep`\|`s3`) | |
| `max_location` | `{lat, lon, km, bearing, near}` |
| `cells_ge_threshold`, `coverage_nonzero_fraction`, `max_track30`, `top_cells` | |
| `domain_radius_km` | |
| `cycle_ms` | `{fetch, decode, compute, write}` |
| `errors` | last 5 |

`/health` adds `score_age_seconds` and downgrades to `stale` when age exceeds
`MAX_SCORE_AGE_SECONDS` (default 900 — tighter than the CNN's 1800, because this product is
2-min cadence).

**All-zero handling.** DECISION: surface `coverage_nonzero_fraction` in the status file and let
alerting word it honestly ("no rotation signal (coverage unverified)") when it is 0, rather than
have the rotation service try to disambiguate calm-sky from outage itself. Alternative: a second
fetch of `RadarQualityIndex` to prove coverage — deferred to Phase 5 if the simple version ever
misleads.

**Dockerfile.** `python:3.12-slim`, `pip install flask requests numpy eccodes` — no torch. Target
< 300 MB image, well under the 300 s kappa relay build cap (see CLAUDE.md "kappa ops"). The grid
is 14000×7000 = 98 M cells; eccodes returns float64 (784 MB), narrowed at once to float32
(392 MB), peak ~1.2 GB during decode; eccodes has no partial-field read so the grid cannot be
cropped before decode. `mem_limit 1536m` (kappa has the RAM; the CNN inference container was
1 g). Decodes must be serial — never decode two products concurrently in the service.

**Env.** `POLL_INTERVAL`, `MAX_SCORE_AGE_SECONDS`, `ROTATION_THRESHOLD` (s⁻¹, default 0.015 —
Phase 1 result), `DOMAIN_RADIUS_KM`, `KFWS_LAT`/`KFWS_LON` (from `common.nws`), `STATUS_PATH`,
`ROTATION_LOG_PATH`, `NCEP_BASE_URL`, `S3_BASE_URL`, `ROTATION_TRACK`, `PORT`.

### 2.2 Alerting changes (`services/alerting/alert_service.py`)

- New env `ANNOTATION_STATUS_PATH` (default `/status/rotation_status.json`), replacing the read
  of `INFERENCE_STATUS_PATH` (currently line 98); keep `INFERENCE_STATUS_PATH` as a deprecated
  alias for one release.
- `classify_model_state` (line 237) unchanged in logic — same 4 states (elevated / not elevated
  / unavailable / withdrawn) — but the threshold now comes from the status file's `threshold`
  key (single source of truth in the rotation service), with `MODEL_RISK_THRESHOLD` env as
  override, same as today (line 84).
- `_tornado_model_readout()` (line 359) rewritten: header "RADAR ROTATION READOUT (NOAA MRMS,
  experimental annotation)"; body lines: max 0-2 km azimuthal shear (s⁻¹) vs threshold, valid
  time + age, location (e.g. "0.0200 s⁻¹ near Crowley, 14 km SW of KFWS"), 30-min track max,
  and — new — whether the strongest cell lies inside the warning polygon. Alerting already has
  the NWS alert geometry; add a ~20-line pure-Python ray-cast point-in-polygon to `common/geo.py`
  (no shapely dependency). If the polygon is missing (some alerts use zones instead), say
  "polygon unavailable". If `coverage_nonzero_fraction == 0`, print "no rotation signal in
  domain (radar coverage unverified)".
- SMS: unchanged structure — state word only (ELEVATED / NOT ELEVATED / UNAVAILABLE), per
  retrospective #8.
- Decision JSONL: add `annotation_source: "mrms"` and `max_location_near`.
- Tests: update the readout-rendering tests in `tests/test_alert_suppression.py`; add
  `tests/test_geo.py` (point-in-polygon: concave, point-on-edge, missing geometry) and
  `tests/test_rotation.py` (decode of a small synthetic GRIB2 fixture generated in-test with
  eccodes; domain masking; the 0.001 scaling; the 5 km radar exclusion; the all-zero coverage
  flag; `top_cells` dedupe; status keys pinned, same pattern as an existing
  `test_status_json_keeps_every_documented_key`-style test).

### 2.3 Compose / deploy

- Add a `rotation` service to `docker-compose.yml` (build `./services/rotation`, port 9010;
  mounts: code `:ro`, `common` `:ro` at `/srv/reaped/common`,
  `/volume1/docker/service-status:/status:rw`, `rotation-logs:/logs:rw`; `mem_limit 1536m`;
  `restart: unless-stopped`; healthcheck `curl /health`).
- Alerting: add `ANNOTATION_STATUS_PATH=/status/rotation_status.json`.
- Inference (CNN): DECISION — move it under `profiles: ["cnn"]` so it is not started by default
  but is one `--profile cnn up` away for research. Alternative: delete the service block and
  keep the code. Either way `models/v1` stays in git and `docs/MODEL_CARD.md` gets a "Superseded"
  note.
- Dashboard: register `rotation-service` in the `SERVICES` dict
  (`services/dashboard/dashboard_server.py:31`, alongside the existing `inference-service` entry
  at line 50). Note from scouting: the frontend today renders cards only for
  screenshot/processor/weather, not inference/alerting — adding a rotation card is optional
  Phase 4 polish, not a blocker.
- Deploy per `docs/DEPLOY.md` (git archive over ssh, throwaway root `docker:cli` for
  `up -d --build`). The rotation image is small enough to build within the 300 s relay cap, but
  still use the nohup+poll pattern out of habit (CLAUDE.md "Relay timeout").
- A new host bind-mount directory (e.g. `/volume1/docker/rotation-logs`) is not auto-created by
  Synology on first `up` — `mkdir -p` it as `magehands` beforehand, or `docker start` fails with
  "Bind mount failed"; see `docs/DEPLOY.md`.

## 3. Validation: the gate (replaces `replay_smoke.py` for the annotation)

`services/rotation/replay.py`: for each case, pull the `MergedAzShear_0-2kmAGL` file nearest the
case time (and a ±10 min series for S5) from S3, run the **same** `compute_readout()` function
the service uses (import it — no reimplementation), print a table and a PASS/FAIL.

**Cases** (reusing `replay_smoke.py`'s list):

| Case | Type | Note |
|---|---|---|
| Crowley/Burleson 2022-04-05T03:41Z | EF2 | positive |
| Arlington 2020-11-25T02:51Z | EF2 | positive |
| Fort Worth 2022-12-13T14:14Z | EF1 | positive |
| Irving 2023-03-16T21:47Z | EF1 | positive |
| Dallas 2025-03-04T11:24Z | EF1 | positive |
| Cresson 2020-01-10 | EF1 | **dropped** — predates the S3 archive (2020-10-14) |
| 2023-08-15T21:00Z, 2024-01-20T06:00Z, 2024-04-10T20:00Z, 2022-10-05T15:00Z | — | quiet controls |

Then extend per `docs/ROADMAP.md` Phase 4.1: every EF1+ within 100 km of KFWS 2021-2025 from the
SPC tornado CSV (`data-tools/collect.py` already parses it — remember its CST→UTC fix), ≥30
quiet controls, and ≥15 severe-thunderstorm-warning-day-without-tornado controls (NWS warning
archive / IEM VTEC).

| Criterion | Target | Notes |
|---|---|---|
| S1 | 0/30 controls ≥ threshold | |
| S2 | ≥60 % of tornado-time scans ≥ threshold | |
| S3 | ≤20 % of SV.W controls ≥ threshold | |
| S4 | n/a | this readout **is** the baseline; note explicitly, not a gap |
| S5 | median \|Δ| between consecutive 2-min scans ≤ 0.10 of threshold | in s⁻¹: median \|Δmax\| ≤ 0.002 s⁻¹ within a storm |
| S6 | honest hit-rate table | for thresholds 0.006 / 0.008 / 0.010 / 0.012, fraction of ≥-threshold scans with a tornado within ±15 min / 25 km; choose the threshold from this table; document it in the status file and the model card |
| S7 | shortened shadow, see below | |

**S7 — DECISION.** Recommend a shortened shadow: run the rotation service on kappa writing
status + JSONL while alerting still reads the withdrawn CNN status, for at least 2 weeks **and**
until ≥1 Severe Thunderstorm Warning day has been observed in the log; then cut over. Rationale:
this is an operational NOAA product, not a learned model — the failure mode S7 guards against (a
model that learned an artifact) does not exist here; the remaining risks are plumbing
(fetch/decode/staleness), which 2 weeks of shadow will surface. Alternative: full severe-season
shadow (spring 2027) per the ROADMAP as written.

## 4. Phases

| Phase | Deliverable | Acceptance | Proof command |
|---|---|---|---|
| **0 — Spike** (half a day, scratch script only) | **DONE 2026-09-07** — Fetch the live latest file + the Crowley 2022-04-05T03:40Z archive file from S3; decode; print max in the 100 km domain and its location. Confirm grid metadata (Ni, Nj, di, dj, first lat/lon, scan order). Confirm no other WSR-88D inside 100 km. | Archive case shows ≥ ~0.008 s⁻¹ within ~20 km of Burleson; live quiet sky shows ~0 | scratch script output |
| **1 — Backtest + threshold** (1-2 days, needs network) | **DONE 2026-09-07** — `services/rotation/rotation_core.py` (pure functions: decode, slice, mask, compute_readout) + `replay.py` + extended case set; files: `services/rotation/{rotation_core,mrms_fetch,replay,build_cases}.py`, `replay_cases.json`, `common/places.py`, `tests/test_rotation_core.py` | S1, S2, S3, S5 pass | `replay.py` table, pasted into this doc's "Results" section |
| **2 — Service + tests + compose** (1-2 days) | **DONE 2026-09-08** — `rotation_service.py`, Dockerfile, compose block, `tests/test_rotation.py`, dashboard registration; deployed to kappa ~12:24Z as commit 76ceb92 (container `rotation-service`, :9010, image `reaped-whirlwind-rotation` 98 MB compressed / 314 MB on disk, `mem_limit 1536m`) via ssh tar transfer + throwaway root `docker:cli` `up -d --build --no-deps rotation`; no other container restarted | `scripts/test.sh` green; `docker-compose -p reaped-whirlwind up -d --build rotation` on kappa; `curl kappa:9010/health` status `running`, `last_score_time` within 5 min of now; JSONL growing every 2 min | `scripts/test.sh`; `curl kappa:9010/health` |
| **3 — Shadow** (≥2 weeks, calendar; no code) | **IN PROGRESS since 2026-09-08 12:24Z** — rotation service running, no cutover | zero `error` status cycles longer than 15 min not attributable to NCEP; rotation JSONL compared against any NWS convective warnings in the alerting decision log; review every ≥threshold cycle: distance from KFWS, coverage fraction, and whether any NWS convective product was active — if the near-radar cases cluster in 5-10 km, widen `RADAR_EXCLUSION_KM` (Crowley's tornado max was 15 km from KFWS, so 10 km would still keep the Phase 1 result) | manual log comparison |
| **4 — Alerting integration + cutover** (1 day; the code can be written and tested during Phase 3, only the cutover waits) | `alert_service.py` changes, `common/geo.py`, `common/places.py`, tests, dry-run emails | `--test-email --dry-run --event "Tornado Warning"` and with `MODEL_ANNOTATION=on` both correct; then set `ANNOTATION_STATUS_PATH` + `MODEL_ANNOTATION=on` in `.env` on kappa (edit in place per `docs/DEPLOY.md`, never chmod), restart alerting, move inference to the `cnn` profile. Update CLAUDE.md status block, `docs/ARCHITECTURE.md`, `docs/DEPLOY.md`, `docs/MODEL_CARD.md` "Superseded", `docs/ROADMAP.md` | dry-run + live email test |
| **5 — Operate** (ongoing) | monthly retrospective (rotation JSONL vs decision JSONL: every Tornado Warning's readout at send time; every readout ≥ threshold and whether a warning existed within 30 min); `/health` alarm if NCEP has returned no new file for >20 min (`fetch_consecutive_failures` counter, same pattern as alerting's `nws_consecutive_failures`); revisit threshold annually | — | monthly retrospective run |

## Results

### Phase 0 — spike (2026-09-07): GO

Scratch script + full output in `scratch/mrms-spike/` (untracked). Environment: eccodes 2.48.0 /
eccodeslib 2.48.0.26 via `pip install eccodes`, clean on macOS arm64 — no brew/apt.

| Check | Result |
|---|---|
| Crowley/Burleson EF2, 2022-04-05 03:44-03:48Z | max 0.014-0.017 s⁻¹ at 10-19 km from Burleson (bar: ≥0.008 within 20 km) |
| Live quiet evening, 2026-09-07 22:48Z | 0.0000 s⁻¹, 0 / 120,293 domain cells nonzero |
| Other WSR-88D inside 100 km | none (nearest KDYX 183 km) — only KFWS's own 5 km disc is needed |
| NCEP index | 1610 files, 26.8 h retention, 1-2 min lag |
| Per-product cycle cost | ~0.35 s download + ~0.5 s decode; slice trivial |

**Grid facts Phase 1 must bake in (all three products share the grid):** Ni 14000 × Nj 7000,
first point (54.9975, 230.0025), last (20.0025, 299.9975), 0.005° both axes, `jScansPositively`
false so **row 0 is north**; **longitudes are 0-360** (normalise site lons with `+360` before
indexing, and `-360` when reporting). `lat = lat0 - row*0.005`, `lon = lon0 + col*0.005`.
Discipline 209 / category 3 / number 0 (AzShear) or 2 (RotationTrack30min); eccodes reports
`shortName`/`units` as `unknown` for this local table, so the 0.001 s⁻¹ scale is hardcoded. Raw
values are small integers as floats (17.0 → 0.017 s⁻¹); no 9999 sentinel appears in practice.
`codes_grib_new_from_file` needs a real file descriptor — spill the gunzipped bytes to a tempfile,
`io.BytesIO` fails. The 30-min RotationTrack max at 03:40Z sat 89 km NNE, not at Burleson —
confirms showing both products rather than picking one.

### Phase 1 — backtest + threshold (2026-09-07): PASS at 0.015 s⁻¹

Case set: 33 EF1+ tornadoes within 100 km of KFWS 2020-10-14..2025 from the SPC CSV (all 5 base
cases matched SPC rows exactly), 36 quiet controls (days with no FWD convective VTEC on D-1/D/D+1;
two of the four legacy `replay_smoke.py` controls, `2023-08-15T21Z` and `2024-04-10T20Z`, fail
that rule and are kept but flagged), 22 severe-thunderstorm-warning controls (FWD SV.W polygons
within 100 km on days with no tornado within 150 km, scored at issuance+10 min). 388 scans, 349
files, all from S3. Reproduce with `services/rotation/build_cases.py` then
`services/rotation/replay.py`. Full per-event table and quiet-control maxima live in
`data/mrms-replay/replay_report.md` (gitignored, regenerable).

#### S1 — quiet controls at or above threshold (target 0)

| threshold | controls >= thr | N | verdict |
|---|---|---|---|
| 0.006 | 7 | 36 | FAIL |
| 0.008 | 3 | 36 | FAIL |
| 0.010 | 0 | 36 | PASS |
| 0.012 | 0 | 36 | PASS |
| 0.015 | 0 | 36 | PASS |

#### S2 — positives detected (target >= 60 % of events)

| threshold | events >= thr | per-event | tornado-time scans >= thr | per-scan | verdict |
|---|---|---|---|---|---|
| 0.006 | 33/33 | 100 % | 162/166 | 98 % | PASS |
| 0.008 | 32/33 | 97 % | 160/166 | 96 % | PASS |
| 0.010 | 31/33 | 94 % | 153/166 | 92 % | PASS |
| 0.012 | 30/33 | 91 % | 140/166 | 84 % | PASS |
| 0.015 | 27/33 | 82 % | 101/166 | 61 % | PASS |

#### S3 — severe-thunderstorm-warning controls at or above threshold (target <= 20 %)

| threshold | controls >= thr | N | fraction | verdict |
|---|---|---|---|---|
| 0.006 | 19 | 22 | 86 % | FAIL |
| 0.008 | 15 | 22 | 68 % | FAIL |
| 0.010 | 10 | 22 | 45 % | FAIL |
| 0.012 | 6 | 22 | 27 % | FAIL |
| 0.015 | 3 | 22 | 14 % | PASS |

#### S5 — scan-to-scan stability (target: median |delta| <= 0.002 s^-1)

Pooled over 297 steps across 33 cases: median |Δ| **0.0010**, max |Δ| **0.0200**.

Verdict: **PASS** (threshold-free — S5 does not depend on the alarm threshold)

#### S6 — honest hit rate over every scan fetched

| threshold | alarms | tornadic alarms | hit rate |
|---|---|---|---|
| 0.006 | 356/388 | 183 | 51 % |
| 0.008 | 348/388 | 183 | 53 % |
| 0.010 | 333/388 | 179 | 54 % |
| 0.012 | 314/388 | 169 | 54 % |
| 0.015 | 266/388 | 143 | 54 % |

#### Verdict

| threshold | S1 | S2 | S3 | S5 | overall |
|---|---|---|---|---|---|
| 0.006 | FAIL | PASS | FAIL | PASS | FAIL |
| 0.008 | FAIL | PASS | FAIL | PASS | FAIL |
| 0.010 | PASS | PASS | FAIL | PASS | FAIL |
| 0.012 | PASS | PASS | FAIL | PASS | FAIL |
| 0.015 | PASS | PASS | PASS | PASS | **PASS** |

**Chosen threshold: 0.015 s⁻¹.** S3 (severe-but-not-tornadic days) is the binding criterion, not
quiet sky — at the literature's 0.010, 45 % of SV.W days exceed it somewhere in the 100 km
domain, so 0.010 is a detection number, not a discrimination number. At 0.015 the SV.W
false-alarm rate is 14 % and per-event tornado recall is 82 % (per-scan 61 %, right at the S2
bar — a thin margin). S6 hit rate is flat at 51-54 % across thresholds: the domain max
barely ranks; what separates positives is *where* the max is (the two misses at 0.010, Garland
2021-05-16 and Terrell 2022-03-30, had healthy domain maxima from storms 63 and 95 km away).
Therefore the Phase 4 warning-polygon check is load-bearing, not polish. S4 is n/a (this is the
baseline).

Negative values exist in the raw field (anticyclonic shear); the readout uses the positive max
only.

### Phase 2 — service on kappa (2026-09-08): DONE

25 h of live operation, `rotation-service` :9010:

| Metric | Value |
|---|---|
| Cycles | 724 |
| `running` / error cycles | 724 / 0 |
| Fetch failures | 0 |
| Source | ncep (100 %) |
| Median cycle time | 6.0 s (decode ~5.1 s for both products, fetch ~0.65 s) |
| Idle RSS | 112 MiB |
| `/health` | `status running`, `score_age_seconds` ~180 at the 2-min cadence |
| Cycles with positive max | 105 / 724 |

Highest reading: 0.017 s⁻¹ at 2026-09-08T19:24Z and 19:26Z, 6.7 km NNW of KFWS ("near Crowley"),
domain coverage only 0.6-1.1 % nonzero — i.e. at/above the 0.015 threshold from a tiny echo patch
just outside the 5 km radar-exclusion disc. Whether this was a real small cell or a near-radar
LLSD artifact is unknown; it is exactly the kind of case the Phase 3 shadow exists to catch. First
shadow-log finding; see §4 Phase 3 acceptance and §5 Risks.

## 5. Risks and open questions

| Risk | Mitigation |
|---|---|
| NCEP retention/SLA undocumented | S3 fallback |
| Missing = 0 ambiguity | surface `coverage_nonzero_fraction`; `RadarQualityIndex` later if needed |
| eccodes wheel on kappa's CPU arch | kappa is x86_64 — verify in Phase 2 build |
| 100 km disc vs warning polygon | polygon check added in Phase 4 |
| AzShear noise from gust fronts / heartbeat echoes | show `RotationTrack30min` alongside; annotation wording is diagnostic, never the alert |
| IEM MRMS archive unverified for AzShear | use S3 instead |
| Cresson 2020 case lost to archive start (2020-10-14) | dropped from the case set, noted above |
| Domain-max threshold alone barely discriminates (S6 flat ~53 %) | polygon check in Phase 4; readout shows location + distance, not just a number |
| Legacy quiet controls 2023-08-15 / 2024-04-10 are adjacent to severe days | flagged in replay_cases.json; kept for continuity with replay_smoke |
| Near-radar (5-10 km) elevated readings with near-zero coverage | shadow review; candidate to widen the exclusion disc; see Results Phase 2 |

## 6. What does not change

The NWS warning path, `ALLOWED_EVENTS`, suppression logic, SMS format, the "experimental" label,
and `docs/MODEL_CARD.md`'s invalidation record (it stays as history).

## Delegation map

| Step | Level |
|---|---|
| Phase 0 spike | sonnet |
| `rotation_core.py` + `replay.py` + threshold analysis | opus |
| Service skeleton (copy pattern from `inference_service.py`) | sonnet |
| Dockerfile / compose / dashboard registration / places table | haiku |
| Tests | sonnet |
| Alerting integration + `common/geo.py` | opus |
| Docs updates | sonnet |
| Deploy commands | haiku, human at the keyboard for `.env` |
