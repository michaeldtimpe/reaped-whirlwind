"""Offline tests for services/rotation/rotation_core.py.

Everything here runs without network and without eccodes: `decode_grib_gz` is
the only function that touches eccodes and it imports it lazily, so the grid
maths, the domain mask and the readout are testable on a machine that has no
GRIB library at all. One optional test decodes a real Phase 0 spike file when
`scratch/mrms-spike/cache/` happens to be present.

The grid facts asserted here are the ones the Phase 0 spike verified against
live files (docs/MRMS_MIGRATION.md "Results / Phase 0"); if MRMS ever changes
the CONUS grid these are the tests that should go red first.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "services" / "rotation"))

from common.places import DFW_PLACES, nearest_place          # noqa: E402
from rotation_core import (AZSHEAR_SCALE, DomainMask, GridSpec,  # noqa: E402
                           bearing_deg, compass8, compass16, compute_readout,
                           decode_grib_gz, haversine_km, latlon_to_rowcol,
                           rowcol_to_latlon, to_grid_lon, to_report_lon)

KFWS_LAT, KFWS_LON = 32.5728, -97.3031

# The real MRMS CONUS grid, as decoded from live files in Phase 0.
MRMS = GridSpec(ni=14000, nj=7000, lat0=54.9975, lon0=230.0025,
                dlat=(20.002501 - 54.9975) / 6999,
                dlon=(299.997498 - 230.0025) / 13999,
                rows_north_first=True)


# ---------------------------------------------------------------------------
# longitude convention (Phase 0 finding #1 — the bug that cost the spike a run)
# ---------------------------------------------------------------------------
def test_grid_is_0_360_and_rows_run_north_to_south():
    assert MRMS.lon_is_0_360 is True
    assert MRMS.dlat < 0, "row 0 must be the NORTH edge"
    assert MRMS.rows_north_first is True
    assert MRMS.dlon > 0


def test_longitude_normalises_both_ways():
    assert to_grid_lon(MRMS, KFWS_LON) == pytest.approx(262.6969, abs=1e-9)
    assert to_report_lon(262.6969) == pytest.approx(KFWS_LON, abs=1e-9)
    # already in the right convention -> unchanged
    assert to_grid_lon(MRMS, 262.6969) == pytest.approx(262.6969)
    assert to_report_lon(-97.3031) == pytest.approx(-97.3031)


def test_latlon_to_rowcol_round_trips_on_the_real_grid():
    row, col = latlon_to_rowcol(MRMS, KFWS_LAT, KFWS_LON)
    # KFWS is well inside CONUS
    assert 0 < row < MRMS.nj
    assert 0 < col < MRMS.ni
    lat, lon = rowcol_to_latlon(MRMS, row, col)
    assert lat == pytest.approx(KFWS_LAT, abs=1e-9)
    assert lon == pytest.approx(KFWS_LON, abs=1e-9)   # reported back as negative


def test_row_zero_is_the_north_edge():
    lat_north, _ = rowcol_to_latlon(MRMS, 0, 0)
    lat_south, _ = rowcol_to_latlon(MRMS, MRMS.nj - 1, 0)
    assert lat_north == pytest.approx(54.9975)
    assert lat_south == pytest.approx(20.002501, abs=1e-6)
    assert lat_north > lat_south
    # and a point further north maps to a SMALLER row
    r_north, _ = latlon_to_rowcol(MRMS, 40.0, KFWS_LON)
    r_south, _ = latlon_to_rowcol(MRMS, 30.0, KFWS_LON)
    assert r_north < r_south


def test_rowcol_matches_the_documented_0_005_degree_step():
    lat_a, lon_a = rowcol_to_latlon(MRMS, 100, 200)
    lat_b, lon_b = rowcol_to_latlon(MRMS, 101, 201)
    assert lat_a - lat_b == pytest.approx(0.005, abs=1e-6)
    assert lon_b - lon_a == pytest.approx(0.005, abs=1e-6)


# ---------------------------------------------------------------------------
# bearings
# ---------------------------------------------------------------------------
def test_bearing_and_compass():
    assert bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=1e-6)     # due N
    assert bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=1e-6)    # due E
    assert bearing_deg(0.0, 0.0, -1.0, 0.0) == pytest.approx(180.0, abs=1e-6)  # due S
    assert compass16(0) == "N" and compass16(202.5) == "SSW" and compass16(359) == "N"
    assert compass8(225) == "SW" and compass8(90) == "E"
    # Burleson is SSW of KFWS — the example sentence in docs/MRMS_MIGRATION.md §2.2
    assert compass16(bearing_deg(KFWS_LAT, KFWS_LON, 32.542, -97.321)) in ("SSW", "S", "SW")


# ---------------------------------------------------------------------------
# domain mask
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def small_grid():
    """A 0.005-deg grid covering ~+/-1.2 deg around KFWS, north-first and 0-360,
    i.e. the real grid's geometry at 1/500th the size."""
    ni, nj = 480, 480
    lat0 = KFWS_LAT + 1.2
    lon0 = to_grid_lon(MRMS, KFWS_LON) - 1.2
    return GridSpec(ni=ni, nj=nj, lat0=lat0, lon0=lon0,
                    dlat=-0.005, dlon=0.005, rows_north_first=True)


