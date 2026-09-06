"""Status-file helpers shared by every service that writes service-status JSON.

Lifted verbatim from the byte-identical copies that lived in
`services/inference/inference_service.py` and `services/alerting/alert_service.py`
so the dashboard never reads a half-written status file.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def utc_iso(dt=None) -> str:
    """UTC timestamp as 'YYYY-MM-DDTHH:MM:SSZ'. Naive datetimes are assumed UTC."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path, obj, *, fsync: bool = True) -> None:
    """Write `obj` as JSON to `path` atomically.

    Serialises into a sibling '.tmp' file (same directory, so os.replace stays a
    rename within one filesystem) and swaps it in. A crash mid-write leaves the
    previous file intact; a reader never sees a partial document.
    `fsync=False` skips the flush-to-disk for hot paths that don't need it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray .tmp behind for the dashboard's folder listings.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def deep_copy_json(obj):
    """Snapshot a JSON-serialisable structure. Raises TypeError on non-JSON data,
    which is deliberate: these dicts are about to be written to a status file."""
    return json.loads(json.dumps(obj))
