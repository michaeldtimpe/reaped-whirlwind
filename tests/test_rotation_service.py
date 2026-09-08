"""Offline tests for services/rotation/rotation_service.py.

No network, no eccodes, no GRIB: the service's three seams — `list_latest`,
`fetch_bytes` and `decode_grib_gz` — are module attributes, so a cycle can be
driven end to end over a synthetic 480x480 grid (the real MRMS geometry at
1/500th the size, same fixture shape as tests/test_rotation_core.py).

What is pinned here is the *contract*: the status keys alerting will read, that
`last_score_time` is the product's valid time rather than the fetch time, that a
no-new-file cycle never rewrites the score, and that a failed fetch is an
`error` status with a counter rather than a dead service.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "services" / "rotation"))

from common.jsonlog import month_path                              # noqa: E402
from rotation_core import GridSpec, latlon_to_rowcol            # noqa: E402
from services.rotation import rotation_service as rot              # noqa: E402

KFWS_LAT, KFWS_LON = 32.5728, -97.3031
VALID = datetime(2026, 9, 7, 22, 48, tzinfo=timezone.utc)

# Same 0.005-deg, north-first, 0-360 geometry as the real CONUS grid, ~+/-1.2
# deg around KFWS (the fixture shape tests/test_rotation_core.py uses).
GRID = GridSpec(ni=480, nj=480,
                lat0=KFWS_LAT + 1.2, lon0=(KFWS_LON + 360.0) - 1.2,
                dlat=-0.005, dlon=0.005, rows_north_first=True)

# Every key docs/MRMS_MIGRATION.md §2.1 promises, plus the three operational
# counters the service adds. If this list changes, alerting's readout and the
# dashboard have to change with it — that is the point of pinning it.
DOCUMENTED_KEYS = (
    "status", "last_score", "last_score_time", "threshold",
    "product", "product_valid_time", "fetched_at", "source",
    "max_location", "cells_ge_threshold", "coverage_nonzero_fraction",
    "max_track30", "top_cells", "domain_radius_km", "cycle_ms", "errors",
    "fetch_consecutive_failures", "no_new_file_streak", "last_new_file_time",
    "service_started",
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def field(*cells):
    """A blank grid with (lat, lon, value) cells poked in, already in s^-1
    exactly as `decode_grib_gz` returns them."""
    v = np.zeros(GRID.shape, dtype=np.float32)
    for lat, lon, value in cells:
        r, c = latlon_to_rowcol(GRID, lat, lon)
        v[int(round(r)), int(round(c))] = value
    return v


class Feed:
    """A fake MRMS feed. `newest` is the valid time `list_latest` reports;
    `fields` maps product -> array. Records every fetch so a test can assert the
    second product was (or was not) fetched."""

    def __init__(self, newest=VALID, az=None, track=None, source="ncep"):
        self.newest = newest
        self.source = source
        self.fields = {rot.PRODUCT: az if az is not None else field(),
                       rot.TRACK_PRODUCT: track if track is not None else field()}
        self.fetched = []
        self.list_error = None

    def list_latest(self, product):
        if self.list_error is not None:
            raise self.list_error
        return self.newest, f"MRMS_{product}.grib2.gz", self.source

    def list_files(self, product):
        if self.list_error is not None:
            raise self.list_error
        return [(self.newest, f"MRMS_{product}.grib2.gz")], self.source

    def fetch_bytes(self, product, ref, source):
        self.fetched.append((product, ref, source))
        return product.encode()      # a token; decode_grib_gz is faked too

    def decode(self, blob):
        product = blob.decode() if isinstance(blob, bytes) else str(blob)
        return self.fields[product], GRID


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the service's paths at tmp_path and reset the tunables."""
    monkeypatch.setattr(rot, "STATUS_PATH", tmp_path / "status" / "rotation_status.json")
    monkeypatch.setattr(rot, "ROTATION_LOG_PATH", tmp_path / "logs" / "rotation.jsonl")
    monkeypatch.setattr(rot, "THRESHOLD", 0.015)
    monkeypatch.setattr(rot, "DOMAIN_RADIUS_KM", 100.0)
    monkeypatch.setattr(rot, "MAX_SCORE_AGE", 900)
    monkeypatch.setattr(rot, "STALE_NO_NEW_FILE", 1200)
    monkeypatch.setattr(rot, "ROTATION_TRACK", True)
    return tmp_path


