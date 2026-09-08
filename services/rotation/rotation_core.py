#!/usr/bin/env python3
"""
Pure core of the MRMS rotation annotation (docs/MRMS_MIGRATION.md Phase 1).

No Flask, no threads, no module-level mutable state, no network: every function
here is a pure transform over bytes/arrays so the service (Phase 2) and the
backtest (`replay.py`) run the *same* code and cannot drift apart. Network I/O
lives in `mrms_fetch.py`.

What the grid actually looks like (verified in the Phase 0 spike, see
docs/MRMS_MIGRATION.md "Results / Phase 0" — all three MRMS 2D products share it):

    Ni 14000 x Nj 7000, first point (54.9975, 230.0025), last (20.0025, 299.9975),
    0.005 deg both axes, jScansPositively false.

Three consequences are baked in here rather than rediscovered per caller:

  1. **Longitudes are 0-360.** KFWS's -97.3031 must become 262.6969 before it
     indexes a column, and a grid longitude must go back to -97.3031 before it
     is printed or fed to a bearing. `latlon_to_rowcol` / `rowcol_to_latlon` do
     this; nothing else should touch the convention.
  2. **Row 0 is the NORTH edge** (dlat is negative). Never assume ascending lat.
  3. **Units are hardcoded.** discipline 209 is an NSSL local-use table that
     eccodes has no definitions for, so it reports shortName/units as
     "unknown". Raw values are integers-as-floats in 0.001 s^-1; `decode_grib_gz`
     applies the 0.001 scale so every array downstream is already in s^-1.

Memory: a full CONUS field is 14000*7000 = 98e6 cells. eccodes hands back
float64 (784 MB) and we immediately narrow to float32 (392 MB), so peak decode
RSS is ~1.2 GB -- see the note in `decode_grib_gz`.
"""
from __future__ import annotations

import gzip
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# `common` is a sibling package of `services/`; the service and the replay both
# put the repo root on sys.path before importing this module.
from common.places import nearest_place

log = logging.getLogger("rotation.core")

EARTH_R_KM = 6371.0088

# GRIB2 raw values are in 0.001 s^-1. Hardcoded because eccodes cannot read the
# NSSL local table (see module docstring).
AZSHEAR_SCALE = 0.001

# LLSD azimuthal shear "breaks down within 5 km of a radar site" (NOAA WDTD).
RADAR_EXCLUSION_KM = 5.0
DEFAULT_DOMAIN_RADIUS_KM = 100.0

COMPASS_16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
COMPASS_8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points (degrees, -180..180)."""
    lat1r, lon1r, lat2r, lon2r = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return 2.0 * EARTH_R_KM * math.asin(math.sqrt(min(1.0, a)))


def _haversine_km_grid(lat0: float, lon0: float,
                       lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorised haversine from one point to a 2-D lat/lon mesh (km)."""
    lat0r, lon0r = math.radians(lat0), math.radians(lon0)
    latr = np.radians(lats)
    lonr = np.radians(lons)
    a = (np.sin((latr - lat0r) / 2.0) ** 2
         + math.cos(lat0r) * np.cos(latr) * np.sin((lonr - lon0r) / 2.0) ** 2)
    return 2.0 * EARTH_R_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise
    from true north in [0, 360)."""
    lat1r, lon1r, lat2r, lon2r = map(math.radians, (lat1, lon1, lat2, lon2))
    dlon = lon2r - lon1r
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def compass16(deg: float) -> str:
    """16-point compass name for a bearing in degrees ('SSW')."""
    return COMPASS_16[int(round(deg / 22.5)) % 16]


def compass8(deg: float) -> str:
    """8-point compass name for a bearing in degrees ('SW')."""
    return COMPASS_8[int(round(deg / 45.0)) % 8]


# ---------------------------------------------------------------------------
# grid
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GridSpec:
    """A regular lat/lon GRIB2 grid.

    `dlat`/`dlon` are SIGNED per-index steps derived from first/last grid point
    (not from iDirectionIncrementInDegrees, which is unsigned), so
    `lat = lat0 + row*dlat` holds without a scan-order special case.
    `rows_north_first` is True when row 0 is the north edge (dlat < 0), which is
    what MRMS does.
    """
    ni: int
    nj: int
    lat0: float
    lon0: float
    dlat: float
    dlon: float
    rows_north_first: bool

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.nj, self.ni)

    @property
    def lon_is_0_360(self) -> bool:
        """True when the grid numbers longitudes 0..360 (MRMS CONUS does)."""
        return self.lon0 > 180.0 or (self.lon0 + self.dlon * (self.ni - 1)) > 180.0


def to_grid_lon(spec: GridSpec, lon: float) -> float:
    """A -180..180 longitude in the grid's own convention (-97.3031 -> 262.6969)."""
    if spec.lon_is_0_360:
        return lon + 360.0 if lon < 0.0 else lon
    return lon - 360.0 if lon > 180.0 else lon


