"""Shared IEM RIDGE helpers — used by both the historical collector
(`data-tools/collect.py`) and the live inference service
(`services/inference/inference_service.py`).

Single source of truth for:
  - the IEM archive URL template + product constants
  - PNG / text HTTP fetchers with magic-byte validation
  - per-file on-disk validation (catches truncated PNGs from prior crashes)
  - time-pairing for matching N0B (reflectivity) ↔ N0S (storm-relative velocity)
    scans whose archive cadences don't line up exactly.

If you change anything here, both consumers see it — that's the point.
"""
import re
from datetime import datetime
from pathlib import Path
import requests

# IEM RIDGE archive base URL. Format vars: {y}=year, {m:02d}=month, {d:02d}=day,
# {s}=station id WITHOUT leading K (e.g. "FWS" for KFWS), {prod}=N0B|N0S|...
ARCH = "https://mesonet.agron.iastate.edu/archive/data/{y}/{m:02d}/{d:02d}/GIS/ridge/{s}/{prod}/"

REFL_PROD = "N0B"   # super-resolution base reflectivity (consistent 2020+)
VEL_PROD  = "N0S"   # storm-relative velocity (16-level)


def list_times(s, prod, day):
    """Scrape an IEM archive directory listing for {station}_{prod}_*.png files.
    Returns {datetime: filename}. Returns {} on HTTP error (caller handles)."""
    try:
        txt = requests.get(
            ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=prod),
            timeout=30,
        ).text
    except requests.RequestException:
        return {}
    out = {}
    for fn in re.findall(rf'{s}_{prod}_(\d{{12}})\.png', txt):
        try:
            out[datetime.strptime(fn, "%Y%m%d%H%M")] = f"{s}_{prod}_{fn}.png"
        except ValueError:
            pass
    return out


def nearest(times, target, tol_min):
    """Pick the closest time in `times` to `target` within `tol_min` minutes.
    Returns None if no available time falls within the tolerance."""
    if not times:
        return None
    dt = min(times, key=lambda t: abs((t - target).total_seconds()))
    return dt if abs((dt - target).total_seconds()) <= tol_min * 60 else None


def fetch_png(url, timeout=30):
    """Fetch a PNG. Returns the bytes iff HTTP 200 + PNG magic-bytes + non-trivial size."""
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200 and r.content[:4] == b"\x89PNG" and len(r.content) > 200:
            return r.content
    except requests.RequestException:
        pass
    return None


def fetch_text(url, timeout=20):
    """Fetch text (e.g. a .wld georeference). Returns the text iff HTTP 200; else None."""
    try:
        r = requests.get(url, timeout=timeout)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def validate_png_on_disk(path: Path) -> bool:
    """Magic-byte + size check for a PNG already written to disk.
    Catches truncated files from prior crashes / partial writes."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
        return head[:4] == b"\x89PNG" and path.stat().st_size > 200
    except OSError:
        return False