@pytest.fixture
def feed(monkeypatch):
    f = Feed()
    monkeypatch.setattr(rot, "list_latest", f.list_latest)
    monkeypatch.setattr(rot, "list_files", f.list_files)
    monkeypatch.setattr(rot, "fetch_bytes", f.fetch_bytes)
    monkeypatch.setattr(rot, "decode_grib_gz", f.decode)
    return f


@pytest.fixture
def state():
    return rot.State()


def rows(tmp_path):
    path = month_path(tmp_path / "logs" / "rotation.jsonl")
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def status_file(tmp_path):
    return json.loads((tmp_path / "status" / "rotation_status.json").read_text())


# ---------------------------------------------------------------------------
# a good cycle
# ---------------------------------------------------------------------------
def test_a_good_cycle_writes_every_documented_key(env, feed, state):
    # 0.017 s^-1 at Mansfield (15 km E of KFWS, outside the 5 km radar disc)
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))
    feed.fields[rot.TRACK_PRODUCT] = field((32.563, -97.142, 0.021))

    status = rot.run_cycle(state)

    for key in DOCUMENTED_KEYS:
        assert key in status, key
    assert set(status) == set(DOCUMENTED_KEYS), "undocumented key in the status file"
    assert status == status_file(env), "the status file must be what run_cycle returned"

    assert status["status"] == "running"
    assert status["last_score"] == pytest.approx(0.017, abs=1e-6)
    assert status["threshold"] == 0.015
    assert status["product"] == "MergedAzShear_0-2kmAGL"
    assert status["source"] == "ncep"
    assert status["cells_ge_threshold"] == 1
    assert status["max_track30"] == pytest.approx(0.021, abs=1e-6)
    assert status["max_location"]["near"] == "Mansfield"
    assert status["top_cells"] and status["top_cells"][0]["near"] == "Mansfield"
    assert status["domain_radius_km"] == 100.0
    assert set(status["cycle_ms"]) == {"fetch", "decode", "compute", "write"}
    assert status["errors"] == []
    assert status["fetch_consecutive_failures"] == 0
    assert status["no_new_file_streak"] == 0


def test_last_score_time_is_the_product_valid_time_not_now(env, feed, state):
    """The whole staleness contract rests on this: `fetched_at` moves with the
    clock, `last_score_time` only with the data."""
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))
    status = rot.run_cycle(state)

    assert status["last_score_time"] == "2026-09-07T22:48:00Z"
    assert status["product_valid_time"] == status["last_score_time"]
    assert status["fetched_at"] != status["last_score_time"]
    assert status["last_new_file_time"] == status["fetched_at"]


def test_the_reported_max_is_positive_only(env, feed, state):
    """Anticyclonic shear is real and negative; the annotation reports positive
    low-level rotation, so an all-negative domain reads 0.0, not a minus sign."""
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, -0.030))
    status = rot.run_cycle(state)
    assert status["last_score"] == 0.0
    assert status["max_location"] is None


def test_an_empty_field_is_an_honest_zero_not_an_error(env, feed, state):
    status = rot.run_cycle(state)
    assert status["status"] == "running"
    assert status["last_score"] == 0.0
    assert status["coverage_nonzero_fraction"] == 0.0
    assert status["max_location"] is None
    assert status["top_cells"] == []


def test_the_domain_mask_is_built_once_and_reused(env, feed, state):
    rot.run_cycle(state)
    first = state.mask
    feed.newest = VALID + timedelta(minutes=2)
    rot.run_cycle(state)
    assert state.mask is first, "the CONUS grid never changes; rebuilding is a bug"


def test_a_changed_grid_rebuilds_the_mask_with_a_warning(env, feed, state, monkeypatch,
                                                        caplog):
    """MRMS has not moved the CONUS grid since 2020, but cropping with a mask
    that no longer lines up would silently report the wrong county."""
    rot.run_cycle(state)
    first = state.mask
    shifted = GridSpec(ni=GRID.ni, nj=GRID.nj, lat0=GRID.lat0 + 0.1, lon0=GRID.lon0,
                       dlat=GRID.dlat, dlon=GRID.dlon, rows_north_first=True)
    feed.newest = VALID + timedelta(minutes=2)
    blank = np.zeros(shifted.shape, dtype=np.float32)
    monkeypatch.setattr(rot, "decode_grib_gz", lambda blob: (blank, shifted))
    with caplog.at_level("WARNING"):
        rot.run_cycle(state)
    assert state.mask is not first
    assert state.mask.grid == shifted
    assert any("grid changed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# no new file
# ---------------------------------------------------------------------------
def test_a_no_new_file_cycle_leaves_the_score_untouched(env, feed, state):
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))
    first = rot.run_cycle(state)
    assert len(feed.fetched) == 2                    # azshear + track

    second = rot.run_cycle(state)                    # same valid time

    assert len(feed.fetched) == 2, "an unchanged file must not be re-downloaded"
    assert second["last_score"] == first["last_score"]
    assert second["last_score_time"] == first["last_score_time"]
    assert second["status"] == "running"
    assert second["no_new_file_streak"] == 1
    assert rot.run_cycle(state)["no_new_file_streak"] == 2
    assert rot.run_cycle(state)["no_new_file_streak"] == 3