def to_report_lon(lon: float) -> float:
    """A grid longitude back in the reporting convention (262.6969 -> -97.3031)."""
    return lon - 360.0 if lon > 180.0 else lon


def latlon_to_rowcol(spec: GridSpec, lat: float, lon: float) -> Tuple[float, float]:
    """(lat, lon) -> continuous (row, col). `lon` is -180..180; the 0-360
    normalisation is applied here. Values may be fractional or out of range —
    callers clamp/round as they need."""
    glon = to_grid_lon(spec, lon)
    row = (lat - spec.lat0) / spec.dlat
    col = (glon - spec.lon0) / spec.dlon
    return row, col


def rowcol_to_latlon(spec: GridSpec, row: float, col: float) -> Tuple[float, float]:
    """(row, col) -> (lat, lon) with lon in the -180..180 reporting convention."""
    lat = spec.lat0 + row * spec.dlat
    lon = to_report_lon(spec.lon0 + col * spec.dlon)
    return lat, lon


def grid_from_grib(msg_handle: Any) -> GridSpec:
    """Read a GridSpec off an open eccodes message handle (gid)."""
    import eccodes  # lazy: the pure functions above must import without it

    get = eccodes.codes_get
    ni = int(get(msg_handle, "Ni"))
    nj = int(get(msg_handle, "Nj"))
    lat0 = float(get(msg_handle, "latitudeOfFirstGridPointInDegrees"))
    lon0 = float(get(msg_handle, "longitudeOfFirstGridPointInDegrees"))
    lat1 = float(get(msg_handle, "latitudeOfLastGridPointInDegrees"))
    lon1 = float(get(msg_handle, "longitudeOfLastGridPointInDegrees"))
    dlat = (lat1 - lat0) / (nj - 1) if nj > 1 else 0.0
    dlon = (lon1 - lon0) / (ni - 1) if ni > 1 else 0.0
    try:
        j_positive = bool(int(get(msg_handle, "jScansPositively")))
    except Exception:                                        # pragma: no cover
        # GRIB2 flag table 3.4: bit 2 (0x40) is "j direction positive".
        j_positive = bool(int(get(msg_handle, "scanningMode")) & 0x40)
    rows_north_first = dlat < 0.0
    if rows_north_first == j_positive:                       # pragma: no cover
        log.warning("scan order disagrees with first/last latitudes "
                    "(jScansPositively=%s, dlat=%s) — trusting the latitudes",
                    j_positive, dlat)
    return GridSpec(ni=ni, nj=nj, lat0=lat0, lon0=lon0, dlat=dlat, dlon=dlon,
                    rows_north_first=rows_north_first)


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------
def _as_bytes(path_or_bytes) -> bytes:
    if isinstance(path_or_bytes, (bytes, bytearray, memoryview)):
        return bytes(path_or_bytes)
    return Path(path_or_bytes).read_bytes()


def decode_grib_gz(path_or_bytes) -> Tuple[np.ndarray, GridSpec]:
    """Decode a (optionally gzip'd) single-field MRMS GRIB2 into
    `(values, spec)` where `values` is float32 [nj, ni] **already scaled to
    s^-1** and `spec` describes the grid.

    Accepts a path or raw bytes. gzip is detected by magic number, so a plain
    .grib2 works too.

    Two eccodes facts drive the shape of this:
      * `codes_grib_new_from_file` calls fileno() on the handle, so an
        io.BytesIO raises io.UnsupportedOperation — the bytes are spilled to a
        tempfile (Phase 0 finding #3).
      * `codes_get_values` returns float64. For CONUS that is 784 MB, narrowed
        immediately to float32 (392 MB) and the float64 dropped, so peak RSS is
        ~1.2 GB for a beat. There is no partial-field read in eccodes; the grid
        cannot be cropped before decode.
    """
    raw = _as_bytes(path_or_bytes)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if raw[:4] != b"GRIB":
        raise ValueError("not a GRIB2 message (no 'GRIB' magic after gunzip)")

    import eccodes  # lazy: keeps the pure functions importable without eccodes

    fd, tmp = tempfile.mkstemp(suffix=".grib2")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        del raw
        with open(tmp, "rb") as fh:
            gid = eccodes.codes_grib_new_from_file(fh)
            if gid is None:
                raise ValueError("eccodes found no GRIB message in the file")
            try:
                spec = grid_from_grib(gid)
                raw_values = eccodes.codes_get_values(gid)
            finally:
                eccodes.codes_release(gid)
    finally:
        try:
            os.unlink(tmp)
        except OSError:                                      # pragma: no cover
            pass

    expected = spec.ni * spec.nj
    if raw_values.size != expected:
        raise ValueError(f"value count {raw_values.size} != Ni*Nj {expected}")
    values = np.asarray(raw_values, dtype=np.float32).reshape(spec.nj, spec.ni)
    del raw_values
    values *= AZSHEAR_SCALE
    return values, spec


