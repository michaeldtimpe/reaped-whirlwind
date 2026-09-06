"""Operational (human-readable) logging for the Part C services.

`docker logs inference-service` used to be 100 % werkzeug `GET /health` access
lines — 251,588 of them in three months, none about weather (retrospective
rec #5). `setup_logging()` gives each service one INFO line per cycle on stderr
and drops the /health access noise so `docker logs` reads as an audit trail.

The JSONL files in `common.jsonlog` remain the durable record; this is the
at-a-glance one. Stdlib only.
"""
import logging
import sys

_FORMAT = "%(asctime)sZ %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


class DropHealthProbes(logging.Filter):
    """Filter out werkzeug access-log lines for the /health endpoint only."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:                    # pragma: no cover - defensive
            return True
        return "/health" not in msg


def setup_logging(level=logging.INFO, stream=None) -> logging.Logger:
    """Idempotent. Root logger -> stderr with a UTC timestamp; werkzeug access
    lines for /health are suppressed (other werkzeug output, e.g. startup
    banners and errors, still shows). Returns the root logger."""
    root = logging.getLogger()
    if not getattr(root, "_reaped_configured", False):
        handler = logging.StreamHandler(stream or sys.stderr)
        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        formatter.converter = __import__("time").gmtime
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root._reaped_configured = True
    root.setLevel(level)
    wz = logging.getLogger("werkzeug")
    if not any(isinstance(f, DropHealthProbes) for f in wz.filters):
        wz.addFilter(DropHealthProbes())
    return root