def test_a_new_file_resets_the_streak(env, feed, state):
    rot.run_cycle(state)
    rot.run_cycle(state)
    assert state.no_new_file_streak == 1
    feed.newest = VALID + timedelta(minutes=2)
    assert rot.run_cycle(state)["no_new_file_streak"] == 0


def test_an_older_listing_is_never_reprocessed(env, feed, state):
    """An S3 fallback can lag NCEP. Rewinding last_score_time would make the
    annotation look fresh when it is not."""
    rot.run_cycle(state)
    feed.newest = VALID - timedelta(minutes=10)
    status = rot.run_cycle(state)
    assert status["last_score_time"] == "2026-09-07T22:48:00Z"
    assert status["no_new_file_streak"] == 1


def test_the_first_cycle_before_any_score_stays_uninitialized(env, feed, state):
    """A no-new-file cycle can only mean 'running' once there is a score to be
    running with."""
    state.last_valid_time = VALID
    status = rot.run_cycle(state)
    assert status["status"] == "uninitialized"
    assert status["last_score"] is None


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------
def test_a_fetch_failure_is_an_error_status_with_a_counter(env, feed, state):
    feed.list_error = RuntimeError("NCEP unreachable")

    status = rot.run_cycle(state)

    assert status["status"] == "error"
    assert status["last_score"] is None
    assert status["fetch_consecutive_failures"] == 1
    assert len(status["errors"]) == 1
    err = status["errors"][0]
    assert err["kind"] == "cycle"
    assert "NCEP unreachable" in err["msg"]
    assert err["time"].endswith("Z")

    assert rot.run_cycle(state)["fetch_consecutive_failures"] == 2
    assert rot.run_cycle(state)["fetch_consecutive_failures"] == 3

    feed.list_error = None
    ok = rot.run_cycle(state)
    assert ok["status"] == "running"
    assert ok["fetch_consecutive_failures"] == 0, "the counter must reset on success"


def test_a_failed_cycle_keeps_the_previous_score_visible(env, feed, state):
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))
    good = rot.run_cycle(state)
    feed.list_error = RuntimeError("boom")
    bad = rot.run_cycle(state)
    assert bad["status"] == "error"
    assert bad["last_score"] == good["last_score"]
    assert bad["last_score_time"] == good["last_score_time"]


def test_the_error_ring_keeps_only_the_last_five(env, feed, state):
    feed.list_error = RuntimeError("boom")
    for _ in range(8):
        rot.run_cycle(state)
    assert len(state.snapshot()["errors"]) == 5


def test_a_track_failure_does_not_fail_the_cycle(env, feed, state, monkeypatch):
    """RotationTrack30min is context beside the instantaneous shear, not the
    number the annotation turns on."""
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))

    def only_azshear(product):
        if product == rot.TRACK_PRODUCT:
            raise RuntimeError("track index 404")
        return feed.list_files(product)

    monkeypatch.setattr(rot, "list_files", only_azshear)
    status = rot.run_cycle(state)

    assert status["status"] == "running"
    assert status["last_score"] == pytest.approx(0.017, abs=1e-6)
    assert status["max_track30"] is None
    assert status["errors"][-1]["kind"] == "track"
    assert status["fetch_consecutive_failures"] == 0


def test_a_track_too_far_from_the_scan_is_null(env, feed, state, monkeypatch):
    def stale_track(product):
        if product == rot.TRACK_PRODUCT:
            return [(VALID - timedelta(minutes=20), "old.grib2.gz")], "ncep"
        return feed.list_files(product)

    monkeypatch.setattr(rot, "list_files", stale_track)
    status = rot.run_cycle(state)
    assert status["status"] == "running"
    assert status["max_track30"] is None
    assert [p for p, _, _ in feed.fetched] == [rot.PRODUCT]


