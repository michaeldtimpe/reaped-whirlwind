#!/usr/bin/env python3
"""
Phase 1 gate: does MRMS 0-2 km azimuthal shear actually separate DFW tornadoes
from quiet sky, and at what threshold? (docs/MRMS_MIGRATION.md §3, Phase 1.)

This is the backtest that decides whether the rotation annotation ships and
what `ROTATION_THRESHOLD` is set to. It replaces `services/inference/
replay_smoke.py` as the gate for the annotation. It calls the *same*
`compute_readout()` the service will call — nothing here re-implements the
science, so a passing gate is a statement about the shipped code path.

For every SPC-confirmed positive it pulls the MergedAzShear_0-2kmAGL series
from t-10 to t+10 min out of the anonymous NOAA S3 archive (~10 files at 2-min
cadence) and, for every quiet / severe-thunderstorm-warning control, the single
file nearest the case time. Each scan yields:

  * `max_azshear` — the strongest cell anywhere in the 100 km KFWS domain
    (minus the 5 km radar-artifact disc). This is what the service would have
    annotated a warning with at that moment, so it is what S1/S3/S6 judge.
  * `max_within_25km` — the strongest cell within 25 km of the tornado's actual
    touchdown point, intersected with the same domain mask. This is what S2
    judges: "did the product light up *where the tornado was*", not merely
    "somewhere in north Texas".

Both matter. A threshold that fires on every positive by way of an unrelated
storm 80 km away has learned nothing, and the two columns make that visible.

Criteria (targets from docs/MRMS_MIGRATION.md §3):
  S1  quiet controls at or above threshold ........... 0
  S2  positives with max_within_25km >= threshold .... >= 60 % (per event)
  S3  SV.W controls with domain max >= threshold ..... <= 20 %
  S5  median |delta max_azshear| scan-to-scan ........ <= 0.002 s^-1
  S6  honest hit-rate table (no target; informs the threshold choice)

Usage (repo root, needs network + eccodes in .venv):
  .venv/bin/python services/rotation/replay.py                  # full case file
  .venv/bin/python services/rotation/replay.py --quick          # built-in 9 cases
  .venv/bin/python services/rotation/replay.py --cases path.json

Writes data/mrms-replay/results.json (every scan) and
data/mrms-replay/replay_report.md (the tables below). Raw downloads are cached
under data/mrms-replay/<product>/ so a re-run is offline and instant.

Exit status: 0 if the recommended threshold PASSes, 1 otherwise (including "no
threshold passes" — a FAIL with a readable table is a valid outcome), 2 if the
archive could not be reached at all.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))

import numpy as np                                                   # noqa: E402
import requests                                                      # noqa: E402

import mrms_fetch                                                    # noqa: E402
from mrms_fetch import AZSHEAR_0_2                                   # noqa: E402
from rotation_core import (DEFAULT_DOMAIN_RADIUS_KM, RADAR_EXCLUSION_KM,  # noqa: E402
                           DomainMask, compute_readout, decode_grib_gz,
                           haversine_km)

from common import nws                                               # noqa: E402
from common.places import DFW_PLACES                                 # noqa: E402
from common.status import utc_iso                                    # noqa: E402

log = logging.getLogger("rotation.replay")

CACHE_ROOT = _REPO / "data" / "mrms-replay"
DEFAULT_CASES = _HERE / "replay_cases.json"

WINDOW_MIN = 10          # +/- minutes of scans pulled around each positive
TORNADO_TIME_MIN = 5     # "tornado-time" scans for the per-scan S2 figure
TORNADIC_WINDOW_MIN = 15 # S6's definition of a scan being tornadic in time
TORNADO_RADIUS_KM = 25.0 # S2/S6's definition of being tornadic in space
CONTROL_MAX_DELTA_S = 180

THRESHOLDS: Tuple[float, ...] = (0.006, 0.008, 0.010, 0.012, 0.015)
# top_cells are picked relative to a reference threshold; the S-criteria only
# use max values, which are threshold-free, so one pass over the data serves
# every threshold in THRESHOLDS.
REF_THRESHOLD = min(THRESHOLDS)

S1_TARGET_COUNT = 0
S2_TARGET_FRACTION = 0.60
S3_TARGET_FRACTION = 0.20
S5_TARGET_DELTA = 0.002

FETCH_WORKERS = 4

# Built-in fallback case set (docs/MRMS_MIGRATION.md §3). Used when the extended
# case file has not been generated yet, and by --quick. Tornado points are the
# SPC touchdown coordinates; Cresson 2020-01-10 is absent on purpose — it
# predates the S3 archive start (2020-10-14).
BASE_CASES: Dict[str, Any] = {
    "generated": None,
    "source": "built-in BASE_CASES (docs/MRMS_MIGRATION.md §3)",
    "positives": [
        {"id": "2022-04-05-crowley",     "time": "2022-04-05T03:41:00Z",
         "lat": 32.579, "lon": -97.363, "ef": 2, "source": "builtin"},
        {"id": "2020-11-25-arlington",   "time": "2020-11-25T02:51:00Z",
         "lat": 32.735, "lon": -97.108, "ef": 2, "source": "builtin"},
        {"id": "2022-12-13-fort-worth",  "time": "2022-12-13T14:14:00Z",
         "lat": 32.755, "lon": -97.331, "ef": 1, "source": "builtin"},
        {"id": "2023-03-16-irving",      "time": "2023-03-16T21:47:00Z",
         "lat": 32.814, "lon": -96.949, "ef": 1, "source": "builtin"},
        {"id": "2025-03-04-dallas",      "time": "2025-03-04T11:24:00Z",
         "lat": 32.777, "lon": -96.797, "ef": 1, "source": "builtin"},
    ],
    "quiet_controls": [
        {"id": "quiet-2023-08-15-aug-afternoon", "time": "2023-08-15T21:00:00Z"},
        {"id": "quiet-2024-01-20-jan-night",     "time": "2024-01-20T06:00:00Z"},
        {"id": "quiet-2024-04-10-apr-afternoon", "time": "2024-04-10T20:00:00Z"},
        {"id": "quiet-2022-10-05-oct-morning",   "time": "2022-10-05T15:00:00Z"},
    ],
    "svw_controls": [],
}


# ---------------------------------------------------------------------------
# case loading
# ---------------------------------------------------------------------------
def parse_iso_z(ts: str) -> datetime:
    """Parse the case file's ISO-Z timestamps (with or without seconds)."""
    t = ts.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(t)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_cases(path: Optional[Path], quick: bool) -> Dict[str, Any]:
    """The case set: the generated file when present, else BASE_CASES.

    The file is produced by `build_cases.py` (SPC tornado CSV + IEM VTEC warning
    archive) and is the contract between the two: positives / quiet_controls /
    svw_controls, each entry carrying at least `id` and `time`.
    """
    if quick or path is None or not path.exists():
        if not quick and path is not None:
            log.info("case file %s not present — falling back to BASE_CASES", path)
        cases = json.loads(json.dumps(BASE_CASES))
        cases["generated"] = None
        return cases
    cases = json.loads(path.read_text())
    cases.setdefault("positives", [])
    cases.setdefault("quiet_controls", [])
    cases.setdefault("svw_controls", [])
    cases.setdefault("source", str(path))
    return cases


