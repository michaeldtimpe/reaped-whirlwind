"""common.status — the status-file helpers every service writes through."""
import json
import os
import re
from datetime import datetime, timezone

import pytest

from common.status import atomic_write_json, deep_copy_json, utc_iso


ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---- utc_iso ----------------------------------------------------------------

def test_utc_iso_default_now_is_zulu_seconds():
    assert ISO_Z.match(utc_iso())


def test_utc_iso_formats_an_aware_datetime():
    dt = datetime(2026, 5, 27, 3, 4, 5, 987654, tzinfo=timezone.utc)
    assert utc_iso(dt) == "2026-05-27T03:04:05Z"


def test_utc_iso_treats_naive_as_utc():
    naive = datetime(2026, 5, 27, 3, 4, 5)
    assert utc_iso(naive) == utc_iso(naive.replace(tzinfo=timezone.utc))


# ---- atomic_write_json ------------------------------------------------------

def test_atomic_write_json_round_trips_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "inference_status.json"
    obj = {"status": "running", "last_score": 0.1234, "errors": []}

    atomic_write_json(target, obj)

    assert json.loads(target.read_text()) == obj
    assert [p.name for p in tmp_path.iterdir()] == ["inference_status.json"]


def test_atomic_write_json_creates_missing_parents(tmp_path):
    target = tmp_path / "a" / "b" / "status.json"
    atomic_write_json(target, {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}


def test_atomic_write_json_accepts_a_str_path(tmp_path):
    target = tmp_path / "status.json"
    atomic_write_json(str(target), {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}


def test_atomic_write_json_overwrites_in_place(tmp_path):
    target = tmp_path / "status.json"
    atomic_write_json(target, {"n": 1})
    atomic_write_json(target, {"n": 2})
    assert json.loads(target.read_text()) == {"n": 2}
    assert list(tmp_path.iterdir()) == [target]


def test_failure_mid_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    """The whole point of the helper: a crash between open() and replace() must
    not leave the dashboard reading a truncated status file."""
    target = tmp_path / "status.json"
    good = {"status": "running", "emails_sent_total": 7}
    atomic_write_json(target, good)

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"status": "clobbered"})

    assert json.loads(target.read_text()) == good          # previous content survives
    assert list(tmp_path.iterdir()) == [target]            # and no .tmp left behind


def test_failure_during_serialisation_leaves_the_previous_file_intact(tmp_path):
    target = tmp_path / "status.json"
    good = {"status": "running"}
    atomic_write_json(target, good)

    class NotJSON:
        pass

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": NotJSON()})

    assert json.loads(target.read_text()) == good
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_json_fsync_can_be_skipped(tmp_path):
    target = tmp_path / "status.json"
    atomic_write_json(target, {"n": 1}, fsync=False)
    assert json.loads(target.read_text()) == {"n": 1}


# ---- deep_copy_json ---------------------------------------------------------

def test_deep_copy_json_is_an_independent_snapshot():
    src = {"errors": [{"kind": "nws"}], "cycle_ms": {"fetch": 12}}
    snap = deep_copy_json(src)
    assert snap == src
    snap["errors"].append({"kind": "smtp"})
    snap["cycle_ms"]["fetch"] = 99
    assert src == {"errors": [{"kind": "nws"}], "cycle_ms": {"fetch": 12}}


def test_deep_copy_json_rejects_non_json_payloads():
    with pytest.raises(TypeError):
        deep_copy_json({"when": datetime.now(timezone.utc)})
