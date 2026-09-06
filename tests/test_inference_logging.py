"""Inference-side operational hygiene: the per-cycle score log and the PNG
cache pruner. Both must be incapable of breaking a scoring cycle.

Nothing here touches IEM, torch or the model — only the two helpers and the
`State` object they read.
"""
import json
import os
import time
from datetime import datetime, timezone

import pytest

from common.jsonlog import month_path
from services.inference import inference_service as inf


@pytest.fixture
def state():
    """A State with no real model — neither helper looks at one."""
    s = inf.State(model=None, model_sha256="c0500889deadbeef", manifest={})
    return s


@pytest.fixture
def score_log(tmp_path, monkeypatch):
    base = tmp_path / "logs" / "scores.jsonl"
    monkeypatch.setattr(inf, "SCORE_LOG_PATH", base)
    return base


def read_lines(base):
    path = month_path(base)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---- score log --------------------------------------------------------------

def test_a_successful_cycle_logs_the_score(state, score_log):
    n0b = datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc)
    n0s = datetime(2026, 9, 5, 18, 1, tzinfo=timezone.utc)
    state.last_status.update({"status": "running", "last_score": 0.4096,
                              "scan_delta_seconds": 60, "threshold": 0.8})

    inf.append_score_log(state, {"fetch": 812, "infer": 7}, n0b, n0s, 0.9)

    (row,) = read_lines(score_log)
    assert row["status"] == "running"
    assert row["score"] == 0.4096
    assert row["n0b_time"] == "2026-09-05T18:00:00Z"
    assert row["n0s_time"] == "2026-09-05T18:01:00Z"
    assert row["scan_delta_seconds"] == 60
    assert (row["fetch_ms"], row["infer_ms"], row["cycle_ms"]) == (812, 7, 900)
    assert row["error"] is None
    assert row["model_sha256"] == "c0500889deadbeef"
    assert row["ts"].endswith("Z")


def test_a_failed_cycle_logs_the_error_class_and_no_score(state, score_log):
    """A stale cycle keeps last_score in the status file; logging it would
    fabricate a score for a moment the model never saw."""
    state.last_status.update({"status": "stale", "last_score": 0.4096,
                              "scan_delta_seconds": 60})
    state.record_error("fetch", "no_recent_n0b_in_iem")

    inf.append_score_log(state, {"fetch": 30012}, None, None, 30.1)

    (row,) = read_lines(score_log)
    assert row["status"] == "stale"
    assert row["score"] is None
    assert row["scan_delta_seconds"] is None
    assert row["n0b_time"] is None and row["n0s_time"] is None
    assert row["error"] == "fetch"
    assert row["error_msg"] == "no_recent_n0b_in_iem"
    assert row["infer_ms"] is None


def test_lines_append_one_per_cycle(state, score_log):
    state.last_status.update({"status": "running", "last_score": 0.1})
    for _ in range(3):
        inf.append_score_log(state, {}, None, None, 0.5)
    assert len(read_lines(score_log)) == 3


def test_the_log_rotates_by_month(tmp_path):
    base = tmp_path / "scores.jsonl"
    assert month_path(base, datetime(2026, 9, 5, tzinfo=timezone.utc)).name == "scores-202609.jsonl"
    assert month_path(base, datetime(2026, 12, 31, tzinfo=timezone.utc)).name == "scores-202612.jsonl"