def test_rotation_track_off_skips_the_second_fetch(env, feed, state, monkeypatch):
    monkeypatch.setattr(rot, "ROTATION_TRACK", False)
    status = rot.run_cycle(state)
    assert [p for p, _, _ in feed.fetched] == [rot.PRODUCT]
    assert status["max_track30"] is None


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
def test_health_reports_the_score_age(env, feed, state, monkeypatch):
    feed.newest = datetime.now(timezone.utc).replace(microsecond=0)
    rot.run_cycle(state)
    body = rot.health_payload(state)
    assert body["status"] == "running"
    assert 0 <= body["score_age_seconds"] < 60
    assert body["no_new_file_seconds"] is not None


def test_health_downgrades_to_stale_when_the_product_is_old(env, feed, state):
    rot.run_cycle(state)                    # VALID is a fixed 2026-09-07 timestamp
    body = rot.health_payload(state)
    assert body["score_age_seconds"] > 900
    assert body["status"] == "stale"
    assert state.snapshot()["status"] == "running", "stale is a /health view, not stored"


def test_health_downgrades_to_stale_when_the_feed_stops_advancing(env, feed, state,
                                                                 monkeypatch):
    """The score itself can be fresh while NCEP has published nothing new for
    20 min — Phase 5's feed alarm."""
    feed.newest = datetime.now(timezone.utc).replace(microsecond=0)
    rot.run_cycle(state)
    s = state.snapshot()
    s["last_new_file_time"] = rot.utc_iso(datetime.now(timezone.utc) - timedelta(hours=1))
    state.commit(s)
    body = rot.health_payload(state)
    assert body["score_age_seconds"] < 900
    assert body["no_new_file_seconds"] > 1200
    assert body["status"] == "stale"


def test_health_never_hides_an_error_behind_stale(env, feed, state):
    feed.list_error = RuntimeError("boom")
    rot.run_cycle(state)
    assert rot.health_payload(state)["status"] == "error"


def test_health_endpoint_serves_the_payload(env, feed, state):
    rot.run_cycle(state)
    client = rot.make_app(state).test_client()
    body = client.get("/health").get_json()
    assert body["status"] in ("running", "stale")
    assert "score_age_seconds" in body


def test_an_untouched_service_reports_uninitialized(env, state):
    body = rot.health_payload(state)
    assert body["status"] == "uninitialized"
    assert body["score_age_seconds"] is None
    assert body["last_score"] is None
    for key in DOCUMENTED_KEYS:
        assert key in body, key


# ---------------------------------------------------------------------------
# JSONL history
# ---------------------------------------------------------------------------
def test_a_good_cycle_appends_the_documented_jsonl_fields(env, feed, state):
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))
    feed.fields[rot.TRACK_PRODUCT] = field((32.563, -97.142, 0.021))
    rot.run_cycle(state)

    (row,) = rows(env)
    assert set(row) == {"ts", "status", "valid_time", "source", "max_azshear",
                        "max_track30", "cells_ge_threshold",
                        "coverage_nonzero_fraction", "max_location", "fetch_ms",
                        "decode_ms", "cycle_ms", "error", "error_msg", "threshold"}
    assert row["status"] == "running"
    assert row["valid_time"] == "2026-09-07T22:48:00Z"
    assert row["source"] == "ncep"
    assert row["max_azshear"] == pytest.approx(0.017, abs=1e-6)
    assert row["max_track30"] == pytest.approx(0.021, abs=1e-6)
    assert row["cells_ge_threshold"] == 1
    assert row["threshold"] == 0.015
    assert row["error"] is None and row["error_msg"] is None
    assert row["cycle_ms"] >= 0 and row["fetch_ms"] >= 0 and row["decode_ms"] >= 0
    # location is where, not the exact pixel
    assert set(row["max_location"]) == {"near", "km", "bearing"}
    assert row["max_location"]["near"] == "Mansfield"
    assert row["ts"].endswith("Z")


def test_every_cycle_appends_exactly_one_line(env, feed, state):
    rot.run_cycle(state)                      # new file
    rot.run_cycle(state)                      # no new file
    feed.list_error = RuntimeError("boom")
    rot.run_cycle(state)                      # error
    assert [r["status"] for r in rows(env)] == ["running", "running", "error"]


def test_a_no_new_file_line_reports_no_reading(env, feed, state):
    """last_score lingers in the status file by design; copying it into the
    history would fabricate a reading for a scan that never happened."""
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))
    rot.run_cycle(state)
    rot.run_cycle(state)
    row = rows(env)[1]
    assert row["max_azshear"] is None
    assert row["max_track30"] is None
    assert row["cells_ge_threshold"] is None
    assert row["coverage_nonzero_fraction"] is None
    assert row["max_location"] is None
    assert row["error"] is None


