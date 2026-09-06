# Radar Image Processor

Converts weather radar screenshots (base reflectivity + base velocity PNGs from
`radar.weather.gov`, captured by the `screenshot` service) into numeric JSON grids, for
downstream ML training and analysis. Deployed as the `processor` compose service — see
`../../docs/ARCHITECTURE.md` for how it fits into the pipeline and `AGENT.md` for the
service contract.

## How conversion works

Each radar type (`reflectivity`, `velocity`) has a small reference scale image
(`base_reflectivity_intensity_scale.png`, `base_velocity_intensity_scale.png`) mapping
colors to physical values (dBZ -20..70, knots -100..100). `radar_tools/color_scale.py`
samples that scale bar into a list of `(RGB, value)` pairs. `radar_tools/converter.py`
builds a KD-tree (`scipy.spatial.cKDTree`) over those colors once at startup, then for
every pixel finds its nearest-neighbor color via tree lookup and takes that color's value
— an O(log n) replacement for the naive O(n) per-pixel Euclidean-distance scan, and the
reason conversion is fast enough to run continuously on NAS-class hardware. Output is a
JSON grid (`radar_tools/utils.py` writes it) with metadata (dimensions, sample rate, units,
value range, source file).

## Package layout

- `radar_tools/color_scale.py` — `RadarColorScale`: extracts the color↔value mapping from
  a scale image.
- `radar_tools/converter.py` — `RadarImageConverter`: PNG → JSON via KD-tree color lookup.
- `radar_tools/verifier.py` — `RadarImageVerifier`: reconstructs a PNG from JSON to sanity
  check a conversion.
- `radar_tools/utils.py` — JSON I/O, stats, dataset comparison helpers.
- `processor_service.py` — the long-running loop: watches `/input`, waits `min_age_seconds`
  (config.yaml) for a file to finish landing, converts, verifies, deletes the source.
- `web_server.py` — Flask app exposing `/`, `/api/status`, `/health` (see AGENT.md).
- `config.yaml` — processing knobs (sample rate, age threshold, check interval, delete/verify
  toggles); bind-mounted, so edits apply on `docker-compose restart processor`.

## Operator tools

These are genuinely used for validating conversion quality, not one-off debug scripts —
kept and runnable standalone (need `PIL`, `numpy`, `scipy`; same `requirements.txt` as the
service):

- **`convert.py`** — CLI wrapper around `RadarImageConverter`.
  `python convert.py IMAGE --type {reflectivity,velocity} --output OUT.json [--sample-rate N] [--save-numpy]`
- **`verify.py`** — CLI wrapper around `RadarImageVerifier`; reconstructs an image from JSON
  and diffs it against the original, pixel-for-pixel. Good for images cropped to just radar
  data (no map background).
  `python verify.py ORIGINAL.png DATA.json [--output-dir DIR] [--reconstruct-only]`
- **`verify_masked.py`** — like `verify.py` but ignores non-weather pixels (map background,
  UI chrome, labels) before scoring accuracy, which is the realistic case for full
  `radar.weather.gov` screenshots — unmasked diffing reports misleadingly low accuracy
  because most of the frame is map, not radar data.
  `python verify_masked.py ORIGINAL.png DATA.json [--output-dir DIR]`
- **`tune_mask.py`** — visualizes what `verify_masked.py`'s mask includes/excludes, for
  tuning its thresholds against a new background color or capture layout.
  `python tune_mask.py IMAGE.png [--suggest] [--map-bg-tolerance N] [--dark-threshold N] [--white-threshold N]`
- **`radar_health_check.sh`** — Synology Task Scheduler script (run as root, every 10 min)
  that curls `/health` on :9005 and fires a DSM notification on failure/recovery. Not
  invoked by anything in this repo — it's an external NAS cron job, kept here as the
  source of truth for what's installed on kappa.

## Known limitations / tuning knobs

- Color matching is nearest-neighbor in RGB space — antialiasing or JPEG-style compression
  artifacts near color-scale boundaries can shift a pixel to the adjacent bucket. Radar PNGs
  from `radar.weather.gov` are not compressed this way in practice, so it hasn't been an issue.
- `config.yaml: processing.sample_rate` trades resolution for file size (rate 4 ≈ 16x smaller
  than rate 1); the live deploy currently runs at a higher rate than the "recommended 4" from
  early tuning notes — check `config.yaml` for the current value before assuming.
- `min_age_seconds` exists because screenshots are written in place; too low a value risks
  converting a partially-written PNG.
- Mask thresholds in `verify_masked.py` (map-background tolerance, dark/white cutoffs) are
  tuned for the current `radar.weather.gov` beige map background — if the capture URL/theme
  in `services/screenshot/config.yaml` ever changes, re-run `tune_mask.py --suggest`.
