#!/usr/bin/env python3
"""
Live MRMS rotation annotation service (docs/MRMS_MIGRATION.md §2.1, Phase 2).

Replaces the invalidated CNN as the annotation on NWS Tornado Warnings. Every
POLL_INTERVAL seconds:

  1. List the NCEP directory index for `MergedAzShear_0-2kmAGL` and take the
     newest valid time. If NCEP is unreachable, fall back to listing today's
     S3 prefix (and yesterday's, just after UTC midnight). `source` records
     which one answered.
  2. If that valid time is not newer than the one already processed, log
     "no new file" and return — the file is byte-identical, re-decoding it
     would burn ~1.2 GB of RSS for nothing. `last_score` is left untouched.
  3. Download, `decode_grib_gz` (already scaled to s^-1), `compute_readout`
     over the 100 km KFWS domain. The `DomainMask` is built ONCE on the first
     successful decode and reused: the MRMS CONUS grid never changes.
  4. If ROTATION_TRACK, repeat for `RotationTrack30min` at the nearest valid
     time within TRACK_MAX_DELTA_SEC -> `max_track30`. The two decodes are
     strictly serial and each field is dropped (`del` + `gc.collect()`) before
     the next is fetched — two 392 MB CONUS fields must never coexist.
  5. Atomically rewrite STATUS_PATH and append one line to ROTATION_LOG_PATH
     (month-rotated JSONL, same pattern as inference's SCORE_LOG_PATH). The
     status file only ever holds the LAST readout; the JSONL is the history.

Key names marked ★ in §2.1 (`status`, `last_score`, `last_score_time`,
`threshold`) deliberately match the inference status file so alerting's
`classify_model_state` needs no key changes at cutover.

CLI:
  python rotation_service.py            # service loop (Flask /health + bg poll)
  python rotation_service.py --once     # one synchronous cycle, JSON, exit 0/1

Never exits on a network failure: an unreachable NCEP is an `error` status with
the message in `errors`, and the loop keeps polling.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Shared modules live under bind-mounted dirs (see docker-compose volumes):
# /app (this dir, holding rotation_core + mrms_fetch) and /srv/reaped/common.
# Outside the container they resolve relative to this file instead.
_HERE = Path(__file__).resolve().parent
_parents = Path(__file__).resolve().parents
# In the container this file is /app/rotation_service.py (only two parents);
# the fallback is never used there, so don't let the index itself crash startup.
_REPO_ROOT = _parents[2] if len(_parents) > 2 else _parents[-1]
sys.path.insert(0, str(_HERE))
# `common` is a package, so its PARENT goes on the path.
sys.path.insert(0, "/srv/reaped" if Path("/srv/reaped/common").is_dir() else str(_REPO_ROOT))

import numpy as np                                                   # noqa: E402
import requests                                                      # noqa: E402
from flask import Flask, jsonify                                     # noqa: E402

import mrms_fetch                                                    # noqa: E402
from rotation_core import (DEFAULT_DOMAIN_RADIUS_KM, RADAR_EXCLUSION_KM,  # noqa: E402
                           DomainMask, compute_readout, decode_grib_gz)

from common import nws                                               # noqa: E402
from common.jsonlog import append_jsonl                              # noqa: E402
from common.oplog import setup_logging                               # noqa: E402
from common.places import DFW_PLACES                                 # noqa: E402
from common.status import atomic_write_json, deep_copy_json, utc_iso  # noqa: E402

log = logging.getLogger("rotation")


def _base_url(env_name: str, fallback: str) -> str:
    """A base URL from the environment, always with the trailing slash the
    mrms_fetch string concatenation assumes."""
    url = (os.environ.get(env_name) or "").strip() or fallback
    return url if url.endswith("/") else url + "/"


# ---- config from env (set in docker-compose.yml) -----------------------------
PORT             = int(os.environ.get("PORT", "9010"))
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "120"))          # 2 min cadence
MAX_SCORE_AGE    = int(os.environ.get("MAX_SCORE_AGE_SECONDS", "900"))  # tighter than the CNN's 1800
THRESHOLD        = float(os.environ.get("ROTATION_THRESHOLD", "0.015")) # s^-1, Phase 1 result
DOMAIN_RADIUS_KM = float(os.environ.get("DOMAIN_RADIUS_KM", str(DEFAULT_DOMAIN_RADIUS_KM)))
KFWS_LAT         = float(os.environ.get("KFWS_LAT", str(nws.KFWS_LAT)))
KFWS_LON         = float(os.environ.get("KFWS_LON", str(nws.KFWS_LON)))
STATUS_PATH      = Path(os.environ.get("STATUS_PATH", "/status/rotation_status.json"))
ROTATION_LOG_PATH = Path(os.environ.get("ROTATION_LOG_PATH", "/logs/rotation.jsonl"))
ROTATION_TRACK   = (os.environ.get("ROTATION_TRACK", "1").strip().lower()
                    not in ("0", "false", "no", "off", ""))
# NCEP has returned nothing newer for this long -> /health says `stale` even if
# the last score itself is younger than MAX_SCORE_AGE (Phase 5's feed alarm).
STALE_NO_NEW_FILE = int(os.environ.get("STALE_NO_NEW_FILE_SECONDS", "1200"))   # 20 min

# mrms_fetch reads these as module globals at call time, so pointing them at a
# mirror (or a test server) is a matter of rebinding them here.
NCEP_BASE_URL = _base_url("NCEP_BASE_URL", mrms_fetch.NCEP_BASE_URL)
S3_BASE_URL   = _base_url("S3_BASE_URL", mrms_fetch.S3_BASE_URL)
mrms_fetch.NCEP_BASE_URL = NCEP_BASE_URL
mrms_fetch.S3_BASE_URL = S3_BASE_URL

PRODUCT       = mrms_fetch.AZSHEAR_0_2
TRACK_PRODUCT = mrms_fetch.ROTATION_TRACK_30

CYCLE_DEADLINE_SEC   = 60.0
TRACK_MAX_DELTA_SEC  = 300      # RotationTrack30min must be within 5 min of the AzShear scan
S3_MIDNIGHT_WINDOW_M = 30       # minutes after UTC midnight where yesterday's prefix still matters

# One connection pool for the whole service. Only the poll thread uses it.
SESSION = requests.Session()


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


# ---- MRMS discovery + download (the seams the offline tests replace) ---------
def _near_utc_midnight(now: datetime) -> bool:
    return now.hour == 0 and now.minute < S3_MIDNIGHT_WINDOW_M


def list_files(product: str) -> Tuple[List[Tuple[datetime, str]], str]:
    """Every currently available file for `product` as (valid_time, ref), oldest
    first, plus the source that answered (`ncep` | `s3`).

    NCEP first (1-2 min behind valid time); S3 only when NCEP fails or lists
    nothing, because S3 costs a listing round trip per UTC day. Raises when
    neither source produces a file — the caller turns that into an `error`
    cycle, never a crash.
    """
    try:
        entries = mrms_fetch.ncep_list(product, session=SESSION)
        if entries:
            return entries, "ncep"
        raise RuntimeError(f"NCEP index for {product} listed no files")
    except Exception as e:                                            # noqa: BLE001
        log.warning("NCEP listing failed for %s (%s: %s) — falling back to S3",
                    product, type(e).__name__, e)

    now = datetime.now(timezone.utc)
    days = [now.date()]
    if _near_utc_midnight(now):
        # Just after 00:00Z today's prefix holds only a handful of files and the
        # newest scan may still be in yesterday's.
        days.append((now - timedelta(days=1)).date())
    entries = []
    for day in days:
        # use_cache=False: mrms_fetch memoises day listings for the backtest's
        # benefit. A long-lived service must never serve a cached listing of the
        # CURRENT day — it would freeze the feed at process start.
        entries.extend(mrms_fetch.s3_list(product, day, session=SESSION, use_cache=False))
    entries.sort()
    if not entries:
        raise RuntimeError(f"no {product} files from NCEP or S3")
    return entries, "s3"


def list_latest(product: str) -> Tuple[datetime, str, str]:
    """(newest valid time, ref, source) for `product`."""
    entries, source = list_files(product)
    valid, ref = entries[-1]
    return valid, ref, source


def fetch_bytes(product: str, ref: str, source: str) -> bytes:
    """Raw (gzip'd) bytes of one file. `ref` is a filename for NCEP, a key for S3."""
    if source == "s3":
        return mrms_fetch.s3_fetch(ref, session=SESSION)
    return mrms_fetch.ncep_fetch(product, ref, session=SESSION)


# ---- readout helpers ---------------------------------------------------------
def domain_max(values: np.ndarray, mask: DomainMask) -> float:
    """Positive max over the domain, 6 dp. Negative maxima are anticyclonic
    shear; the readout reports positive rotation only, so an all-negative domain
    reads 0.0 rather than a misleading minus sign."""
    domain = mask.crop(values)[mask.mask]
    if domain.size == 0:
        return 0.0
    return round(max(0.0, float(domain.max())), 6)


def _log_location(loc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The JSONL's trimmed max_location: where, not the exact pixel."""
    if not loc:
        return None
    return {"near": loc.get("near"), "km": loc.get("km"), "bearing": loc.get("bearing")}


def append_rotation_log(record: Dict[str, Any]) -> None:
    """One JSONL line per cycle, whatever the outcome. Never raises: a broken
    /logs mount must not stop the service from reading the radar."""
    try:
        append_jsonl(ROTATION_LOG_PATH, record)
    except Exception:                                     # pragma: no cover - defensive
        pass


# ---- service state -----------------------------------------------------------
class State:
    """`last_status` is guarded by `lock` (the Flask thread reads it). Everything
    else — the mask, the counters, the last valid time — is written only by the
    single poll thread (or the `--once` path)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.mask: Optional[DomainMask] = None
        self.last_valid_time: Optional[datetime] = None
        self.no_new_file_streak = 0
        self.fetch_failures = 0
        started = utc_iso()
        self.last_status: Dict[str, Any] = {
            "status": "uninitialized",
            "last_score": None,
            "last_score_time": None,
            "threshold": THRESHOLD,
            "product": PRODUCT,
            "product_valid_time": None,
            "fetched_at": None,
            "source": None,
            "max_location": None,
            "cells_ge_threshold": None,
            "coverage_nonzero_fraction": None,
            "max_track30": None,
            "top_cells": [],
            "domain_radius_km": DOMAIN_RADIUS_KM,
            "cycle_ms": {},
            "errors": [],
            "fetch_consecutive_failures": 0,
            "no_new_file_streak": 0,
            "last_new_file_time": None,
            "service_started": started,
        }

    # -- mask ---------------------------------------------------------------
    def ensure_mask(self, grid) -> DomainMask:
        """The 100 km domain, built once. The MRMS CONUS grid has been constant
        since 2020; if it ever changes, rebuild loudly rather than crop with a
        mask that no longer lines up."""
        if self.mask is not None and self.mask.grid == grid:
            return self.mask
        if self.mask is not None:
            log.warning("MRMS grid changed (%s -> %s) — rebuilding the domain mask",
                        self.mask.grid, grid)
        self.mask = DomainMask(grid, KFWS_LAT, KFWS_LON,
                               radius_km=DOMAIN_RADIUS_KM,
                               exclusion_km=RADAR_EXCLUSION_KM)
        log.info("domain: rows %d:%d cols %d:%d, %d cells within %.0f km of KFWS "
                 "(minus the %.0f km radar disc)",
                 self.mask.row0, self.mask.row1, self.mask.col0, self.mask.col1,
                 self.mask.n_cells, DOMAIN_RADIUS_KM, RADAR_EXCLUSION_KM)
        return self.mask

    # -- status -------------------------------------------------------------
    def record_error(self, kind, msg):
        with self.lock:
            errs = list(self.last_status.get("errors", []))
            errs.append({"time": utc_iso(), "kind": kind, "msg": str(msg)[:200]})
            self.last_status["errors"] = errs[-5:]

    def snapshot(self):
        with self.lock:
            return deep_copy_json(self.last_status)

    def commit(self, status):
        """Replace last_status and atomically write the JSON file."""
        with self.lock:
            self.last_status = status
            atomic_write_json(STATUS_PATH, status)


# ---- cycle -------------------------------------------------------------------
def _track_max(state: State, valid: datetime, timings: Dict[str, int]) -> Optional[float]:
    """Domain max of RotationTrack30min nearest `valid`, or None.

    A failure here is never a cycle failure: the 30-min track is context beside
    the instantaneous shear, not the number the annotation turns on.
    """
    try:
        t = time.time()
        entries, source = list_files(TRACK_PRODUCT)
        best = min(entries, key=lambda kv: abs((kv[0] - valid).total_seconds()))
        delta = abs((best[0] - valid).total_seconds())
        if delta > TRACK_MAX_DELTA_SEC:
            timings["fetch"] += _ms(t)
            log.info("no %s within %ds of %s (nearest %s, %.0fs away)",
                     TRACK_PRODUCT, TRACK_MAX_DELTA_SEC, utc_iso(valid),
                     utc_iso(best[0]), delta)
            return None
        blob = fetch_bytes(TRACK_PRODUCT, best[1], source)
        timings["fetch"] += _ms(t)

        t = time.time()
        values, grid = decode_grib_gz(blob)
        timings["decode"] += _ms(t)
        try:
            if state.mask is None or state.mask.grid != grid:
                state.record_error("track", f"{TRACK_PRODUCT} grid differs from {PRODUCT}'s")
                return None
            t = time.time()
            out = domain_max(values, state.mask)
            timings["compute"] += _ms(t)
            return out
        finally:
            del values
            gc.collect()
    except Exception as e:                                            # noqa: BLE001
        state.record_error("track", f"{type(e).__name__}: {e}")
        return None


def run_cycle(state: State) -> dict:
    t0 = time.time()
    timings = {"fetch": 0, "decode": 0, "compute": 0, "write": 0}
    # Bound up-front so the finally can always log, whatever went wrong.
    valid: Optional[datetime] = None
    source: Optional[str] = None
    readout: Optional[Dict[str, Any]] = None
    max_track30: Optional[float] = None
    cycle_error: Optional[Tuple[str, str]] = None
    status = state.snapshot()

    try:
        t = time.time()
        valid, ref, source = list_latest(PRODUCT)
        timings["fetch"] += _ms(t)

        # `<=`, not `==`: a listing that regresses (mirror lag, an S3 fallback
        # behind NCEP) must never make us reprocess an older field and rewrite
        # last_score_time backwards.
        if state.last_valid_time is not None and valid <= state.last_valid_time:
            state.no_new_file_streak += 1
            state.fetch_failures = 0
            log.info("no new file (valid=%s src=%s streak=%d)",
                     utc_iso(valid), source, state.no_new_file_streak)
            status = state.snapshot()
            status.update({
                # The feed answered; whether the newest file is too old for the
                # annotation is /health's staleness call, not this cycle's.
                "status": "running" if status.get("last_score_time") else "uninitialized",
                "source": source,
                "fetched_at": utc_iso(),
                "fetch_consecutive_failures": 0,
                "no_new_file_streak": state.no_new_file_streak,
                "cycle_ms": timings,
            })
            state.commit(status)
            return status

        t = time.time()
        blob = fetch_bytes(PRODUCT, ref, source)
        timings["fetch"] += _ms(t)

        t = time.time()
        values, grid = decode_grib_gz(blob)
        timings["decode"] += _ms(t)
        del blob
        try:
            mask = state.ensure_mask(grid)
            t = time.time()
            readout = compute_readout(values, mask, THRESHOLD, places=DFW_PLACES)
            timings["compute"] += _ms(t)
        finally:
            # Never hold two CONUS fields: this one goes before the track fetch.
            del values
            gc.collect()

        if ROTATION_TRACK:
            max_track30 = _track_max(state, valid, timings)

        t = time.time()
        now = datetime.now(timezone.utc)
        status = state.snapshot()
        status.update({
            "status": "running",
            # Positive max only — anticyclonic shear is not what this annotates.
            "last_score": round(max(0.0, float(readout["max_azshear"])), 4),
            "last_score_time": utc_iso(valid),      # the PRODUCT's valid time, not now
            "threshold": THRESHOLD,
            "product": PRODUCT,
            "product_valid_time": utc_iso(valid),
            "fetched_at": utc_iso(now),
            "source": source,
            "max_location": readout["max_location"],
            "cells_ge_threshold": readout["cells_ge_threshold"],
            "coverage_nonzero_fraction": readout["coverage_nonzero_fraction"],
            "max_track30": max_track30,
            "top_cells": readout["top_cells"],
            "domain_radius_km": DOMAIN_RADIUS_KM,
            "fetch_consecutive_failures": 0,
            "no_new_file_streak": 0,
            "last_new_file_time": utc_iso(now),
        })
        state.commit(status)
        timings["write"] = _ms(t)
        status["cycle_ms"] = timings
        state.commit(status)

        state.last_valid_time = valid
        state.no_new_file_streak = 0
        state.fetch_failures = 0
        return status
    except Exception as e:                                            # noqa: BLE001
        state.fetch_failures += 1
        cycle_error = ("cycle", f"{type(e).__name__}: {e}")
        state.record_error(*cycle_error)
        status = state.snapshot()
        status["status"] = "error"
        status["fetch_consecutive_failures"] = state.fetch_failures
        status["cycle_ms"] = timings
        state.commit(status)
        return status
    finally:
        elapsed = time.time() - t0
        if elapsed > CYCLE_DEADLINE_SEC:
            state.record_error("deadline", f"cycle {elapsed:.1f}s > {CYCLE_DEADLINE_SEC}s")
        _log_cycle(state, status, timings, valid, source, readout, max_track30,
                   cycle_error, elapsed)


def _log_cycle(state: State, status: Dict[str, Any], timings: Dict[str, int],
               valid, source, readout, max_track30, cycle_error, elapsed: float) -> None:
    """Append the JSONL row and emit the one human-readable INFO line.

    `readout` is None for a no-new-file or failed cycle, and every derived field
    is then null: `last_score` lingers in the status file by design, and copying
    it into the history would fabricate a reading for a scan that never happened.
    """
    try:
        record = {
            "ts":                        utc_iso(),
            "status":                    status.get("status"),
            "valid_time":                utc_iso(valid) if valid else None,
            "source":                    source,
            "max_azshear":               status.get("last_score") if readout else None,
            "max_track30":               max_track30 if readout else None,
            "cells_ge_threshold":        readout["cells_ge_threshold"] if readout else None,
            "coverage_nonzero_fraction": readout["coverage_nonzero_fraction"] if readout else None,
            "max_location":              _log_location(readout["max_location"]) if readout else None,
            "fetch_ms":                  timings.get("fetch"),
            "decode_ms":                 timings.get("decode"),
            "cycle_ms":                  int(elapsed * 1000),
            "error":                     cycle_error[0] if cycle_error else None,
            "error_msg":                 cycle_error[1] if cycle_error else None,
            "threshold":                 THRESHOLD,
        }
    except Exception:                                     # pragma: no cover - defensive
        return
    append_rotation_log(record)

    if cycle_error:
        log.warning("cycle status=%s error=%s cycle=%dms",
                    record["status"], record["error_msg"], record["cycle_ms"])
    elif readout:
        loc = record["max_location"] or {}
        near = ("%s %sm %s" % (loc.get("near"), loc.get("km"), loc.get("bearing"))
                if loc.get("near") else "-")
        log.info("cycle ok valid=%s max=%.4f near=%s track30=%s cov=%.4f src=%s %.1fs",
                 record["valid_time"], record["max_azshear"] or 0.0, near,
                 ("%.4f" % max_track30) if max_track30 is not None else "n/a",
                 record["coverage_nonzero_fraction"] or 0.0, source, elapsed)


# ---- service loop + Flask /health -------------------------------------------
def poll_loop(state):
    while True:
        try:
            run_cycle(state)
        except Exception as e:                                        # noqa: BLE001
            state.record_error("loop", f"{type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)


def _age_seconds(iso: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    if not iso:
        return None
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:                                    # pragma: no cover - defensive
        return None
    return int(((now or datetime.now(timezone.utc)) - t).total_seconds())


def health_payload(state: State) -> Dict[str, Any]:
    """The /health body: the status file plus the two ages that only make sense
    relative to now. `stale` never overwrites `error` — a cycle that raised is
    the more urgent fact."""
    s = state.snapshot()
    age = _age_seconds(s.get("last_score_time"))
    feed_age = _age_seconds(s.get("last_new_file_time"))
    s["score_age_seconds"] = age
    s["no_new_file_seconds"] = feed_age
    if s.get("status") == "running":
        if (age is not None and age > MAX_SCORE_AGE) or \
           (feed_age is not None and feed_age > STALE_NO_NEW_FILE):
            s["status"] = "stale"
    return s


def make_app(state: State) -> Flask:
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify(health_payload(state))

    return app


# ---- entrypoint --------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="run one cycle synchronously, print status JSON, exit")
    args = ap.parse_args()

    setup_logging()
    log.info("rotation starting: product=%s threshold=%s s^-1 domain=%.0fkm poll=%ss "
             "track=%s ncep=%s", PRODUCT, THRESHOLD, DOMAIN_RADIUS_KM, POLL_INTERVAL,
             ROTATION_TRACK, NCEP_BASE_URL)
    state = State()
    state.commit(state.last_status)   # writes the initial "uninitialized" status

    if args.once:
        status = run_cycle(state)
        print(json.dumps(status, indent=2))
        sys.exit(0 if status.get("status") == "running" else 1)

    app = make_app(state)
    t = threading.Thread(target=poll_loop, args=(state,), daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