def test_an_error_line_carries_the_error_class_and_message(env, feed, state):
    feed.list_error = RuntimeError("NCEP unreachable")
    rot.run_cycle(state)
    (row,) = rows(env)
    assert row["status"] == "error"
    assert row["max_azshear"] is None
    assert row["error"] == "cycle"
    assert "NCEP unreachable" in row["error_msg"]


def test_the_history_rotates_by_month(env):
    base = env / "logs" / "rotation.jsonl"
    assert month_path(base, datetime(2026, 9, 7, tzinfo=timezone.utc)).name == "rotation-202609.jsonl"
    assert month_path(base, datetime(2027, 1, 1, tzinfo=timezone.utc)).name == "rotation-202701.jsonl"


def test_an_unwritable_log_dir_never_breaks_a_cycle(env, feed, state, monkeypatch):
    blocked = env / "logs-is-a-file"
    blocked.write_text("not a directory")
    monkeypatch.setattr(rot, "ROTATION_LOG_PATH", blocked / "rotation.jsonl")
    assert rot.run_cycle(state)["status"] == "running"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_once_prints_json_and_exits_zero_on_a_good_cycle(env, feed, monkeypatch, capsys):
    feed.fields[rot.PRODUCT] = field((32.563, -97.142, 0.017))
    monkeypatch.setattr(sys, "argv", ["rotation_service.py", "--once"])
    with pytest.raises(SystemExit) as exc:
        rot.main()
    assert exc.value.code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "running"
    assert printed["last_score"] == pytest.approx(0.017, abs=1e-6)


def test_once_exits_nonzero_when_the_cycle_failed(env, feed, monkeypatch, capsys):
    feed.list_error = RuntimeError("NCEP unreachable")
    monkeypatch.setattr(sys, "argv", ["rotation_service.py", "--once"])
    with pytest.raises(SystemExit) as exc:
        rot.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_the_defaults_match_the_migration_plan():
    assert rot.PORT == 9010
    assert rot.POLL_INTERVAL == 120
    assert rot.MAX_SCORE_AGE == 900
    assert rot.THRESHOLD == 0.015          # Phase 1 result
    assert rot.DOMAIN_RADIUS_KM == 100.0
    assert rot.CYCLE_DEADLINE_SEC == 60.0
    assert rot.ROTATION_TRACK is True
    assert rot.PRODUCT == "MergedAzShear_0-2kmAGL"
    assert rot.TRACK_PRODUCT == "RotationTrack30min"
    assert str(rot.STATUS_PATH) == "/status/rotation_status.json"
    assert str(rot.ROTATION_LOG_PATH) == "/logs/rotation.jsonl"


def test_the_base_urls_are_wired_into_mrms_fetch():
    import mrms_fetch as mf
    assert mf.NCEP_BASE_URL == rot.NCEP_BASE_URL
    assert mf.S3_BASE_URL == rot.S3_BASE_URL
    assert rot.NCEP_BASE_URL.endswith("/")
    assert rot.S3_BASE_URL.endswith("/")


def test_s3_fallback_never_serves_a_cached_listing_of_today(monkeypatch):
    """mrms_fetch memoises day listings for the backtest; a service that polls
    for hours must bypass that or the feed freezes at process start."""
    seen = {}

    def fake_ncep_list(product, session=None):
        raise RuntimeError("down")

    def fake_s3_list(product, day, session=None, timeout=30, use_cache=True):
        seen["use_cache"] = use_cache
        return [(VALID, "some/key.grib2.gz")]

    monkeypatch.setattr(rot.mrms_fetch, "ncep_list", fake_ncep_list)
    monkeypatch.setattr(rot.mrms_fetch, "s3_list", fake_s3_list)
    monkeypatch.setattr(rot, "_near_utc_midnight", lambda now: False)
    entries, source = rot.list_files(rot.PRODUCT)
    assert source == "s3"
    assert entries == [(VALID, "some/key.grib2.gz")]
    assert seen["use_cache"] is False


def test_the_midnight_window_pulls_yesterdays_prefix_too(monkeypatch):
    days = []

    monkeypatch.setattr(rot.mrms_fetch, "ncep_list",
                        lambda product, session=None: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(rot.mrms_fetch, "s3_list",
                        lambda product, day, **kw: days.append(day) or [(VALID, "k")])
    monkeypatch.setattr(rot, "_near_utc_midnight", lambda now: True)
    rot.list_files(rot.PRODUCT)
    assert len(days) == 2 and days[0] - days[1] == timedelta(days=1)