@pytest.fixture(scope="module")
def mask(small_grid):
    return DomainMask(small_grid, KFWS_LAT, KFWS_LON, radius_km=100.0, exclusion_km=5.0)


def test_mask_excludes_the_radar_disc_and_keeps_a_50km_cell(mask):
    assert mask.n_cells > 0
    # the centre itself is inside the 5 km exclusion
    d = mask.dist_km
    assert not mask.mask[np.unravel_index(int(np.argmin(d)), d.shape)]
    assert d.min() < 5.0, "the sub-grid must actually contain the centre"
    # every unmasked cell is in the annulus
    kept = d[mask.mask]
    assert kept.min() > 5.0
    assert kept.max() <= 100.0
    # a point 50 km due north of KFWS is inside
    lat50 = KFWS_LAT + 50.0 / 111.0
    r, c = latlon_to_rowcol(mask.grid, lat50, KFWS_LON)
    sr, sc = int(round(r)) - mask.row0, int(round(c)) - mask.col0
    assert mask.mask[sr, sc]
    assert haversine_km(KFWS_LAT, KFWS_LON, *mask.latlon_at(sr, sc)) == pytest.approx(50, abs=1.0)
    # and a point 150 km away is outside the disc (it may be outside the bbox too)
    assert not mask.mask[0, 0]


def test_burleson_sits_inside_the_radar_exclusion_disc():
    """KFWS is ~3.8 km from Burleson's town centre, so the 5 km exclusion disc
    swallows the town. Not a bug — but it means "near Burleson" can only ever
    come from a cell on the far side of town, and any test fixture that pokes a
    cell "at Burleson" is poking a cell the mask throws away."""
    assert haversine_km(KFWS_LAT, KFWS_LON, 32.542, -97.321) < 5.0


def test_mask_lons_are_reported_negative(mask):
    assert mask.lons.max() < 0
    assert mask.lons.min() > -180


def test_mask_crop_rejects_a_wrongly_shaped_array(mask):
    with pytest.raises(ValueError):
        mask.crop(np.zeros((10, 10), dtype=np.float32))


# ---------------------------------------------------------------------------
# readout
# ---------------------------------------------------------------------------
def _blank(grid):
    return np.zeros(grid.shape, dtype=np.float32)


def _poke(values, grid, lat, lon, value):
    """Set one cell, by lat/lon. Returns the (lat, lon) of the cell actually set."""
    r, c = latlon_to_rowcol(grid, lat, lon)
    r, c = int(round(r)), int(round(c))
    values[r, c] = value
    return rowcol_to_latlon(grid, r, c)