# ---------------------------------------------------------------------------
# domain
# ---------------------------------------------------------------------------
class DomainMask:
    """The set of grid cells we ever look at: a disc of `radius_km` around a
    centre, minus an `exclusion_km` disc around the same point (the radar's own
    LLSD breakdown zone).

    Built once — at service startup, or once per replay run — and reused for
    every scan, because the grid never changes. Holds:

      * `row0:row1, col0:col1` — a half-open bounding-box slice into the full grid
      * `lats`, `lons` — 1-D coordinate vectors for the sub-grid (lons in the
        -180..180 reporting convention). No full-grid meshgrid is ever built;
        the only 2-D arrays are sub-grid sized (~500x600 for a 100 km disc).
      * `mask` — bool [nrows, ncols], the disc minus the exclusion
      * `dist_km` — distance from the centre for every sub-grid cell
    """

    def __init__(self, grid: GridSpec, center_lat: float, center_lon: float,
                 radius_km: float = DEFAULT_DOMAIN_RADIUS_KM,
                 exclusion_km: float = RADAR_EXCLUSION_KM):
        self.grid = grid
        self.center_lat = float(center_lat)
        self.center_lon = float(center_lon)
        self.radius_km = float(radius_km)
        self.exclusion_km = float(exclusion_km)

        # Bounding box with 15 % padding: the disc's corners in degrees, then
        # to row/col. Padding covers the difference between the flat-earth box
        # and the true haversine disc at these latitudes.
        pad = 1.15
        dlat_deg = self.radius_km / 111.0 * pad
        coslat = max(0.05, math.cos(math.radians(self.center_lat)))
        dlon_deg = self.radius_km / (111.0 * coslat) * pad

        r_a, c_a = latlon_to_rowcol(grid, self.center_lat - dlat_deg, self.center_lon - dlon_deg)
        r_b, c_b = latlon_to_rowcol(grid, self.center_lat + dlat_deg, self.center_lon + dlon_deg)
        row0 = max(0, int(math.floor(min(r_a, r_b))))
        row1 = min(grid.nj, int(math.ceil(max(r_a, r_b))) + 1)
        col0 = max(0, int(math.floor(min(c_a, c_b))))
        col1 = min(grid.ni, int(math.ceil(max(c_a, c_b))) + 1)
        self.row0, self.row1 = row0, max(row0, row1)
        self.col0, self.col1 = col0, max(col0, col1)

        rows = np.arange(self.row0, self.row1)
        cols = np.arange(self.col0, self.col1)
        self.lats = grid.lat0 + rows * grid.dlat
        raw_lons = grid.lon0 + cols * grid.dlon
        self.lons = np.where(raw_lons > 180.0, raw_lons - 360.0, raw_lons)

        lat_mesh, lon_mesh = np.meshgrid(self.lats, self.lons, indexing="ij")
        self._lat_mesh = lat_mesh
        self._lon_mesh = lon_mesh
        self.dist_km = _haversine_km_grid(self.center_lat, self.center_lon,
                                          lat_mesh, lon_mesh)
        self.mask = self.dist_km <= self.radius_km
        if self.exclusion_km > 0:
            self.mask &= self.dist_km > self.exclusion_km
        self.n_cells = int(np.count_nonzero(self.mask))

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.row1 - self.row0, self.col1 - self.col0)

    def crop(self, values: np.ndarray) -> np.ndarray:
        """The bounding-box sub-array of a full-grid array (a view, not a copy)."""
        if values.shape != self.grid.shape:
            raise ValueError(f"values shape {values.shape} != grid shape {self.grid.shape}")
        return values[self.row0:self.row1, self.col0:self.col1]

    def distance_to(self, lat: float, lon: float) -> np.ndarray:
        """Distance in km from an arbitrary point to every sub-grid cell.

        Used by the backtest to score "within 25 km of the tornado" without
        building a second, differently-shaped mask.
        """
        return _haversine_km_grid(lat, lon, self._lat_mesh, self._lon_mesh)

    def latlon_at(self, sub_row: int, sub_col: int) -> Tuple[float, float]:
        """(lat, lon) of a sub-grid index, reporting convention."""
        return float(self.lats[sub_row]), float(self.lons[sub_col])


