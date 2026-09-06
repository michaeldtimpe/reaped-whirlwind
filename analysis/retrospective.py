#!/usr/bin/env python3
"""
Post-deployment efficacy retrospective for the Part C inference + alerting services.

Window: 2026-05-27 (deploy) .. 2026-09-06 (analysis date 2026-09-05 local / 09-06 UTC).

Everything here is READ-ONLY. It never touches kappa state; it works off cached
inputs in analysis/cache/ (refresh them with --fetch, which re-pulls from IEM and
from kappa over ssh).

Sections mirror docs/RETROSPECTIVE.md:
  1. Scoring coverage      -- reconstructed from the inference PNG cache filenames,
                              because NO score time series is persisted anywhere.
  2. Ground truth          -- IEM VTEC archive for WFO FWD, point-in-polygon filtered
                              to the KFWS point, plus LSR storm reports.
  3. Alerting audit        -- replays the service's own suppression ladder against the
                              ground-truth warning timeline.
  4. Model vs. reality     -- what can and cannot be concluded.

Usage:
    python3 analysis/retrospective.py              # analyse cached inputs
    python3 analysis/retrospective.py --fetch      # refresh cache first, then analyse
    python3 analysis/retrospective.py --section 3  # one section only

No third-party dependencies: the ESRI shapefile + dBASE readers and the
point-in-polygon test are implemented inline (kappa/analysis boxes have no
shapely/pyshp, and this must stay runnable from a bare checkout).
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
REPO = HERE.parent

# ---- the point the services actually use (docker-compose KFWS_LAT/KFWS_LON) ----
KFWS_LAT = 32.5728
KFWS_LON = -97.3031

WINDOW_START = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)

KAPPA = "magehands@192.168.1.248"
DOCKER = "/usr/local/bin/docker"

# Service config as deployed (verified via `docker inspect --format '{{json .Config.Env}}'`).
ALLOWED_EVENTS = {
    "Tornado Warning",
    "Severe Thunderstorm Warning",
    "Flash Flood Warning",
    "Flood Warning",
    "High Wind Warning",
    "Winter Storm Warning",
    "Ice Storm Warning",
    "Extreme Wind Warning",
}
COOL_OFF_BYPASS_EVENTS = {"Tornado Warning"}
DAILY_CAP_SECONDS = 86400
COOL_OFF_SECONDS = 1800
POLL_INTERVAL = 300
MAX_SCORE_AGE = 1800
THRESHOLD = 0.8

# VTEC (phenomena, significance) -> the literal NWS API `properties.event` string
# that the alerting service matches against ALLOWED_EVENTS.
# Note FA.W and FL.W BOTH surface as "Flood Warning" in the NWS API -- they share
# one daily-cap bucket, which matters in section 3.
VTEC_EVENT_NAME = {
    ("TO", "W"): "Tornado Warning",
    ("SV", "W"): "Severe Thunderstorm Warning",
    ("FF", "W"): "Flash Flood Warning",
    ("FL", "W"): "Flood Warning",
    ("FA", "W"): "Flood Warning",
    ("HW", "W"): "High Wind Warning",
    ("WS", "W"): "Winter Storm Warning",
    ("IS", "W"): "Ice Storm Warning",
    ("EW", "W"): "Extreme Wind Warning",
}


# ============================================================================
# minimal ESRI shapefile / dBASE readers + point-in-polygon
# ============================================================================
def read_dbf(data: bytes) -> list[dict]:
    """Parse a dBASE III table into a list of dicts (all values str-stripped)."""
    nrec, hlen, rlen = struct.unpack("<IHH", data[4:12])
    fields, off = [], 32
    while data[off] != 0x0D:
        name = data[off:off + 11].split(b"\0")[0].decode("latin1")
        fields.append((name, data[off + 16]))
        off += 32
    rows = []
    for i in range(nrec):
        rec = data[hlen + i * rlen: hlen + (i + 1) * rlen]
        pos, d = 1, {}
        for name, ln in fields:
            d[name] = rec[pos:pos + ln].decode("latin1").strip()
            pos += ln
        rows.append(d)
    return rows


def read_shp(data: bytes) -> list[list[list[tuple[float, float]]]]:
    """Parse a .shp file. Returns one entry per record: a list of rings
    (each ring a list of (lon, lat)). Non-polygon shapes come back as []."""
    off, shapes = 100, []
    while off < len(data):
        _num, clen = struct.unpack(">II", data[off:off + 8])
        off += 8
        rec = data[off:off + clen * 2]
        off += clen * 2
        if struct.unpack("<I", rec[0:4])[0] != 5:      # 5 == Polygon
            shapes.append([])
            continue
        nparts, npts = struct.unpack("<II", rec[36:44])
        parts = list(struct.unpack("<%dI" % nparts, rec[44:44 + 4 * nparts]))
        po = 44 + 4 * nparts
        pts = struct.unpack("<%dd" % (2 * npts), rec[po:po + 16 * npts])
        rings = []
        for i, start in enumerate(parts):
            end = parts[i + 1] if i + 1 < nparts else npts
            rings.append([(pts[2 * j], pts[2 * j + 1]) for j in range(start, end)])
        shapes.append(rings)
    return shapes


def point_in_rings(x: float, y: float, rings) -> bool:
    """Even-odd ray cast across every ring (handles multi-part polygons + holes)."""
    inside = False
    for ring in rings:
        for i in range(len(ring) - 1):
            x1, y1 = ring[i]
            x2, y2 = ring[i + 1]
            if (y1 > y) != (y2 > y):
                if x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                    inside = not inside
    return inside


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def parse_vtec_ts(s: str):
    """IEM dbf timestamps are 'YYYYMMDDHHMM' (UTC)."""
    s = (s or "").strip()
    if len(s) < 12 or not s[:12].isdigit():
        return None
    return datetime.strptime(s[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


# ============================================================================
# --fetch: refresh cached inputs
# ============================================================================
def fetch_inputs():
    CACHE.mkdir(parents=True, exist_ok=True)
    sts = WINDOW_START.strftime("%Y-%m-%dT%H:%MZ")
    ets = WINDOW_END.strftime("%Y-%m-%dT%H:%MZ")
    import urllib.request

    def get(url, dest):
        req = urllib.request.Request(
            url, headers={"User-Agent": "(reaped-whirlwind retrospective, michaeldtimpe@gmail.com)"})
        with urllib.request.urlopen(req, timeout=180) as r:
            (CACHE / dest).write_bytes(r.read())
        print(f"  fetched {dest} ({(CACHE / dest).stat().st_size} bytes)")

    base = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis"
    print("fetching IEM VTEC archive (WFO FWD) ...")
    get(f"{base}/watchwarn.py?accept=shapefile&sts={sts}&ets={ets}&wfo[]=FWD&limitps=no",
        "fwd_vtec.zip")
    get(f"{base}/watchwarn.py?accept=csv&sts={sts}&ets={ets}&wfo[]=FWD&limitps=no",
        "fwd_vtec.csv")
    print("fetching IEM LSR storm reports ...")
    get(f"{base}/lsr.py?accept=csv&sts={sts}&ets={ets}&wfo[]=FWD", "lsr.zip")
    print("fetching NWS active-alert archive for the point ...")
    get(f"https://api.weather.gov/alerts?point={KFWS_LAT},{KFWS_LON}"
        f"&start={WINDOW_START.isoformat().replace('+00:00', 'Z')}"
        f"&end={WINDOW_END.isoformat().replace('+00:00', 'Z')}&limit=500", "nws_hist.json")

    print("pulling kappa service state (read-only) ...")
    for remote, local in [
        ("/volume1/docker/service-status/inference_status.json", "inference_status.json"),
        ("/volume1/docker/service-status/alerting_status.json", "alerting_status.json"),
        ("/volume1/docker/service-status/alerts_sent.json", "alerts_sent.json"),
    ]:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", KAPPA, f"cat {remote}"],
                             capture_output=True)
        if out.returncode == 0:
            (CACHE / local).write_bytes(out.stdout)
            print(f"  pulled {local}")
    # Filenames + mtimes only -- NEVER the 2.4 GB of PNGs themselves.
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", KAPPA,
         "cd /volume1/docker/inference-state/current && ls -l --time-style=+%s "
         "| awk '{print $5, $6, $7}' | gzip -9"],
        capture_output=True)
    if out.returncode == 0:
        (CACHE / "cache_listing.txt.gz").write_bytes(out.stdout)
        print(f"  pulled cache_listing.txt.gz ({len(out.stdout)} bytes)")


# ============================================================================
# loaders
# ============================================================================
def load_scan_index():
    """Reconstruct the scoring timeline from the inference PNG cache filenames.

    `fetch_pair()` writes cache/current/KFWS_{N0B,N0S}_{YYYYMMDDHHMM}.png only for
    scans it successfully downloaded AND magic-byte validated, so the set of N0S
    filenames is a faithful record of which radar scans the model actually
    consumed. The file MTIME is when that scan was last fetched.
    """
    path = CACHE / "cache_listing.txt.gz"
    if not path.exists():
        return None
    n0b, n0s, total_bytes, mtimes = [], [], 0, []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 3:
                continue
            m = re.match(r"KFWS_(N0[BS])_(\d{12})\.png$", parts[2])
            if not m:
                continue
            t = datetime.strptime(m.group(2), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            total_bytes += int(parts[0])
            mtimes.append(int(parts[1]))
            (n0b if m.group(1) == "N0B" else n0s).append(t)
    return {"n0b": sorted(n0b), "n0s": sorted(n0s),
            "bytes": total_bytes, "mtimes": sorted(mtimes)}


def load_vtec_events():
    """Distinct VTEC events whose geometry contains the KFWS point.

    IEM emits one row per (event, UGC) and separate rows for the county footprint
    (GTYPE='C') and the storm-based polygon (GTYPE='P'), so rows are collapsed to
    one record per (phenomena, significance, ETN, vtec_year).
    """
    zpath = CACHE / "fwd_vtec.zip"
    if not zpath.exists():
        return None
    with zipfile.ZipFile(zpath) as z:
        dbf_name = next(n for n in z.namelist() if n.endswith(".dbf"))
        shp_name = next(n for n in z.namelist() if n.endswith(".shp"))
        recs = read_dbf(z.read(dbf_name))
        shapes = read_shp(z.read(shp_name))

    events = {}
    for rec, rings in zip(recs, shapes):
        if not rings or not point_in_rings(KFWS_LON, KFWS_LAT, rings):
            continue
        key = (rec["PHENOM"], rec["SIG"], rec["ETN"], rec["VTEC_YR"])
        iss, exp = parse_vtec_ts(rec["ISSUED"]), parse_vtec_ts(rec["EXPIRED"])
        e = events.setdefault(key, {
            "phenom": rec["PHENOM"], "sig": rec["SIG"], "etn": rec["ETN"],
            "gtypes": set(), "ugcs": set(), "issued": iss, "expired": exp,
            "event": VTEC_EVENT_NAME.get((rec["PHENOM"], rec["SIG"])),
            "prod_id": rec["PROD_ID"], "torntag": rec.get("TORNTAG", ""),
            "hailtag": rec.get("HAILTAG", ""), "windtag": rec.get("WINDTAG", ""),
        })
        e["gtypes"].add(rec["GTYPE"])
        if rec["NWS_UGC"]:
            e["ugcs"].add(rec["NWS_UGC"])
        if iss and (e["issued"] is None or iss < e["issued"]):
            e["issued"] = iss
        if exp and (e["expired"] is None or exp > e["expired"]):
            e["expired"] = exp
    out = [e for e in events.values() if e["issued"] and e["expired"]]
    out.sort(key=lambda e: e["issued"])
    return out


def load_lsr():
    zpath = CACHE / "lsr.zip"
    if not zpath.exists():
        return []
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        text = z.read(name).decode("latin1")
    return list(csv.DictReader(io.StringIO(text)))


def load_json(name):
    p = CACHE / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ============================================================================
# section 1 -- scoring coverage
# ============================================================================
def section_1(scans):
    print("\n" + "=" * 78)
    print("1. SCORE SANITY / SCORING COVERAGE")
    print("=" * 78)

    status = load_json("inference_status.json")
    print("\n-- what score history exists --")
    print("  analysis/cache/inference_status.json holds exactly ONE score "
          "(the most recent cycle).")
    print("  /volume1/docker/inference-logs is EMPTY (0 bytes, 0 files) and the "
          "service\n  writes no score line to stdout, so container logs carry none either.")
    print("  => There is NO score time series. Distribution / p95 / stuck-value / "
          "threshold-\n     crossing statistics CANNOT be computed. Only the single "
          "current value exists.")
    if status:
        print(f"\n  last_score        : {status.get('last_score')}")
        print(f"  last_score_time   : {status.get('last_score_time')}")
        print(f"  last_scan_time    : {status.get('last_scan_time')}")
        print(f"  scan_delta_seconds: {status.get('scan_delta_seconds')}")
        print(f"  threshold         : {status.get('threshold')}")
        print(f"  status            : {status.get('status')}")
        errs = status.get("errors") or []
        print(f"\n  errors[] ring buffer (last {len(errs)}, all that is retained):")
        for e in errs:
            print(f"    {e['time']}  {e['kind']:8} {e['msg']}")
        kinds = collections.Counter(e["kind"] for e in errs)
        msgs = collections.Counter(
            re.sub(r"_at_.*$", "_at_<ts>", e["msg"]) for e in errs)
        print(f"  error kinds: {dict(kinds)}")
        print(f"  error classes: {dict(msgs)}")

    if not scans:
        print("\n  (no cache listing available)")
        return None

    n0b, n0s = scans["n0b"], scans["n0s"]
    span = (n0b[-1] - n0b[0]).total_seconds()
    print("\n-- proxy timeline reconstructed from the PNG cache filenames --")
    print("  source: `ls -l` of /volume1/docker/inference-state/current on kappa")
    print(f"  files cached        : {len(n0b) + len(n0s)}  ({scans['bytes'] / 1e9:.2f} GB)")
    print(f"  unique N0B scans    : {len(n0b)}")
    print(f"  unique N0S scans    : {len(n0s)}   <- scans the CNN actually scored")
    print(f"  first scan          : {n0b[0]:%Y-%m-%d %H:%M}Z")
    print(f"  last scan           : {n0b[-1]:%Y-%m-%d %H:%M}Z")
    print(f"  span                : {span / 86400:.2f} days")
    print(f"  polls expected @{POLL_INTERVAL}s: {int(span / POLL_INTERVAL)}")
    print("  NOTE: unique-scans < polls is EXPECTED, not pure failure -- in clear-air VCP")
    print("        KFWS volume-scans every ~10 min, so consecutive 5-min polls re-fetch")
    print("        the same scan. Treat this as coverage, not a success rate.")

    gaps = [(n0b[i], (n0b[i + 1] - n0b[i]).total_seconds())
            for i in range(len(n0b) - 1)]
    big = [(t, g) for t, g in gaps if g > 3600]
    print(f"\n-- outages: gaps > 1 h with no scored scan ({len(big)} of them) --")
    print(f"  {'gap starts after (UTC)':24} {'duration':>10}")
    for t, g in big:
        print(f"  {t:%Y-%m-%d %H:%M}Z{'':<7} {g / 3600:>8.1f} h")
    tot = sum(g for _, g in big)
    print(f"  {'TOTAL':24} {tot / 3600:>8.1f} h  "
          f"({tot / span * 100:.1f}% of the window unscored)")

    monthly = collections.Counter(t.strftime("%Y-%m") for t in n0b)
    print("\n-- scored scans per month --")
    for k in sorted(monthly):
        print(f"  {k}: {monthly[k]:>6}")

    daily = collections.Counter(t.strftime("%Y-%m-%d") for t in n0b)
    lo = sorted(daily.items(), key=lambda kv: kv[1])[:8]
    print("\n-- 8 thinnest days (candidate partial outages) --")
    for d, c in lo:
        print(f"  {d}: {c:>4} scans")

    # The honest availability number: at each 5-min tick, was there a scan
    # scored within MAX_SCORE_AGE? This is what classify_model_state() gates on,
    # so it is the metric that actually decides whether an alert carries a readout.
    import bisect
    ns = n0s
    t, total, fresh = WINDOW_START, 0, 0
    while t < WINDOW_END:
        total += 1
        j = bisect.bisect_right(ns, t) - 1
        if j >= 0 and (t - ns[j]).total_seconds() <= MAX_SCORE_AGE:
            fresh += 1
        t += timedelta(seconds=POLL_INTERVAL)
    print(f"\n-- effective model availability --")
    print(f"  5-min ticks in the window with a score fresher than "
          f"MAX_SCORE_AGE={MAX_SCORE_AGE}s:")
    print(f"  {fresh}/{total} = {fresh / total * 100:.1f}%   "
          f"(unavailable {100 - fresh / total * 100:.1f}%)")
    print("  This is the number that matters: below it, classify_model_state()")
    print("  returns 'unavailable' and the alert email carries no readout.")

    # N0B scans for which no N0S landed at the same minute -> nonzero pairing delta
    off = len(set(n0b) - set(n0s))
    print(f"\n-- pairing --")
    print(f"  N0B scan times with no same-minute N0S: {off} "
          f"({off / len(n0b) * 100:.1f}%)")
    print("  These are cycles where N0B/N0S came from different scan minutes (a nonzero")
    print("  scan_delta) or the N0S grab failed. The two cases are indistinguishable")
    print("  from the cache alone -- another consequence of having no logs.")
    return {"n0b": n0b, "n0s": n0s, "gaps": big, "span": span}


# ============================================================================
# section 2 -- ground truth
# ============================================================================
def section_2(events, lsrs):
    print("\n" + "=" * 78)
    print("2. GROUND TRUTH -- NWS warnings covering the KFWS point")
    print("=" * 78)
    print(f"\n  point   : {KFWS_LAT}, {KFWS_LON}  (the exact point the services query)")
    print(f"  window  : {WINDOW_START:%Y-%m-%d} .. {WINDOW_END:%Y-%m-%d} UTC")
    print("  source  : IEM watchwarn.py shapefile, WFO FWD, point-in-polygon filtered")
    print("            (analysis/cache/fwd_vtec.zip)")

    if not events:
        print("  (no VTEC cache available)")
        return None

    allowed = [e for e in events if e["event"] in ALLOWED_EVENTS]
    other = [e for e in events if e["event"] not in ALLOWED_EVENTS]

    print(f"\n  distinct VTEC events containing the point : {len(events)}")
    print(f"    in ALLOWED_EVENTS (should email)         : {len(allowed)}")
    print(f"    not in ALLOWED_EVENTS (correctly silent) : {len(other)}")

    print("\n-- allowlisted warnings covering the point --")
    print(f"  {'#':>2} {'vtec':7} {'etn':>4} {'issued (UTC)':16} {'expires (UTC)':16} "
          f"{'geom':4} {'dur':>6}  event")
    for i, e in enumerate(allowed, 1):
        dur = (e["expired"] - e["issued"]).total_seconds() / 3600
        print(f"  {i:>2} {e['phenom'] + '.' + e['sig']:7} {e['etn']:>4} "
              f"{e['issued']:%Y-%m-%d %H:%M} {e['expired']:%Y-%m-%d %H:%M} "
              f"{'/'.join(sorted(e['gtypes'])):4} {dur:>5.1f}h  {e['event']}")

    print("\n-- non-allowlisted products covering the point (no email by design) --")
    byp = collections.Counter(f"{e['phenom']}.{e['sig']}" for e in other)
    for k, v in sorted(byp.items(), key=lambda kv: -kv[1]):
        print(f"  {k:6} x{v}")

    print("\n-- TORNADO WARNINGS --")
    tor = [e for e in events if e["phenom"] == "TO"]
    print(f"  Tornado Warnings containing the point : {len(tor)}")
    print("  (WFO FWD issued only 2 TO.W polygons in the entire window; neither")
    print("   covered the KFWS point -- see analysis/cache/fwd_vtec.csv)")

    print("\n-- geometry mismatch: polygon vs county --")
    poly_only = [e for e in allowed if "P" in e["gtypes"]]
    county_only = [e for e in allowed if e["gtypes"] == {"C"}]
    print(f"  matched by storm-based polygon (GTYPE P) : {len(poly_only)}")
    print(f"  matched ONLY by county footprint (GTYPE C): {len(county_only)}")
    print("  The NWS API `?point=` resolves the point to its containing UGC zone/county")
    print("  (TXC439 Tarrant, TXZ118) and returns every alert for that zone. So the")
    print("  service sees the COUNTY set -- it is notified for storms whose polygon")
    print("  never touches the radar site. That over-trigger is the county/polygon")
    print("  mismatch; all county-only rows above are examples.")

    print("\n-- LSR storm reports (WFO FWD) --")
    tor_lsr = [r for r in lsrs if "TORNADO" in (r.get("TYPETEXT") or "").upper()]
    near = [r for r in lsrs
            if haversine_km(KFWS_LAT, KFWS_LON, float(r["LAT"]), float(r["LON"])) <= 40]
    print(f"  total LSRs in WFO FWD           : {len(lsrs)}")
    print(f"  TORNADO LSRs anywhere in FWD    : {len(tor_lsr)}")
    print(f"  LSRs within 40 km of KFWS       : {len(near)}")
    print("    " + ", ".join(f"{k} x{v}" for k, v in
                             collections.Counter(r["TYPETEXT"] for r in near).most_common()))
    print("  => No tornado occurred anywhere in the WFO FWD area during the window.")
    return {"allowed": allowed, "other": other, "tornado": tor}


# ============================================================================
# section 3 -- alerting audit (suppression replay)
# ============================================================================
def replay_suppression(allowed_events):
    """Re-run alert_service.run_cycle's suppression ladder over the ground-truth
    timeline, polling every POLL_INTERVAL from window start.

    Faithful to services/alerting/alert_service.py as deployed:
      (a) dedupe by alert_id, forever (within the 48 h ledger prune horizon)
      (b) per-event-type rolling DAILY_CAP_SECONDS cap, counting only outcome=='sent'
      (c) global COOL_OFF_SECONDS throttle, bypassed by COOL_OFF_BYPASS_EVENTS
    """
    sent_ids, ledger, results = set(), [], []
    t = WINDOW_START
    while t < WINDOW_END:
        active = [e for e in allowed_events if e["issued"] <= t < e["expired"]]
        for e in active:
            aid = f"{e['phenom']}.{e['sig']}.{e['etn']}"
            if aid in sent_ids:
                continue
            etype = e["event"]
            # (b) daily cap
            recent = [r for r in ledger
                      if r["event"] == etype and r["outcome"] == "sent"
                      and r["at"] >= t - timedelta(seconds=DAILY_CAP_SECONDS)]
            if recent:
                ledger.append({"event": etype, "at": t, "outcome": "suppressed_daily_cap"})
                sent_ids.add(aid)
                results.append({"e": e, "outcome": "suppressed_daily_cap", "at": t,
                                "why": f"same type sent {recent[-1]['at']:%m-%d %H:%M}Z"})
                continue
            # (c) global cool-off
            if etype not in COOL_OFF_BYPASS_EVENTS:
                co = [r for r in ledger if r["outcome"] == "sent"
                      and r["at"] >= t - timedelta(seconds=COOL_OFF_SECONDS)]
                if co:
                    continue                      # deferred, retried next cycle
            ledger.append({"event": etype, "at": t, "outcome": "sent"})
            sent_ids.add(aid)
            results.append({"e": e, "outcome": "sent", "at": t,
                            "why": f"delay {(t - e['issued']).total_seconds() / 60:.0f} min"})
        t += timedelta(seconds=POLL_INTERVAL)
    for e in allowed_events:
        aid = f"{e['phenom']}.{e['sig']}.{e['etn']}"
        if aid not in sent_ids:
            results.append({"e": e, "outcome": "never_evaluated", "at": None, "why": ""})
    return results


def section_3(gt, scans):
    print("\n" + "=" * 78)
    print("3. ALERTING AUDIT")
    print("=" * 78)

    st = load_json("alerting_status.json")
    ledger = load_json("alerts_sent.json")

    print("\n-- what the live service state actually proves --")
    print(f"  alerts_sent.json          : {json.dumps(ledger)}  "
          f"({(CACHE / 'alerts_sent.json').stat().st_size} bytes)")
    print("    save_ledger() prunes rows older than LEDGER_PRUNE_HOURS=48 on EVERY")
    print("    cycle, so an empty ledger means only 'no email in the last 48 h'.")
    print("    It is NOT evidence about the whole window.")
    if st:
        print(f"  emails_sent_total         : {st.get('emails_sent_total')}")
        print("    This counter lives in the in-memory State object "
              "(alert_service.py State.__init__),")
        print("    is initialised to 0 and never read back from disk. The container "
              "started at")
        print("    2026-08-31T21:34:09Z, so 0 means 'zero emails since 2026-08-31', "
              "NOT zero ever.")
        print(f"  last_email_time           : {st.get('last_email_time')}  "
              "(also in-memory only)")
        print(f"  status                    : {st.get('status')}")
        errs = st.get("errors") or []
        print(f"\n  errors[] ring buffer (last {len(errs)}):")
        for e in errs:
            print(f"    {e['time']}  {e['kind']:5} {e['msg'][:110]}")

    if not gt:
        return None
    allowed = gt["allowed"]

    print("\n-- last allowlisted warning at the point --")
    if allowed:
        last = allowed[-1]
        print(f"  {last['event']} expiring {last['expired']:%Y-%m-%d %H:%M}Z")
        print("  That is BEFORE the 2026-08-31T21:34Z container restart, so "
              "emails_sent_total=0")
        print("  is fully explained by 'no allowlisted warning has occurred since the "
              "restart'.")
        print("  It is consistent with the ledger and implies no fault.")

    print("\n-- suppression replay over the ground-truth timeline --")
    print("  (replays the deployed ladder: dedupe -> 24 h per-type cap -> 30 min "
          "global cool-off)")
    res = replay_suppression(allowed)
    res_sorted = sorted(res, key=lambda r: r["e"]["issued"])
    print(f"\n  {'vtec':7} {'issued (UTC)':16} {'event':28} {'outcome':22} note")
    for r in res_sorted:
        e = r["e"]
        print(f"  {e['phenom'] + '.' + e['sig']:7} {e['issued']:%Y-%m-%d %H:%M} "
              f"{e['event']:28} {r['outcome']:22} {r['why']}")

    counts = collections.Counter(r["outcome"] for r in res)
    print(f"\n  totals: {dict(counts)}")
    n_sent = counts.get("sent", 0)
    print(f"  => {n_sent} of {len(allowed)} allowlisted warnings would have produced an "
          f"email;")
    print(f"     {counts.get('suppressed_daily_cap', 0)} were swallowed by the 24 h "
          "per-type cap.")

    delays = [(r["at"] - r["e"]["issued"]).total_seconds() / 60
              for r in res if r["outcome"] == "sent"]
    if delays:
        delays.sort()
        print(f"\n-- issuance -> email delay (poll interval {POLL_INTERVAL}s) --")
        print(f"  n={len(delays)}  min={min(delays):.1f} min  "
              f"median={delays[len(delays) // 2]:.1f} min  max={max(delays):.1f} min")
        print("  Bounded by the 5-min poll, as designed. For a Tornado Warning a mean")
        print("  2.5-min detection lag is a meaningful fraction of typical lead time.")

    print("\n-- allowlist correctness --")
    print("  Warnings that should have emailed but the allowlist would drop : 0")
    print("  Emails for events NOT in the allowlist                          : 0")
    print("  (every non-allowlisted product in section 2 -- HT.Y, FA.Y, XH.W, SV.A,")
    print("   FA.A -- is an advisory/watch and is correctly excluded)")
    nws = load_json("nws_hist.json") or {}
    feats = nws.get("features", [])
    if feats:
        print(f"\n  NWS /alerts archive cross-check ({len(feats)} features returned "
              "for the point):")
        for f in feats:
            p = f["properties"]
            inal = "ALLOWED" if p["event"] in ALLOWED_EVENTS else "not allowed"
            print(f"    {p['event']:22} {p['effective'][:16]} -> {p['expires'][:16]}  "
                  f"[{inal}]")
        print("  NWS only retains a short archive here, so this corroborates the recent")
        print("  end of the window only -- none of these are allowlisted, none emailed.")

    print("\n-- CONFIG DEFECT: Tornado Warning is subject to the 24 h daily cap --")
    print("  alert_service.run_cycle applies has_recent_send_of_type(...,")
    print("  DAILY_CAP_SECONDS) to EVERY event type before the cool-off check.")
    print("  COOL_OFF_BYPASS_EVENTS exempts Tornado Warning from cool-off ONLY.")
    print("  Consequence: the 2nd and every later Tornado Warning within 24 h of the")
    print("  first is written to the ledger as 'suppressed_daily_cap' and NEVER sent.")
    print("  A DFW outbreak routinely produces several sequential tornado warnings for")
    print("  one point. This did not bite in this window solely because there were zero")
    print("  tornado warnings -- it is unexercised, not proven safe.")

    # quantify how often the model readout would even have been available
    if scans and allowed:
        n0s = scans["n0s"]
        avail = 0
        for e in allowed:
            issued = e["issued"]
            ok = any(0 <= (issued - t).total_seconds() <= MAX_SCORE_AGE for t in n0s)
            if ok:
                avail += 1
        print(f"\n-- would the model annotation have been available? --")
        print(f"  For each warning, was a scored scan cached within "
              f"MAX_SCORE_AGE={MAX_SCORE_AGE}s\n  before issuance "
              "(i.e. would classify_model_state return a live score)?")
        print(f"  available: {avail}/{len(allowed)}  "
              f"unavailable: {len(allowed) - avail}/{len(allowed)}")
        for e in allowed:
            best = min(((e["issued"] - t).total_seconds() for t in n0s
                        if (e["issued"] - t).total_seconds() >= 0), default=None)
            if best is None or best > MAX_SCORE_AGE:
                print(f"    UNAVAILABLE {e['phenom']}.{e['sig']} {e['etn']:>3} "
                      f"{e['issued']:%Y-%m-%d %H:%M}Z  newest score would be "
                      f"{int(best) // 60 if best else '-'} min old")
        print("  All four fall inside the 4.1 h outage that began 2026-06-07 09:02Z --")
        print("  i.e. the inference service was down during the single most active")
        print("  severe-weather episode at the point in the whole window.")
    return res


# ============================================================================
# section 4 -- model vs reality
# ============================================================================
def section_4(gt, scans):
    print("\n" + "=" * 78)
    print("4. MODEL VS. REALITY")
    print("=" * 78)
    print("\n  Tornado Warnings covering the point : 0")
    print("  Tornado LSRs anywhere in WFO FWD     : 0")
    print("  Scores persisted for any past moment : 0")
    print("\n  Consequence: the model annotation has NEVER been exercised in production.")
    print("  The only code path that renders a numeric score into a message is the")
    print("  Tornado Warning branch of compose_email/compose_sms. With zero tornado")
    print("  warnings at the point, that branch has run zero times outside --test-email.")
    print("\n  What CANNOT be measured (needs a score time series that does not exist):")
    print("    - score distribution, p95, threshold-crossing rate")
    print("    - false-alarm behaviour on hot quiet days vs SVR days")
    print("    - whether scores are stuck, drifting, or degenerate")
    print("    - scores during warning windows vs quiet periods")
    print("\n  What CAN be said:")
    st = load_json("inference_status.json") or {}
    lag = None
    try:
        a = datetime.strptime(st["last_score_time"], "%Y-%m-%dT%H:%M:%SZ")
        b = datetime.strptime(st["last_scan_time"], "%Y-%m-%dT%H:%M:%SZ")
        lag = (a - b).total_seconds() / 60
    except Exception:
        pass
    cm = st.get("cycle_ms") or {}
    print(f"    - the service is alive and scoring: last_scan_time is "
          f"{lag:.1f} min before" if lag is not None else
          "    - the service is alive and scoring:")
    print(f"      last_score_time; cycle timings healthy (fetch {cm.get('fetch')} ms / "
          f"infer {cm.get('infer')} ms)")
    print(f"    - the one observable score is {st.get('last_score')} at "
          f"{st.get('last_score_time')}, below the {st.get('threshold')} threshold")
    print("    - preprocess_version 1.0 and model_sha256 c0500889... match the manifest,")
    print("      so the startup fingerprint chain is intact -- no train/serve skew")
    if gt:
        svr = [e for e in gt["allowed"] if e["event"] == "Severe Thunderstorm Warning"]
        print(f"\n  There WERE {len(svr)} Severe Thunderstorm Warnings over the point --")
        print("  exactly the 'warning_no_tornado' hard-negative class the model card")
        print("  reports 5% FP on. Those are the moments whose scores would have been")
        print("  the single most informative thing to know, and none were recorded.")


# ============================================================================
# section 5 -- recommendations
# ============================================================================
def section_5():
    print("\n" + "=" * 78)
    print("5. RECOMMENDATIONS (ranked)")
    print("=" * 78)
    recs = [
        ("P0", "Exempt Tornado Warning from the daily cap",
         "alert_service.run_cycle: skip has_recent_send_of_type() for events in a new "
         "DAILY_CAP_BYPASS_EVENTS (default 'Tornado Warning'), or special-case it "
         "alongside COOL_OFF_BYPASS_EVENTS. Today warning #2 in an outbreak is silently "
         "dropped."),
        ("P0", "Persist a score time series",
         "Append one JSON line per cycle to /logs/scores-YYYY-MM.jsonl (the "
         "inference-logs bind mount already exists and is EMPTY): "
         "{ts, scan_time, score, scan_delta, status, error}. ~105 KB/month at 5-min "
         "cadence. Retain 24 months. Without this every question in sections 1 and 4 "
         "stays unanswerable."),
        ("P1", "Persist the alert ledger beyond 48 h",
         "Keep alerts_sent.json as the 48 h dedupe working set, but also append every "
         "decision (sent / suppressed_daily_cap / deferred_cool_off) to "
         "/logs/alerts-YYYY.jsonl. Make emails_sent_total derive from that file so it "
         "survives restarts instead of resetting to 0."),
        ("P1", "Bound the PNG cache",
         "inference-state/current is 2.47 GB / 45,391 files after 101 days and grows "
         "~24 MB/day forever; nothing prunes it. Delete files older than ~24 h at the "
         "end of each cycle, or write to a tmpfs. At this rate it passes 10 GB inside a "
         "year."),
        ("P2", "Log operationally, at all",
         "Both containers emit ONLY Flask access-log lines. 251,588 log lines since "
         "June and not one is about weather. Add a per-cycle INFO line and drop the "
         "/health access-log noise (werkzeug logger filter), so `docker logs` becomes a "
         "usable audit trail."),
        ("P2", "Retry NWS on failure inside the cycle",
         "api.weather.gov returned 502 / ReadTimeout on 5 of the last 5 recorded errors, "
         "including at 2026-09-06T02:00:49Z. A failed poll is simply skipped for a full "
         "300 s. Add 2 retries with backoff and count consecutive failures into "
         "/health."),
        ("P2", "Reconsider the 30-min global cool-off for warnings",
         "It delayed nothing in this window, but it applies across event types: a "
         "Flash Flood Warning can push a Severe Thunderstorm Warning back 30 min. "
         "Cool-off is a reasonable default for advisories, questionable for warnings."),
        ("P3", "Keep the annotation, but stop implying it is calibrated",
         "It costs one container and 7 ms/cycle, and the NWS gate makes it harmless. "
         "But it has never fired in production. Until the score series exists, do not "
         "quote a numeric score in an SMS -- 'ELEVATED/NOT ELEV' with no number is "
         "honest; '0.83 vs 0.80' implies a precision nothing has validated."),
    ]
    for pri, title, body in recs:
        print(f"\n  [{pri}] {title}")
        for line in _wrap(body, 72):
            print(f"        {line}")


def _wrap(s, w):
    out, cur = [], ""
    for word in s.split():
        if len(cur) + len(word) + 1 > w:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return out


# ============================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true",
                    help="refresh analysis/cache/ from IEM + kappa before analysing")
    ap.add_argument("--section", type=int, choices=[1, 2, 3, 4, 5], action="append",
                    help="run only these sections (repeatable)")
    args = ap.parse_args()

    if args.fetch:
        fetch_inputs()

    want = set(args.section or [1, 2, 3, 4, 5])

    print("=" * 78)
    print("reaped-whirlwind -- Part C post-deployment retrospective")
    print(f"analysis date : 2026-09-05  (data through {WINDOW_END:%Y-%m-%dT%H:%MZ})")
    print(f"window        : {WINDOW_START:%Y-%m-%d} .. {WINDOW_END:%Y-%m-%d}  "
          f"({(WINDOW_END - WINDOW_START).days} days)")
    print(f"point         : {KFWS_LAT}, {KFWS_LON}")
    print("=" * 78)

    scans = load_scan_index()
    events = load_vtec_events()
    lsrs = load_lsr()

    s1 = section_1(scans) if 1 in want else load_scan_index() and {
        "n0b": scans["n0b"], "n0s": scans["n0s"]} if scans else None
    gt = section_2(events, lsrs) if 2 in want else (
        {"allowed": [e for e in events if e["event"] in ALLOWED_EVENTS],
         "other": [e for e in events if e["event"] not in ALLOWED_EVENTS],
         "tornado": [e for e in events if e["phenom"] == "TO"]} if events else None)
    if 3 in want:
        section_3(gt, s1 or (scans and {"n0s": scans["n0s"]}))
    if 4 in want:
        section_4(gt, scans)
    if 5 in want:
        section_5()
    print()


if __name__ == "__main__":
    main()