# ---------------------------------------------------------------------------
# fetch + cache
# ---------------------------------------------------------------------------
def cache_path(product: str, key_or_name: str) -> Path:
    """Where one raw download lives: data/mrms-replay/<product>/<filename>."""
    return CACHE_ROOT / product / key_or_name.rsplit("/", 1)[-1]


def ensure_cached(product: str, key: str, session: requests.Session) -> Optional[Path]:
    """Download `key` unless it is already on disk. Returns None on failure —
    one unreachable scan must not abort a 10-minute backtest."""
    dest = cache_path(product, key)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        blob = mrms_fetch.s3_fetch(key, session=session)
    except Exception as e:                                            # noqa: BLE001
        log.warning("fetch failed for %s: %s", key, e)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(blob)
    tmp.replace(dest)
    return dest


def prefetch(keys: Sequence[Tuple[str, str]]) -> Dict[str, Optional[Path]]:
    """Download every (product, key) pair with a small thread pool.

    Decode is deliberately NOT parallel: a single CONUS field peaks around
    1.2 GB during the float64 -> float32 narrowing, so four concurrent decodes
    would be 5 GB. Network is the part worth overlapping.
    """
    out: Dict[str, Optional[Path]] = {}
    # Dedupe: overlapping +/-10 min windows (2022-12-13 alone has six tornadoes)
    # ask for the same scan more than once, and two workers writing the same
    # .part file would race.
    unique = list(dict.fromkeys(keys))
    todo = [(p, k) for p, k in unique if not (cache_path(p, k).exists()
                                              and cache_path(p, k).stat().st_size > 0)]
    for product, key in unique:
        out[key] = cache_path(product, key)
    if not todo:
        log.info("all %d files already cached", len(unique))
        return out
    log.info("fetching %d/%d files (%d cached)", len(todo), len(unique),
             len(unique) - len(todo))
    session = requests.Session()
    done = 0
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(ensure_cached, p, k, session): k for p, k in todo}
        for fut, key in futures.items():
            out[key] = fut.result()
            done += 1
            if done % 10 == 0 or done == len(todo):
                log.info("  fetched %d/%d", done, len(todo))
    return out