def test_readout_finds_the_max_and_names_the_town(small_grid, mask):
    v = _blank(small_grid)
    # 0.0170 s^-1 at Mansfield — already scaled, exactly as decode_grib_gz returns.
    # (Not Burleson: the KFWS tower sits ~3.8 km from Burleson's centre, i.e.
    # inside the 5 km exclusion disc — see the exclusion test below.)
    lat, lon = _poke(v, small_grid, 32.563, -97.142, 0.017)
    _poke(v, small_grid, 32.8, -97.0, 0.009)
    out = compute_readout(v, mask, 0.010, places=DFW_PLACES)

    assert out["max_azshear"] == pytest.approx(0.017, abs=1e-6)
    loc = out["max_location"]
    assert set(loc) == {"lat", "lon", "km", "bearing", "near"}
    assert loc["lat"] == pytest.approx(lat, abs=0.005)
    assert loc["lon"] == pytest.approx(lon, abs=0.005)
    assert loc["near"] == "Mansfield"
    assert loc["bearing"] in ("E", "ENE", "ESE")
    assert loc["km"] == pytest.approx(haversine_km(KFWS_LAT, KFWS_LON, lat, lon), abs=0.2)
    assert out["cells_ge_threshold"] == 1          # only the 0.017 cell clears 0.010
    assert out["coverage_nonzero_fraction"] == pytest.approx(2 / mask.n_cells, abs=1e-8)


def test_readout_of_an_all_zero_domain_is_honestly_empty(small_grid, mask):
    out = compute_readout(_blank(small_grid), mask, 0.010, places=DFW_PLACES)
    assert out["max_azshear"] == 0.0
    assert out["coverage_nonzero_fraction"] == 0.0
    assert out["cells_ge_threshold"] == 0
    assert out["top_cells"] == []
    # no location is invented for an empty field
    assert out["max_location"] is None


def test_readout_ignores_cells_inside_the_radar_exclusion(small_grid, mask):
    v = _blank(small_grid)
    _poke(v, small_grid, KFWS_LAT, KFWS_LON, 0.099)     # 0 km — LLSD range artifact
    _poke(v, small_grid, 32.542, -97.321, 0.050)        # Burleson, 3.8 km — also excluded
    _poke(v, small_grid, 32.563, -97.142, 0.012)        # Mansfield, 15 km — kept
    out = compute_readout(v, mask, 0.010, places=DFW_PLACES)
    assert out["max_azshear"] == pytest.approx(0.012, abs=1e-6)
    assert out["max_location"]["near"] == "Mansfield"
    assert out["cells_ge_threshold"] == 1
    assert [c["near"] for c in out["top_cells"]] == ["Mansfield"]


def test_top_cells_dedupe_within_10km(small_grid, mask):
    v = _blank(small_grid)
    # two cells ~1 km apart (one storm) and one 40 km away (a second storm)
    _poke(v, small_grid, 32.900, -97.300, 0.020)
    _poke(v, small_grid, 32.908, -97.300, 0.019)
    far_lat, far_lon = _poke(v, small_grid, 32.900, -96.870, 0.015)
    out = compute_readout(v, mask, 0.010, top_n=5, dedupe_km=10.0, places=DFW_PLACES)

    cells = out["top_cells"]
    assert len(cells) == 2, "the two cells 1 km apart must collapse to one"
    assert cells[0]["value"] == pytest.approx(0.020, abs=1e-6)
    assert cells[1]["value"] == pytest.approx(0.015, abs=1e-6)
    assert set(cells[0]) == {"lat", "lon", "value", "km_from_kfws", "bearing", "near"}
    assert haversine_km(cells[0]["lat"], cells[0]["lon"],
                        cells[1]["lat"], cells[1]["lon"]) > 10.0
    assert cells[1]["lat"] == pytest.approx(far_lat, abs=0.005)
    assert cells[1]["lon"] == pytest.approx(far_lon, abs=0.005)
    # with dedupe off, both cells of the first storm survive
    loose = compute_readout(v, mask, 0.010, top_n=5, dedupe_km=0.0, places=DFW_PLACES)
    assert len(loose["top_cells"]) == 3


