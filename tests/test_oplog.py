"""common.oplog: one INFO line per cycle, no /health access noise."""
import io
import logging

from common import oplog


def _fresh_root():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    if hasattr(root, "_reaped_configured"):
        del root._reaped_configured
    wz = logging.getLogger("werkzeug")
    for f in list(wz.filters):
        wz.removeFilter(f)
    return root


def test_health_probes_are_dropped_but_other_werkzeug_lines_survive():
    _fresh_root()
    buf = io.StringIO()
    oplog.setup_logging(stream=buf)
    wz = logging.getLogger("werkzeug")
    wz.info('172.18.0.1 - - [06/Sep/2026 12:00:00] "GET /health HTTP/1.1" 200 -')
    wz.info('172.18.0.1 - - [06/Sep/2026 12:00:01] "GET /nope HTTP/1.1" 404 -')
    logging.getLogger("alerting").info("cycle status=running active=0")
    out = buf.getvalue()
    assert "/health" not in out
    assert "/nope" in out
    assert "cycle status=running" in out
    assert "Z INFO alerting:" in out           # UTC stamp + level + logger name


def test_setup_is_idempotent():
    _fresh_root()
    buf = io.StringIO()
    oplog.setup_logging(stream=buf)
    oplog.setup_logging(stream=buf)
    logging.getLogger("x").warning("once")
    assert buf.getvalue().count("once") == 1
    assert sum(isinstance(f, oplog.DropHealthProbes)
               for f in logging.getLogger("werkzeug").filters) == 1