# ---------------------------------------------------------------------------
# scan scoring
# ---------------------------------------------------------------------------
class Scorer:
    """Decodes scans and turns each into one result row.

    The 100 km KFWS `DomainMask` and each case's "within 25 km of the tornado"
    selector are built once, on the first decoded grid, and reused — that is the
    whole point of `DomainMask` being separate from `compute_readout`.
    """

    def __init__(self) -> None:
        self.grid = None
        self.mask: Optional[DomainMask] = None
        self._tornado_sel: Dict[str, Optional[np.ndarray]] = {}

    def _ensure_mask(self, grid) -> DomainMask:
        if self.mask is None:
            self.grid = grid
            self.mask = DomainMask(grid, nws.KFWS_LAT, nws.KFWS_LON,
                                   radius_km=DEFAULT_DOMAIN_RADIUS_KM,
                                   exclusion_km=RADAR_EXCLUSION_KM)
            log.info("domain: rows %d:%d cols %d:%d, %d cells within %.0f km of KFWS "
                     "(minus the %.0f km radar disc)",
                     self.mask.row0, self.mask.row1, self.mask.col0, self.mask.col1,
                     self.mask.n_cells, DEFAULT_DOMAIN_RADIUS_KM, RADAR_EXCLUSION_KM)
        elif grid != self.grid:
            raise RuntimeError(f"grid changed mid-run: {grid} != {self.grid}")
        return self.mask

    def tornado_selector(self, case_id: str, lat: float, lon: float) -> Optional[np.ndarray]:
        """Boolean sub-grid selector for "within TORNADO_RADIUS_KM of the
        touchdown point AND inside the domain". Intersecting with the domain
        mask keeps the 5 km KFWS artifact disc out of the tornado figure too —
        Crowley is 15 km from KFWS, so its 25 km disc contains the radar."""
        if case_id not in self._tornado_sel:
            assert self.mask is not None
            sel = self.mask.mask & (self.mask.distance_to(lat, lon) <= TORNADO_RADIUS_KM)
            self._tornado_sel[case_id] = sel if sel.any() else None
            if sel.any():
                log.debug("case %s: %d cells within %.0f km of the tornado",
                          case_id, int(sel.sum()), TORNADO_RADIUS_KM)
            else:
                log.warning("case %s: tornado point (%.3f, %.3f) has no domain cells "
                            "within %.0f km — max_within_25km will be null",
                            case_id, lat, lon, TORNADO_RADIUS_KM)
        return self._tornado_sel[case_id]

    def score(self, path: Path, case: Dict[str, Any], kind: str,
              valid: datetime) -> Dict[str, Any]:
        values, grid = decode_grib_gz(path)
        try:
            mask = self._ensure_mask(grid)
            readout = compute_readout(values, mask, REF_THRESHOLD, places=DFW_PLACES)
            row: Dict[str, Any] = {
                "case_id": case["id"],
                "kind": kind,
                "valid_time": valid.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "offset_s": (valid - parse_iso_z(case["time"])).total_seconds(),
                "max_azshear": readout["max_azshear"],
                "coverage_nonzero_fraction": readout["coverage_nonzero_fraction"],
                "cells_ge_ref": readout["cells_ge_threshold"],
                "max_location": readout["max_location"],
                "top_cells": readout["top_cells"],
                "max_within_25km": None,
                "max_cell_km_from_tornado": None,
            }
            if kind == "positive":
                sel = self.tornado_selector(case["id"], case["lat"], case["lon"])
                if sel is not None:
                    sub = mask.crop(values)
                    row["max_within_25km"] = round(float(sub[sel].max()), 6)
                loc = readout["max_location"]
                if loc is not None:
                    row["max_cell_km_from_tornado"] = round(
                        haversine_km(case["lat"], case["lon"], loc["lat"], loc["lon"]), 1)
            return row
        finally:
            del values
            gc.collect()


