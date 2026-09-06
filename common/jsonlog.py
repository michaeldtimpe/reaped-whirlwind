"""Append-only, month-rotated JSONL operational logs.

Both Part C services keep durable history here (the `*-logs` bind mounts):
  inference: one line per cycle       -> /logs/scores-YYYYMM.jsonl
  alerting:  one line per decision    -> /logs/decisions-YYYYMM.jsonl

Design rules, both load-bearing:
  * Logging NEVER breaks the thing it observes. Every failure is swallowed and
    reported once per path to stderr; `append_jsonl` returns True/False so a
    caller can count, but it does not raise.
  * Rotation is by calendar month on the record's own timestamp, so a file can
    only grow for a month. Old months are left alone — pruning is a human
    decision, not a side effect of logging.

Not atomic and not fsynced: a torn tail line is an acceptable loss for an
append-only audit trail, and O_APPEND writes of a single short line are
effectively atomic on the NAS's ext4/btrfs volumes.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Paths we've already complained about, so a broken mount logs once, not every cycle.
_warned = set()


def month_path(base_path, when=None) -> Path:
    """'/logs/scores.jsonl' + 2026-09 -> '/logs/scores-202609.jsonl'."""
    base = Path(base_path)
    if when is None:
        when = datetime.now(timezone.utc)
    return base.with_name(f"{base.stem}-{when:%Y%m}{base.suffix}")


def append_jsonl(base_path, record, when=None) -> bool:
    """Append one JSON line to this month's file. Returns False on any failure
    (already reported to stderr once for that path). Never raises."""
    try:
        path = month_path(base_path, when)
    except Exception as e:                       # pragma: no cover - defensive
        _warn_once(str(base_path), f"bad log path {base_path!r}: {e}")
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        return True
    except Exception as e:
        _warn_once(str(path), f"operational log unavailable at {path}: "
                              f"{type(e).__name__}: {e} (continuing without it)")
        return False


def _warn_once(key, msg):
    if key in _warned:
        return
    _warned.add(key)
    sys.stderr.write(f"WARN: {msg}\n")
    sys.stderr.flush()