# ---------------------------------------------------------------------------
# readout
# ---------------------------------------------------------------------------
def _cell(lat: float, lon: float, center_lat: float, center_lon: float,
          places: Optional[Sequence[Tuple[str, float, float]]],
          value: Optional[float] = None, km_key: str = "km") -> Dict[str, Any]:
    """One reported grid cell. `km_key` is "km" in max_location and
    "km_from_kfws" in top_cells — the two field names §2.1 step 4 spells out."""
    brng = bearing_deg(center_lat, center_lon, lat, lon)
    near = None
    if places:
        near = nearest_place(lat, lon, places)
    out: Dict[str, Any] = {"lat": round(lat, 4), "lon": round(lon, 4)}
    if value is not None:
        out["value"] = round(float(value), 6)
    out[km_key] = round(haversine_km(center_lat, center_lon, lat, lon), 1)
    out["bearing"] = compass16(brng)
    out["near"] = near
    return out


def compute_readout(values: np.ndarray, mask: DomainMask, threshold: float, *,
                    top_n: int = 5, dedupe_km: float = 10.0,
                    places: Optional[Sequence[Tuple[str, float, float]]] = None
                    ) -> Dict[str, Any]:
    """The numeric half of the status file (docs/MRMS_MIGRATION.md §2.1 step 4).

    `values` is a full-grid float32 array already in s^-1 (i.e. straight out of
    `decode_grib_gz`); `mask` selects the domain. Returns:

        max_azshear                 float, s^-1, 0.0 when the domain is empty or
                                    holds nothing positive
        max_location                {lat, lon, km, bearing, near} or None
        cells_ge_threshold          int
        coverage_nonzero_fraction   fraction of domain cells != 0. 0.0 is
                                    AMBIGUOUS — calm sky and a radar outage look
                                    identical in this product; the caller words
                                    it honestly rather than guessing.
        top_cells                   up to `top_n` cells >= 0.5*threshold, picked
                                    greedily strongest-first and suppressing
                                    everything within `dedupe_km` of an
                                    already-picked cell, so five cells means five
                                    distinct storms, not five pixels of one.

    `bearing` is the 16-point compass name (what the readout sentence needs);
    call `bearing_deg()` directly if you want degrees.
    """
    sub = mask.crop(values)
    sel = mask.mask
    n_domain = int(sel.sum())

    readout: Dict[str, Any] = {
        "max_azshear": 0.0,
        "max_location": None,
        "cells_ge_threshold": 0,
        "coverage_nonzero_fraction": 0.0,
        "top_cells": [],
    }
    if n_domain == 0:
        return readout

    domain = sub[sel]
    # 8 dp, not 4: a 100 km domain is ~120k cells, so a single nonzero cell is
    # 8e-6 of it and coarser rounding would report "exactly zero coverage" for a
    # field that is not empty — the one number the all-zero diagnostic turns on.
    readout["coverage_nonzero_fraction"] = round(
        float(np.count_nonzero(domain)) / n_domain, 8)
    readout["cells_ge_threshold"] = int(np.count_nonzero(domain >= threshold))
    max_val = float(domain.max())
    readout["max_azshear"] = round(max_val, 6)

    # An all-zero (or all-negative) domain has no meaningful location. Reporting
    # the first cell of the argmax would be a lie dressed as a coordinate.
    if max_val <= 0.0:
        return readout

    masked = np.where(sel, sub, -np.inf)
    r, c = np.unravel_index(int(np.argmax(masked)), masked.shape)
    lat, lon = mask.latlon_at(int(r), int(c))
    readout["max_location"] = _cell(lat, lon, mask.center_lat, mask.center_lon, places)

    # top_cells: greedy peak picking with a distance veto.
    cand = sel & (sub >= 0.5 * threshold) & (sub > 0.0)
    rows, cols = np.nonzero(cand)
    if rows.size:
        vals = np.asarray(sub[rows, cols], dtype=np.float64)
        clats = mask.lats[rows]
        clons = mask.lons[cols]
        alive = np.ones(vals.size, dtype=bool)
        picked: List[Dict[str, Any]] = []
        for _ in range(top_n):
            if not alive.any():
                break
            i = int(np.argmax(np.where(alive, vals, -np.inf)))
            plat, plon = float(clats[i]), float(clons[i])
            picked.append(_cell(plat, plon, mask.center_lat, mask.center_lon,
                                places, value=vals[i], km_key="km_from_kfws"))
            if dedupe_km > 0:
                alive &= _haversine_km_grid(plat, plon, clats, clons) > dedupe_km
            else:
                alive[i] = False
        readout["top_cells"] = picked
    return readout