def test_an_unwritable_log_dir_never_breaks_a_cycle(state, tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "logs-is-a-file"
    blocked.write_text("not a directory")
    monkeypatch.setattr(inf, "SCORE_LOG_PATH", blocked / "scores.jsonl")
    state.last_status.update({"status": "running", "last_score": 0.5})

    inf.append_score_log(state, {}, None, None, 0.4)      # must not raise
    inf.append_score_log(state, {}, None, None, 0.4)      # and must not spam

    assert capsys.readouterr().err.count("operational log unavailable") <= 1


def test_a_read_only_log_dir_never_breaks_a_cycle(state, tmp_path, monkeypatch):
    logs = tmp_path / "ro-logs"
    logs.mkdir()
    monkeypatch.setattr(inf, "SCORE_LOG_PATH", logs / "scores.jsonl")
    os.chmod(logs, 0o500)
    try:
        state.last_status.update({"status": "running", "last_score": 0.5})
        inf.append_score_log(state, {}, None, None, 0.4)   # must not raise
    finally:
        os.chmod(logs, 0o700)


# ---- PNG cache pruning ------------------------------------------------------

@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(inf, "STATE_DIR", tmp_path)
    d = tmp_path / "current"
    d.mkdir()
    return d


def aged(path, days):
    path.write_bytes(b"\x89PNG" + b"0" * 300)
    old = time.time() - days * 86400
    os.utime(path, (old, old))
    return path


def test_files_older_than_the_retention_window_are_removed(cache, state):
    old = aged(cache / "KFWS_N0B_202605270000.png", 30)
    fresh = aged(cache / "KFWS_N0B_202609050000.png", 1)

    assert inf.prune_png_cache(state, retention_days=14) == 1
    assert not old.exists()
    assert fresh.exists()


def test_the_boundary_is_the_retention_age(cache, state):
    aged(cache / "KFWS_N0B_a.png", 13)
    aged(cache / "KFWS_N0S_b.png", 15)
    assert inf.prune_png_cache(state, retention_days=14) == 1
    assert (cache / "KFWS_N0B_a.png").exists()


def test_the_current_cycles_pngs_are_never_touched(cache, state):
    """fetch_pair writes both PNGs at the top of the cycle; pruning runs at the
    end of the same cycle."""
    n0b = aged(cache / "KFWS_N0B_202609052355.png", 0)
    n0s = aged(cache / "KFWS_N0S_202609052355.png", 0)
    inf.prune_png_cache(state, retention_days=1)
    assert n0b.exists() and n0s.exists()


def test_only_pngs_in_that_one_directory_are_considered(cache, state, tmp_path):
    keeper = aged(cache / "notes.txt", 90)
    sub = cache / "subdir"
    sub.mkdir()
    nested = aged(sub / "KFWS_N0B_old.png", 90)
    sibling = aged(tmp_path / "KFWS.wld", 90)

    inf.prune_png_cache(state, retention_days=14)
    assert keeper.exists() and nested.exists() and sibling.exists()


def test_symlinks_are_not_followed(cache, state, tmp_path):
    target = aged(tmp_path / "precious.png", 90)
    link = cache / "KFWS_N0B_link.png"
    link.symlink_to(target)

    inf.prune_png_cache(state, retention_days=14)
    assert target.exists(), "pruning must never delete through a symlink"
    assert link.is_symlink(), "a symlink is not a file we wrote; leave it alone"


def test_a_missing_cache_dir_is_a_no_op(tmp_path, monkeypatch, state):
    monkeypatch.setattr(inf, "STATE_DIR", tmp_path / "nope")
    assert inf.prune_png_cache(state) == 0
    assert state.snapshot()["errors"] == []


def test_retention_zero_disables_pruning(cache, state):
    old = aged(cache / "KFWS_N0B_ancient.png", 400)
    assert inf.prune_png_cache(state, retention_days=0) == 0
    assert old.exists()


def test_the_default_retention_is_fourteen_days():
    assert inf.STATE_RETENTION_DAYS == 14


# ---- wiring: the cycle really does log and prune ----------------------------

def test_run_cycle_logs_a_line_even_when_the_fetch_fails(state, score_log, tmp_path, monkeypatch):
    """Proves the finally-block wiring, not just the helper: a cycle that never
    reaches the model still leaves a row in the history."""
    monkeypatch.setattr(inf, "STATUS_PATH", tmp_path / "inference_status.json")
    monkeypatch.setattr(inf, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(inf, "fetch_pair", lambda: (None, None, None, None, None))

    status = inf.run_cycle(state)

    assert status["status"] == "uninitialized"
    (row,) = read_lines(score_log)
    assert row["status"] == "uninitialized"
    assert row["score"] is None
    assert row["error"] == "fetch"
    assert row["cycle_ms"] >= 0


def test_run_cycle_prunes_the_png_cache(state, score_log, tmp_path, monkeypatch):
    monkeypatch.setattr(inf, "STATUS_PATH", tmp_path / "inference_status.json")
    monkeypatch.setattr(inf, "STATE_DIR", tmp_path)
    monkeypatch.setattr(inf, "fetch_pair", lambda: (None, None, None, None, None))
    (tmp_path / "current").mkdir()
    old = aged(tmp_path / "current" / "KFWS_N0B_ancient.png", 90)

    inf.run_cycle(state)
    assert not old.exists()
