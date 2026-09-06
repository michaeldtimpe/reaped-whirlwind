# analysis/

Offline, read-only analysis of the deployed Part C services. Nothing here runs in the stack or
writes to kappa.

## `retrospective.py`

Generates the numbers in `docs/RETROSPECTIVE.md`.

```sh
python3 analysis/retrospective.py              # analyse the cached inputs
python3 analysis/retrospective.py --fetch      # re-pull inputs, then analyse
python3 analysis/retrospective.py --section 3  # one section (repeatable)
```

No third-party dependencies — the ESRI shapefile / dBASE readers and the point-in-polygon test
are inline, because neither the analysis Mac nor kappa has `shapely` or `pyshp`, and this must
stay runnable from a bare checkout.

## `cache/` — inputs (~1.2 MB, refreshed by `--fetch`)

| File | Source |
|---|---|
| `fwd_vtec.zip` / `.csv` | IEM `watchwarn.py`, WFO FWD, 2026-05-27 → 2026-09-06 |
| `lsr.zip` | IEM `lsr.py`, WFO FWD, same window |
| `nws_hist.json` | `api.weather.gov/alerts?point=32.5728,-97.3031&start=…&end=…` |
| `inference_status.json`, `alerting_status.json`, `alerts_sent.json` | `ssh magehands@kappa 'cat /volume1/docker/service-status/…'` |
| `cache_listing.txt.gz` | `ls -l` of `/volume1/docker/inference-state/current` — **filenames and mtimes only** |

`cache_listing.txt.gz` is the load-bearing one: no score time series is persisted anywhere, so
the scoring timeline is reconstructed from the cached PNG filenames, which encode radar scan
times. **Never pull the PNGs themselves — that directory is 2.4 GB.**

## kappa access notes

`scp`/SFTP are disabled. Pull files with `ssh … 'cat <file>' > local`, and note that Docker is
not on the non-interactive PATH — use `/usr/local/bin/docker`. `docker logs` is backed by a
Synology `log.db` and is very slow over a 250k-line history; prefer `--since` with a tight
bound, and expect only werkzeug `/health` access lines.