def is_tornadic(row: Dict[str, Any]) -> bool:
    """S6's definition: a positive-case scan within +/-15 min of touchdown whose
    strongest domain cell lies within 25 km of the touchdown point. Note this is
    threshold-free — it describes the scan, not the alarm."""
    if row["kind"] != "positive":
        return False
    if abs(row["offset_s"]) > TORNADIC_WINDOW_MIN * 60:
        return False
    d = row.get("max_cell_km_from_tornado")
    return d is not None and d <= TORNADO_RADIUS_KM


# ---------------------------------------------------------------------------
# criteria
# ---------------------------------------------------------------------------
def evaluate(rows: List[Dict[str, Any]], cases: Dict[str, Any]) -> Dict[str, Any]:
    """Every criterion at every threshold, plus the recommendation."""
    by_case: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_case.setdefault(r["case_id"], []).append(r)
    for series in by_case.values():
        series.sort(key=lambda r: r["valid_time"])

    pos_ids = [c["id"] for c in cases["positives"] if c["id"] in by_case]
    quiet_ids = [c["id"] for c in cases["quiet_controls"] if c["id"] in by_case]
    svw_ids = [c["id"] for c in cases["svw_controls"] if c["id"] in by_case]

    # window/tornado-time maxima, threshold-free
    per_event: Dict[str, Dict[str, Any]] = {}
    for cid in pos_ids:
        series = by_case[cid]
        within = [r["max_within_25km"] for r in series if r["max_within_25km"] is not None]
        t_scans = [r for r in series if abs(r["offset_s"]) <= TORNADO_TIME_MIN * 60
                   and r["max_within_25km"] is not None]
        per_event[cid] = {
            "n_scans": len(series),
            "window_max_within_25km": max(within) if within else None,
            "window_max_domain": max(r["max_azshear"] for r in series) if series else None,
            "tornado_time_values": [r["max_within_25km"] for r in t_scans],
            "min_km_max_cell_to_tornado": min(
                [r["max_cell_km_from_tornado"] for r in series
                 if r["max_cell_km_from_tornado"] is not None] or [None]),
        }

    quiet_max = {cid: max(r["max_azshear"] for r in by_case[cid]) for cid in quiet_ids}
    svw_max = {cid: max(r["max_azshear"] for r in by_case[cid]) for cid in svw_ids}

    # S5 — scan-to-scan stability inside each positive window (threshold-free)
    s5_per_case: Dict[str, Dict[str, float]] = {}
    all_deltas: List[float] = []
    for cid in pos_ids:
        series = by_case[cid]
        deltas = [abs(series[i]["max_azshear"] - series[i - 1]["max_azshear"])
                  for i in range(1, len(series))]
        if deltas:
            s5_per_case[cid] = {"n_steps": len(deltas),
                                "median": round(statistics.median(deltas), 6),
                                "max": round(max(deltas), 6)}
            all_deltas.extend(deltas)
    s5 = {
        "per_case": s5_per_case,
        "pooled_median": round(statistics.median(all_deltas), 6) if all_deltas else None,
        "pooled_max": round(max(all_deltas), 6) if all_deltas else None,
        "n_steps": len(all_deltas),
        "target": S5_TARGET_DELTA,
        "pass": bool(all_deltas) and statistics.median(all_deltas) <= S5_TARGET_DELTA,
    }

    tornadic_flags = {id(r): is_tornadic(r) for r in rows}

    per_threshold: Dict[str, Any] = {}
    for thr in THRESHOLDS:
        s1_hits = [cid for cid, v in quiet_max.items() if v >= thr]
        s1 = {"n": len(quiet_max), "hits": len(s1_hits), "hit_ids": s1_hits,
              "pass": len(s1_hits) <= S1_TARGET_COUNT}

        ev_hits = [cid for cid in pos_ids
                   if (per_event[cid]["window_max_within_25km"] or 0.0) >= thr]
        scan_vals = [v for cid in pos_ids for v in per_event[cid]["tornado_time_values"]]
        scan_hits = [v for v in scan_vals if v >= thr]
        s2 = {
            "n_events": len(pos_ids),
            "events_hit": len(ev_hits),
            "event_fraction": (len(ev_hits) / len(pos_ids)) if pos_ids else None,
            "missed_ids": [cid for cid in pos_ids if cid not in ev_hits],
            "n_tornado_time_scans": len(scan_vals),
            "scans_hit": len(scan_hits),
            "scan_fraction": (len(scan_hits) / len(scan_vals)) if scan_vals else None,
        }
        s2["pass"] = bool(pos_ids) and s2["event_fraction"] >= S2_TARGET_FRACTION

        if svw_max:
            s3_hits = [cid for cid, v in svw_max.items() if v >= thr]
            s3 = {"n": len(svw_max), "hits": len(s3_hits),
                  "fraction": len(s3_hits) / len(svw_max),
                  "pass": (len(s3_hits) / len(svw_max)) <= S3_TARGET_FRACTION,
                  "available": True}
        else:
            s3 = {"n": 0, "hits": 0, "fraction": None, "pass": None, "available": False}

        alarms = [r for r in rows if r["max_azshear"] >= thr]
        hits = [r for r in alarms if tornadic_flags[id(r)]]
        s6 = {"n_scans_total": len(rows), "n_alarms": len(alarms),
              "n_tornadic_alarms": len(hits),
              "hit_rate": (len(hits) / len(alarms)) if alarms else None,
              "n_tornadic_scans": sum(1 for r in rows if tornadic_flags[id(r)])}

        overall = bool(s1["pass"] and s2["pass"] and s5["pass"]
                       and (s3["pass"] is not False))
        per_threshold[f"{thr:.3f}"] = {"threshold": thr, "S1": s1, "S2": s2,
                                       "S3": s3, "S6": s6, "pass": overall}

    # Recommendation: among thresholds that keep the false-alarm criteria clean
    # (S1 and S3), take the strongest detection (S2 per-event), break ties on the
    # honest hit rate (S6), then on the lower threshold.
    eligible = [t for t in THRESHOLDS
                if per_threshold[f"{t:.3f}"]["S1"]["pass"]
                and per_threshold[f"{t:.3f}"]["S3"]["pass"] is not False]
    recommended = None
    if eligible:
        recommended = sorted(
            eligible,
            key=lambda t: (-(per_threshold[f"{t:.3f}"]["S2"]["event_fraction"] or 0.0),
                           -(per_threshold[f"{t:.3f}"]["S6"]["hit_rate"] or 0.0),
                           t))[0]

    return {
        "per_case": by_case,
        "per_event": per_event,
        "quiet_max": quiet_max,
        "svw_max": svw_max,
        "S5": s5,
        "per_threshold": per_threshold,
        "eligible_thresholds": eligible,
        "recommended_threshold": recommended,
        "recommended_pass": bool(recommended is not None
                                 and per_threshold[f"{recommended:.3f}"]["pass"]),
        "pos_ids": pos_ids, "quiet_ids": quiet_ids, "svw_ids": svw_ids,
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _f(v: Optional[float], nd: int = 4) -> str:
    return "n/a" if v is None else f"{v:.{nd}f}"


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{100.0 * v:.0f} %"


def _yn(v: Optional[bool]) -> str:
    return "n/a" if v is None else ("PASS" if v else "FAIL")


def render_report(ev: Dict[str, Any], cases: Dict[str, Any], meta: Dict[str, Any]) -> str:
    L: List[str] = []
    a = L.append
    a("# MRMS rotation backtest — Phase 1 gate")
    a("")
    a(f"Run {meta['run_utc']} · cases: {meta['case_source']} · "
      f"{len(ev['pos_ids'])} positives / {len(ev['quiet_ids'])} quiet / "
      f"{len(ev['svw_ids'])} SV.W controls · {meta['n_scans']} case-scan rows "
      f"({meta['n_unique_files']} distinct files)")
    a(f"Product `{AZSHEAR_0_2}` · domain {DEFAULT_DOMAIN_RADIUS_KM:.0f} km around KFWS "
      f"minus {RADAR_EXCLUSION_KM:.0f} km · tornado radius {TORNADO_RADIUS_KM:.0f} km")
    a("")

    a("## Positives (per event)")
    a("")
    a("| case | EF | scans | window max in domain | window max within 25 km | "
      "closest max-cell to tornado | tornado-time scans |")
    a("|---|---|---|---|---|---|---|")
    ef_by_id = {c["id"]: c.get("ef") for c in cases["positives"]}
    for cid in ev["pos_ids"]:
        e = ev["per_event"][cid]
        a(f"| {cid} | {ef_by_id.get(cid, '?')} | {e['n_scans']} | "
          f"{_f(e['window_max_domain'])} | {_f(e['window_max_within_25km'])} | "
          f"{_f(e['min_km_max_cell_to_tornado'], 1)} km | "
          f"{len(e['tornado_time_values'])} |")
    a("")

    a("## S1 — quiet controls at or above threshold (target 0)")
    a("")
    a("| threshold | controls >= thr | N | verdict |")
    a("|---|---|---|---|")
    for thr in THRESHOLDS:
        s1 = ev["per_threshold"][f"{thr:.3f}"]["S1"]
        a(f"| {thr:.3f} | {s1['hits']} | {s1['n']} | {_yn(s1['pass'])} |")
    a("")
    if ev["quiet_max"]:
        a("Quiet-control domain maxima: "
          + ", ".join(f"`{cid}` {v:.4f}" for cid, v in sorted(ev["quiet_max"].items())))
        a("")

    a("## S2 — positives detected (target >= 60 % of events)")
    a("")
    a("| threshold | events >= thr | per-event | tornado-time scans >= thr | per-scan | verdict |")
    a("|---|---|---|---|---|---|")
    for thr in THRESHOLDS:
        s2 = ev["per_threshold"][f"{thr:.3f}"]["S2"]
        a(f"| {thr:.3f} | {s2['events_hit']}/{s2['n_events']} | {_pct(s2['event_fraction'])} | "
          f"{s2['scans_hit']}/{s2['n_tornado_time_scans']} | {_pct(s2['scan_fraction'])} | "
          f"{_yn(s2['pass'])} |")
    a("")

    a("## S3 — severe-thunderstorm-warning controls at or above threshold (target <= 20 %)")
    a("")
    if not ev["svw_ids"]:
        a("n/a — the case file carries no `svw_controls`.")
    else:
        a("| threshold | controls >= thr | N | fraction | verdict |")
        a("|---|---|---|---|---|")
        for thr in THRESHOLDS:
            s3 = ev["per_threshold"][f"{thr:.3f}"]["S3"]
            a(f"| {thr:.3f} | {s3['hits']} | {s3['n']} | {_pct(s3['fraction'])} | "
              f"{_yn(s3['pass'])} |")
    a("")

    a("## S5 — scan-to-scan stability (target: median |delta| <= 0.002 s^-1)")
    a("")
    a("| case | steps | median \\|delta\\| | max \\|delta\\| |")
    a("|---|---|---|---|")
    for cid, v in ev["S5"]["per_case"].items():
        a(f"| {cid} | {v['n_steps']} | {_f(v['median'])} | {_f(v['max'])} |")
    a(f"| **pooled** | {ev['S5']['n_steps']} | **{_f(ev['S5']['pooled_median'])}** | "
      f"**{_f(ev['S5']['pooled_max'])}** |")
    a("")
    a(f"Verdict: **{_yn(ev['S5']['pass'])}** "
      f"(threshold-free — S5 does not depend on the alarm threshold)")
    a("")

    a("## S6 — honest hit rate over every scan fetched")
    a("")
    a("A scan counts as an alarm when its **domain** max reaches the threshold, and as a hit "
      "when it is a positive-case scan within 15 min of touchdown whose strongest cell is "
      "within 25 km of it. Controls can only ever be false alarms, so this rate is bounded by "
      "the case mix — read it as a ranking signal between thresholds, not an operational PPV. "
      "A scan shared by two positives whose windows overlap is counted once per case, because "
      "whether it is tornadic is a statement about the case, not about the file.")
    a("")
    a("| threshold | alarms | tornadic alarms | hit rate |")
    a("|---|---|---|---|")
    for thr in THRESHOLDS:
        s6 = ev["per_threshold"][f"{thr:.3f}"]["S6"]
        a(f"| {thr:.3f} | {s6['n_alarms']}/{s6['n_scans_total']} | {s6['n_tornadic_alarms']} | "
          f"{_pct(s6['hit_rate'])} |")
    a("")

    a("## Verdict")
    a("")
    a("| threshold | S1 | S2 | S3 | S5 | overall |")
    a("|---|---|---|---|---|---|")
    for thr in THRESHOLDS:
        t = ev["per_threshold"][f"{thr:.3f}"]
        a(f"| {thr:.3f} | {_yn(t['S1']['pass'])} | {_yn(t['S2']['pass'])} | "
          f"{_yn(t['S3']['pass'])} | {_yn(ev['S5']['pass'])} | "
          f"{'**PASS**' if t['pass'] else 'FAIL'} |")
    a("")
    rec = ev["recommended_threshold"]
    if rec is None:
        a("**No threshold recommended** — none kept S1 (and S3) clean.")
    else:
        t = ev["per_threshold"][f"{rec:.3f}"]
        a(f"**Recommended threshold: {rec:.3f} s^-1** — the strongest S2 among the thresholds "
          f"that keep S1/S3 clean (S2 per-event {_pct(t['S2']['event_fraction'])}, "
          f"S6 hit rate {_pct(t['S6']['hit_rate'])}).")
        a("")
        a(f"**{'PASS' if t['pass'] else 'FAIL'}** at the recommended threshold.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    global CACHE_ROOT
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES,
                    help=f"case JSON (default {DEFAULT_CASES}); BASE_CASES if absent")
    ap.add_argument("--quick", action="store_true",
                    help="ignore the case file and use the 9 built-in cases")
    ap.add_argument("--out-dir", type=Path, default=CACHE_ROOT,
                    help="where results.json / replay_report.md / the raw cache go")
    ap.add_argument("--window-min", type=int, default=WINDOW_MIN,
                    help="minutes either side of a positive to pull (default 10)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stderr)

    CACHE_ROOT = args.out_dir
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    cases = load_cases(args.cases, args.quick)
    log.info("cases: %d positives, %d quiet, %d SV.W  (%s)",
             len(cases["positives"]), len(cases["quiet_controls"]),
             len(cases["svw_controls"]), cases.get("source"))

    # ---- plan: which files does each case need? ----
    plan: List[Tuple[Dict[str, Any], str, datetime, str]] = []   # case, kind, valid, key
    session = requests.Session()
    for case in cases["positives"]:
        t0 = parse_iso_z(case["time"])
        try:
            found = mrms_fetch.s3_window(AZSHEAR_0_2,
                                         t0 - timedelta(minutes=args.window_min),
                                         t0 + timedelta(minutes=args.window_min),
                                         session=session)
        except Exception as e:                                        # noqa: BLE001
            log.error("listing failed for %s: %s", case["id"], e)
            continue
        if not found:
            log.warning("no archive files in the window for %s (%s)", case["id"], case["time"])
        for valid, key in found:
            plan.append((case, "positive", valid, key))
    for kind, group in (("quiet", "quiet_controls"), ("svw", "svw_controls")):
        for case in cases[group]:
            t0 = parse_iso_z(case["time"])
            try:
                hit = mrms_fetch.s3_nearest(AZSHEAR_0_2, t0,
                                            max_delta_s=CONTROL_MAX_DELTA_S, session=session)
            except Exception as e:                                    # noqa: BLE001
                log.error("listing failed for %s: %s", case["id"], e)
                continue
            if hit is None:
                log.warning("no file within %ds of %s (%s)",
                            CONTROL_MAX_DELTA_S, case["time"], case["id"])
                continue
            plan.append((case, kind, hit[0], hit[1]))

    if not plan:
        print("NO VERDICT: the archive could not be reached for any case", file=sys.stderr)
        return 2

    # ---- fetch ----
    fetched = prefetch([(AZSHEAR_0_2, key) for _, _, _, key in plan])

    # ---- decode + score ----
    scorer = Scorer()
    rows: List[Dict[str, Any]] = []
    failures = 0
    for i, (case, kind, valid, key) in enumerate(plan, 1):
        path = fetched.get(key)
        if path is None or not path.exists():
            failures += 1
            continue
        try:
            rows.append(scorer.score(path, case, kind, valid))
        except Exception as e:                                        # noqa: BLE001
            log.error("decode/score failed for %s (%s): %s", key, case["id"], e)
            failures += 1
        if i % 10 == 0 or i == len(plan):
            log.info("  scored %d/%d scans", i, len(plan))
    if not rows:
        print("NO VERDICT: every scan failed to decode", file=sys.stderr)
        return 2
    if failures:
        log.warning("%d/%d scans unusable (fetch or decode failure)", failures, len(plan))

    ev = evaluate(rows, cases)
    meta = {"run_utc": utc_iso(), "case_source": cases.get("source") or "built-in",
            "case_file_generated": cases.get("generated"),
            "n_scans": len(rows), "n_failed": failures,
            "n_unique_files": len({key for _, _, _, key in plan}),
            "thresholds": list(THRESHOLDS),
            "window_min": args.window_min,
            "domain_radius_km": DEFAULT_DOMAIN_RADIUS_KM,
            "exclusion_km": RADAR_EXCLUSION_KM,
            "tornado_radius_km": TORNADO_RADIUS_KM}

    report = render_report(ev, cases, meta)
    print()
    print(report)

    results = {
        "meta": meta,
        "scans": rows,
        "per_event": ev["per_event"],
        "quiet_max": ev["quiet_max"],
        "svw_max": ev["svw_max"],
        "S5": ev["S5"],
        "per_threshold": ev["per_threshold"],
        "eligible_thresholds": ev["eligible_thresholds"],
        "recommended_threshold": ev["recommended_threshold"],
        "recommended_pass": ev["recommended_pass"],
    }
    (CACHE_ROOT / "results.json").write_text(json.dumps(results, indent=2))
    (CACHE_ROOT / "replay_report.md").write_text(report + "\n")
    log.info("wrote %s and %s", CACHE_ROOT / "results.json", CACHE_ROOT / "replay_report.md")

    return 0 if ev["recommended_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