def test_top_cells_use_half_the_threshold_and_respect_top_n(small_grid, mask):
    v = _blank(small_grid)
    lats = [32.90, 33.05, 33.20, 32.30, 32.15, 31.90]
    for i, lat in enumerate(lats):
        _poke(v, small_grid, lat, -97.30, 0.006 + 0.001 * i)
    _poke(v, small_grid, 32.75, -96.60, 0.004)     # below 0.5 * 0.010 -> excluded
    out = compute_readout(v, mask, 0.010, top_n=5, places=DFW_PLACES)
    assert len(out["top_cells"]) == 5
    assert all(c["value"] >= 0.005 for c in out["top_cells"])
    values = [c["value"] for c in out["top_cells"]]
    assert values == sorted(values, reverse=True)


def test_readout_without_places_leaves_near_null(small_grid, mask):
    v = _blank(small_grid)
    _poke(v, small_grid, 32.563, -97.142, 0.012)
    out = compute_readout(v, mask, 0.010)
    assert out["max_location"]["near"] is None
    assert out["top_cells"][0]["near"] is None


# ---------------------------------------------------------------------------
# places
# ---------------------------------------------------------------------------
def test_place_table_is_sane_and_lookup_picks_the_nearest():
    assert len(DFW_PLACES) >= 25
    names = [n for n, _, _ in DFW_PLACES]
    assert len(set(names)) == len(names)
    for name, lat, lon in DFW_PLACES:
        assert 31.0 < lat < 34.5, name
        assert -99.0 < lon < -95.5, name
    assert nearest_place(32.755, -97.331) == "Fort Worth"
    assert nearest_place(32.777, -96.797) == "Dallas"
    assert nearest_place(32.545, -97.325) == "Burleson"
    assert nearest_place(0.0, 0.0, places=()) is None


# ---------------------------------------------------------------------------
# real GRIB decode — optional, only if the Phase 0 spike cache is still around
# ---------------------------------------------------------------------------
_SPIKE_CACHE = _REPO / "scratch" / "mrms-spike" / "cache"
_SPIKE_FILES = sorted(_SPIKE_CACHE.glob("*.grib2.gz")) if _SPIKE_CACHE.is_dir() else []
_HAS_ECCODES = True
try:                                        # pragma: no cover - environment probe
    import eccodes  # noqa: F401
except Exception:                           # pragma: no cover
    _HAS_ECCODES = False


@pytest.mark.skipif(not _HAS_ECCODES, reason="eccodes not installed")
@pytest.mark.skipif(not _SPIKE_FILES, reason="scratch/mrms-spike/cache is not present")
def test_decode_real_mrms_file_matches_the_phase0_grid():
    values, spec = decode_grib_gz(_SPIKE_FILES[0])
    assert (spec.ni, spec.nj) == (14000, 7000)
    assert spec.lat0 == pytest.approx(54.9975)
    assert spec.lon0 == pytest.approx(230.0025)
    assert spec.rows_north_first is True
    assert spec.lon_is_0_360 is True
    assert spec.dlat == pytest.approx(-0.005, abs=1e-6)
    assert spec.dlon == pytest.approx(0.005, abs=1e-6)
    assert values.dtype == np.float32
    assert values.shape == (spec.nj, spec.ni)
    # already scaled to s^-1: raw values are small integers in 0.001 s^-1, so
    # nothing may exceed a physically absurd shear after scaling
    assert abs(float(values.max())) < 1.0
    # raw values are integers in 0.001 s^-1, so every scaled value is a multiple
    # of AZSHEAR_SCALE to within float32 resolution
    peak = float(np.abs(values).max())
    assert peak == pytest.approx(round(peak / AZSHEAR_SCALE) * AZSHEAR_SCALE, abs=1e-7)

    mask = DomainMask(spec, KFWS_LAT, KFWS_LON, radius_km=100.0, exclusion_km=5.0)
    out = compute_readout(values, mask, 0.010, places=DFW_PLACES)
    assert 0.0 <= out["coverage_nonzero_fraction"] <= 1.0
    assert out["max_azshear"] >= 0.0
