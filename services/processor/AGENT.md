# services/processor — agent notes

**What it is.** Second stage of the pipeline. Watches `/input` (shared with `screenshot`'s
output) for `radar_base_{reflectivity,velocity}_*.png`, waits `min_age_seconds`
(config.yaml) so a file has finished landing, converts it to a JSON value-grid via
`radar_tools` (see `README.md` for the conversion algorithm), verifies the output, then
deletes the source PNG.

**Entry point.** `processor_service.py` (the processing loop + status tracking) +
`web_server.py` (Flask, runs alongside it in the same container) on `:8080` (mapped to
`:9005`). `PROCESSING_MODE` env var (`normal`/`test`/`debug`) is read at startup by
`RadarProcessor.__init__`; `SAMPLE_RATE` env var overrides `config.yaml`'s sample rate.
Pinned to a single worker thread (`max_workers = 1`) deliberately, to keep CPU load low on
the NAS — see `docker-compose.yml`'s `cpu_shares`/`cpuset` for the rest of that budget.

**Status file.** Writes `/status/processor_status.json` via `ProcessorStatus`; the
dashboard's `SERVICES["radar-processor"]` reads it.

**Health endpoint.** `GET /health` on `:8080` — see `web_server.py`; reports unhealthy if no
processing has happened in 20+ minutes. Also `GET /api/status` (dashboard detail) and `GET /`
(HTML view).

**Gotchas.**
- `radar_tools/converter.py` builds a KD-tree over the scale image's colors at
  `RadarImageConverter.__init__` — if this fails (bad/missing scale PNG), the whole service
  raises and refuses to start; check `base_*_intensity_scale.png` are present and readable.
- `delete_after_processing: true` in `config.yaml` means a bad conversion silently loses the
  source PNG unless `verify_output: true` catches it first — don't flip both to false at once.
- Only `converter.py`, `color_scale.py`, `verifier.py`, `utils.py` in `radar_tools/` are live;
  don't resurrect old variant filenames (`converter_final.py` etc. were removed as dead code —
  see git history if you need the old approach for reference).

See `README.md` for the conversion algorithm and operator tools, and
`../../docs/ARCHITECTURE.md` for how this fits into the six-service pipeline.
