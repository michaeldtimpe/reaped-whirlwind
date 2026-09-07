#!/usr/bin/env python3
"""
Domain-matched radar collector for reaped-whirlwind (Part B).

Pulls per-station IEM RIDGE **N0B** (super-res reflectivity) + **N0S** (storm-relative
velocity) — the consistent 2020-2025 / live-era product pair — for:
  POSITIVES : SPC confirmed tornadoes
  NEGATIVES : SPC hail + SPC wind (severe, non-tornadic) AND
              tornado-WARNINGS with NO confirmed tornado (the hardest negatives) AND
              QUIET sky: random station/time with no SPC report or TO.W within
              QUIET_EXCL_KM / ±QUIET_EXCL_HOURS. Without this class the model's
              answer for an empty scan is undefined — models/v1 and the v2 retrain
              both scored a near-empty night 0.6+ (docs/MODEL_CARD.md).

Stations: all CONUS WSR-88D (fetched live from IEM, with an embedded fallback).
Products have different cadences, so we list each product's archive dir and pair
nearest-available frames. 150 km range cutoff.

Full run (M4 Pro): see run_collection.sh.  Quick check: --sample 50
"""
import argparse, csv, io, math, os, random, time, zipfile, json, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import requests

from iem import (ARCH, REFL_PROD, VEL_PROD,
                 list_times, nearest, fetch_png, fetch_text, validate_png_on_disk)

SPC_TORNADO_URL  = "https://www.spc.noaa.gov/wcm/data/1950-2025_actual_tornadoes.csv"
SPC_HAIL_URL_FMT = "https://www.spc.noaa.gov/wcm/data/{year}_hail.csv"
SPC_WIND_URL_FMT = "https://www.spc.noaa.gov/wcm/data/{year}_wind.csv"
STATIONS_GEOJSON = "https://mesonet.agron.iastate.edu/geojson/network/NEXRAD.geojson"
WATCHWARN = ("https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py?accept=shapefile"
             "&year1={y}&month1=1&day1=1&hour1=0&minute1=0&year2={y2}&month2=1&day2=1&hour2=0&minute2=0"
             "&limitps=yes&phenomena=TO&significance=W")

WINDOW_BEFORE_MIN, WINDOW_AFTER_MIN = 30, 10
VEL_TOL_MIN = 8
REQUEST_DELAY_SEC = 0.25
MAX_RANGE_KM = 150
FETCH_WORKERS = 4            # concurrent PNG fetches per event (IEM-polite; was 1)
QUIET_EXCL_KM, QUIET_EXCL_HOURS = 200, 6
QUIET_CENTER_MAX_KM = 100    # crop centre: random point this close to the station

# Embedded fallback if the live station list can't be fetched (central/southern US).
FALLBACK_STATIONS = [
    ("KTLX",35.3331,-97.2778),("KICT",37.6544,-97.4428),("KFWS",32.5728,-97.3031),
    ("KEAX",38.8100,-94.2644),("KLSX",38.6989,-90.6828),("KIND",39.7075,-86.2803),
    ("KOHX",36.2472,-86.5625),("KBMX",33.1722,-86.7700),("KLCH",30.1253,-93.2158),
    ("KHGX",29.4719,-95.0792),("KMPX",44.8489,-93.5656),("KLOT",41.6044,-88.0847),
]
STATIONS = FALLBACK_STATIONS


def load_stations():
    try:
        d = json.load(urllib.request.urlopen(STATIONS_GEOJSON, timeout=30))
        out = []
        for f in d["features"]:
            lon, lat = f["geometry"]["coordinates"]
            if -125 < lon < -66 and 24 < lat < 50:        # CONUS bbox
                out.append(("K" + f["id"], lat, lon))
        return out or FALLBACK_STATIONS
    except Exception as e:
        print(f"  station list fetch failed ({e}); using fallback"); return FALLBACK_STATIONS


def haversine_km(a, b, c, d):
    R = 6371.0
    dlat, dlon = math.radians(c - a), math.radians(d - b)
    h = math.sin(dlat/2)**2 + math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(dlon/2)**2
    return R*2*math.atan2(math.sqrt(h), math.sqrt(1-h))


def nearest_station(lat, lon, max_km=MAX_RANGE_KM):
    best, best_km = None, float("inf")
    for sid, sl, so in STATIONS:
        km = haversine_km(lat, lon, sl, so)
        if km < best_km:
            best, best_km = sid, km
    return (best, best_km) if best_km <= max_km else (None, best_km)


# SPC's `tz` column: 3 = CST (the value on effectively every modern row, year-round —
# SPC does NOT observe DST), 9 = GMT/UTC, 0 = unknown. IEM RIDGE archive filenames
# are UTC, so SPC times MUST be shifted before they are used to pick radar scans.
# The IEM watchwarn ISSUED field is already UTC and needs no shift.
#
# History: models/v1 was collected WITHOUT this shift (SPC times were used as if UTC),
# so every tornado/hail/wind scan was taken 6 h before the event — usually
# pre-convective sky — while the warning-no-tornado negatives were at the right time.
# The CNN learned that artifact; see docs/MODEL_CARD.md "Invalidation (2026-09-06)".
SPC_TZ_OFFSET_HOURS = {"3": 6, "9": 0}


def spc_local_to_utc(dt, tz):
    """Shift an SPC report time to UTC. Returns None for unknown time zones."""
    off = SPC_TZ_OFFSET_HOURS.get(str(tz or "").strip())
    if off is None:
        return None
    return dt + timedelta(hours=off)


def _spc_rows(url, years, label, subtype, min_ef=0):
    r = requests.get(url, timeout=120); r.raise_for_status()
    out, yset = [], set(years)
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            if int(row["yr"]) not in yset: continue
            mag = int(float(row.get("mag") or -9))            # hail/wind mag is a float
            if label == "tornado" and mag < min_ef: continue
            lat, lon = float(row.get("slat", 0)), float(row.get("slon", 0))
            if lat == 0 or lon == 0: continue
            dt = spc_local_to_utc(
                datetime.strptime(f'{row.get("date","")} {row.get("time","00:00:00")}', "%Y-%m-%d %H:%M:%S"),
                row.get("tz"))
            if dt is None:
                continue
            out.append({"dt": dt, "lat": lat, "lon": lon, "mag": mag,
                        "st": row.get("st","").strip().upper(), "label": label, "subtype": subtype})
        except (ValueError, KeyError):
            continue
    return out


def fetch_tornadoes(years, min_ef):
    return _spc_rows(SPC_TORNADO_URL, years, "tornado", "tornado", min_ef=min_ef)


def sample_quiet(years, all_events, n, seed=42, excl_km=QUIET_EXCL_KM, excl_hours=QUIET_EXCL_HOURS):
    """QUIET negatives: random (station, UTC time) pairs with no SPC report of any
    kind (tornado incl. EF0, hail, wind) and no TO.W within `excl_km` / ±`excl_hours`.
    Ordinary rain is allowed — the class is "nothing severe", not "clear air".
    The crop centre is a random point within QUIET_CENTER_MAX_KM of the station
    so the geometry matches the event-centred classes. Deterministic per seed."""
    rng = random.Random(seed)
    by_day = defaultdict(list)
    for e in all_events:
        by_day[e["dt"].date()].append(e)
    out, tries = [], 0
    while len(out) < n and tries < n * 100:
        tries += 1
        sid, slat, slon = rng.choice(STATIONS)
        y = rng.choice(years)
        dt = datetime(y, 1, 1) + timedelta(minutes=rng.randrange(365 * 24 * 60))
        near = False
        for dd in (dt.date() - timedelta(days=1), dt.date(), dt.date() + timedelta(days=1)):
            for e in by_day.get(dd, []):
                if abs((e["dt"] - dt).total_seconds()) <= excl_hours * 3600 and \
                   haversine_km(slat, slon, e["lat"], e["lon"]) <= excl_km:
                    near = True; break
            if near: break
        if near:
            continue
        b, r = rng.uniform(0, 2 * math.pi), rng.uniform(0, QUIET_CENTER_MAX_KM)
        lat = slat + (r * math.cos(b)) / 111.0
        lon = slon + (r * math.sin(b)) / (111.0 * math.cos(math.radians(slat)))
        out.append({"dt": dt, "lat": round(lat, 4), "lon": round(lon, 4), "mag": -1,
                    "st": sid, "label": "no_tornado", "subtype": "quiet"})
    return out


def fetch_spc_negs(years, fmt, subtype):
    out = []
    for y in years:
        try:
            out += _spc_rows(fmt.format(year=y), [y], "no_tornado", subtype)
        except Exception as e:
            print(f"  {subtype} {y}: ERROR {e}")
    return out


def fetch_warnings_no_tornado(years, tornado_events, exclude_km=30, exclude_min=45):
    """Tornado WARNINGS (TO.W) with NO confirmed tornado nearby in space+time."""
    import shapefile  # pyshp
    tb = defaultdict(list)
    for t in tornado_events:
        tb[t["dt"].date()].append(t)
    out = []
    for y in years:
        try:
            blob = urllib.request.urlopen(WATCHWARN.format(y=y, y2=y+1), timeout=180).read()
            z = zipfile.ZipFile(io.BytesIO(blob))
            shp = [n for n in z.namelist() if n.endswith(".shp")][0]
            dbf = [n for n in z.namelist() if n.endswith(".dbf")][0]
            rd = shapefile.Reader(shp=io.BytesIO(z.read(shp)), dbf=io.BytesIO(z.read(dbf)))
        except Exception as e:
            print(f"  warnings {y}: ERROR {e}"); continue
        fields = [f[0] for f in rd.fields[1:]]
        seen = set()
        for sr in rd.iterShapeRecords():
            d = dict(zip(fields, sr.record))
            if d.get("PHENOM") != "TO" or d.get("SIG") != "W" or d.get("GTYPE") != "P":
                continue
            key = (d.get("WFO"), d.get("ETN"))
            if key in seen:
                continue
            seen.add(key)
            pts = sr.shape.points
            if not pts:
                continue
            cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
            try:
                issue = datetime.strptime(str(d["ISSUED"]), "%Y%m%d%H%M")
            except (ValueError, KeyError):
                continue
            near = False
            for dd in (issue.date() - timedelta(days=1), issue.date(), issue.date() + timedelta(days=1)):
                for t in tb.get(dd, []):
                    if abs((t["dt"] - issue).total_seconds()) <= exclude_min*60 and \
                       haversine_km(cy, cx, t["lat"], t["lon"]) <= exclude_km:
                        near = True; break
                if near: break
            if not near:
                out.append({"dt": issue, "lat": cy, "lon": cx, "mag": -1,
                            "st": d.get("WFO",""), "label": "no_tornado", "subtype": "warning_no_torn"})
        print(f"  warnings {y}: {sum(1 for e in out if e['dt'].year==y)} warned-no-tornado")
    return out


MANIFEST_FIELDS = ["event_id","label","class","subtype","station","mag",
                   "event_lat","event_lon","dist_km","date","scan_time","refl","vel"]


def load_existing_manifest(path: Path):
    """Read prior manifest if present. Tolerate a truncated trailing row from a hard crash."""
    if not path.exists() or path.stat().st_size == 0:
        return set()
    done = set()
    with open(path, newline="") as f:
        rdr = csv.DictReader(f)
        try:
            for row in rdr:
                eid = row.get("event_id")
                if eid:
                    done.add(eid)
        except csv.Error:
            # Truncated final line from a hard crash — drop it and trust everything we got.
            pass
    return done


def collect(events, out: Path, max_scans, label, writer, fh, already_done, counters):
    """Per-event commit barrier: an event's manifest rows are written ONLY after all its
    PNGs are downloaded AND validated on disk. Manifest-row presence ⇔ artifacts on disk."""
    raw = out / "raw" / label; raw.mkdir(parents=True, exist_ok=True)
    wdir = out / "wld"; wdir.mkdir(parents=True, exist_ok=True)
    seen_wld = set()
    written_total = 0
    events_committed_since_fsync = 0
    for ei, ev in enumerate(events):
        station, dist = nearest_station(ev["lat"], ev["lon"])
        if not station: continue
        s = station[1:]
        day = ev["dt"].replace(hour=0, minute=0, second=0, microsecond=0)
        eid = f'{ev["subtype"]}_{ev["dt"]:%Y%m%d_%H%M}_{station}'
        if eid in already_done:
            counters["events_skipped_resume"] += 1
            continue
        reflt = list_times(s, REFL_PROD, day)
        if not reflt:
            continue
        velt = list_times(s, VEL_PROD, day)
        lo, hi = ev["dt"] - timedelta(minutes=WINDOW_BEFORE_MIN), ev["dt"] + timedelta(minutes=WINDOW_AFTER_MIN)
        window = sorted([t for t in reflt if lo <= t <= hi], key=lambda t: abs((t - ev["dt"]).total_seconds()))
        chosen = sorted(window[:max_scans])
        base = ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=REFL_PROD)
        vbase = ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=VEL_PROD)
        if chosen and station not in seen_wld:
            wld_path = wdir / f"{station}.wld"
            if wld_path.exists() and wld_path.read_text().strip():
                seen_wld.add(station)
            else:
                w = fetch_text(base + reflt[chosen[0]].replace(".png", ".wld"))
                if w:
                    wld_path.write_text(w); seen_wld.add(station)

        def _scan(t):
            """Fetch+validate one scan's N0B (+N0S). Returns (row|None, downloaded, reused).
            Thread-safe: touches only its own two files."""
            dl = re = 0
            ts = t.strftime("%Y%m%d_%H%M")
            rp = raw / f"{eid}_{ts}_{REFL_PROD}.png"
            if rp.exists() and validate_png_on_disk(rp):
                re += 1
            else:
                if rp.exists():
                    rp.unlink()  # corrupt stub from prior crash
                refl = fetch_png(base + reflt[t]); time.sleep(REQUEST_DELAY_SEC)
                if refl is None:
                    return None, dl, re  # missing reflectivity disqualifies the scan
                rp.write_bytes(refl); dl += 1
                if not validate_png_on_disk(rp):
                    rp.unlink(missing_ok=True)
                    return None, dl, re
            vp = ""; vt = nearest(velt, t, VEL_TOL_MIN)
            if vt:
                vpp = raw / f"{eid}_{ts}_{VEL_PROD}.png"
                if vpp.exists() and validate_png_on_disk(vpp):
                    re += 1
                    vp = str(vpp.relative_to(out))
                else:
                    if vpp.exists():
                        vpp.unlink()
                    v = fetch_png(vbase + velt[vt]); time.sleep(REQUEST_DELAY_SEC)
                    if v:
                        vpp.write_bytes(v); dl += 1
                        if validate_png_on_disk(vpp):
                            vp = str(vpp.relative_to(out))
                        else:
                            vpp.unlink(missing_ok=True)
            row = {"event_id": eid, "label": 1 if label == "tornado" else 0, "class": label,
                   "subtype": ev["subtype"], "station": station, "mag": ev["mag"],
                   "event_lat": ev["lat"], "event_lon": ev["lon"], "dist_km": round(dist, 1),
                   "date": ev["dt"].strftime("%Y-%m-%d"), "scan_time": t.strftime("%Y-%m-%dT%H:%MZ"),
                   "refl": str(rp.relative_to(out)), "vel": vp}
            return row, dl, re

        ev_rows = []
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            for row, dl, re in pool.map(_scan, chosen):   # map keeps `chosen` order
                counters["png_downloaded"] += dl
                counters["png_reused"] += re
                if row is not None:
                    ev_rows.append(row)
        if not ev_rows:
            continue
        # COMMIT BARRIER: all artifacts on disk; flush rows for this event together.
        writer.writerows(ev_rows)
        fh.flush()
        already_done.add(eid)
        written_total += len(ev_rows)
        counters["events_committed"] += 1
        events_committed_since_fsync += 1
        if events_committed_since_fsync >= 25:
            os.fsync(fh.fileno())
            events_committed_since_fsync = 0
        if (ei + 1) % 50 == 0:
            print(f"  ...{label}: {ei+1}/{len(events)} events, "
                  f"{counters['events_committed']} committed ({written_total} scans), "
                  f"{counters['events_skipped_resume']} resume-skipped")
    # Final fsync so the tail of the run is durable even if something kills us next.
    if events_committed_since_fsync:
        os.fsync(fh.fileno())
    return written_total


def sub(lst, n, seed=42):
    """First n of a seeded shuffle, so a larger cap is a SUPERSET of a smaller one
    (resume-friendly). n=0 -> everything. (Pre-2026-09-07 this was a stride sample.)"""
    if not n or n >= len(lst):
        return list(lst)
    idx = list(range(len(lst)))
    random.Random(seed).shuffle(idx)
    return [lst[i] for i in sorted(idx[:n])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, default=[2020,2021,2022,2023,2024,2025])
    ap.add_argument("--min-ef", type=int, default=1)
    ap.add_argument("--cap-pos", type=int, default=0, help="max tornado events (0 = all)")
    ap.add_argument("--cap-neg-each", type=int, default=2000, help="max events per hail/wind/warning source")
    ap.add_argument("--cap-quiet", type=int, default=1500, help="quiet-sky negative events (0 = none)")
    ap.add_argument("--sample", type=int, default=None, help="quick test: N events per class-source")
    ap.add_argument("--max-scans", type=int, default=5)
    ap.add_argument("--out", default="../data/full")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    global STATIONS
    print("Loading CONUS NEXRAD stations..."); STATIONS = load_stations(); print(f"  stations: {len(STATIONS)}")
    cap_pos = args.sample or args.cap_pos
    cap_neg = args.sample or args.cap_neg_each
    cap_quiet = args.sample or args.cap_quiet

    years = [max(args.years)] if args.sample else args.years   # sample = 1 recent year (fast)
    print(f"Fetching events for years {years}...")
    torn_any = fetch_tornadoes(years, 0)                 # incl. EF0/EFU — for exclusion zones
    torn_all = [t for t in torn_any if t["mag"] >= args.min_ef]
    hail = fetch_spc_negs(years, SPC_HAIL_URL_FMT, "hail")
    wind = fetch_spc_negs(years, SPC_WIND_URL_FMT, "wind")
    warn = fetch_warnings_no_tornado(years, torn_all)   # uses FULL tornado list to exclude
    quiet = sample_quiet(years, torn_any + hail + wind + warn, cap_quiet) if cap_quiet else []
    print(f"  tornado={len(torn_all)} hail={len(hail)} wind={len(wind)} warn_no_torn={len(warn)} "
          f"quiet={len(quiet)}")

    pos = sub(torn_all, cap_pos)
    neg = sub(hail, cap_neg) + sub(wind, cap_neg) + sub(warn, cap_neg) + quiet
    print(f"  collecting: pos={len(pos)}  neg={len(neg)} "
          f"(hail {min(len(hail),cap_neg) if cap_neg else len(hail)} / "
          f"wind {min(len(wind),cap_neg) if cap_neg else len(wind)} / "
          f"warn {min(len(warn),cap_neg) if cap_neg else len(warn)} / quiet {len(quiet)})")

    man = out / "manifest.csv"
    already_done = load_existing_manifest(man)
    if already_done:
        print(f"resumed: skipping {len(already_done)} already-collected events")
    fresh = (not man.exists()) or man.stat().st_size == 0
    # Line-buffered text-mode append; per-event flush + periodic fsync inside collect().
    fh = open(man, "a", newline="", buffering=1)
    try:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        if fresh:
            writer.writeheader(); fh.flush()
        counters = {"events_committed": 0, "events_skipped_resume": 0,
                    "png_downloaded": 0, "png_reused": 0}
        print("Collecting positives (tornado)...")
        npos_written = collect(pos, out, args.max_scans, "tornado", writer, fh, already_done, counters)
        print("Collecting negatives...")
        nneg_written = collect(neg, out, args.max_scans, "no_tornado", writer, fh, already_done, counters)
    finally:
        fh.close()

    # Final summary reads back the (now fully durable) manifest.
    rows = []
    with open(man, newline="") as f:
        try:
            rows = list(csv.DictReader(f))
        except csv.Error:
            pass
    npos = sum(1 for r in rows if str(r.get("label")) == "1")
    nvel = sum(1 for r in rows if r.get("vel"))
    nev  = len({r["event_id"] for r in rows})
    print(f"\nDONE  scans={len(rows)}  pos={npos}  neg={len(rows)-npos}  "
          f"with_velocity={nvel}  events={nev}")
    print(f"  this run: committed={counters['events_committed']}  "
          f"resume-skipped={counters['events_skipped_resume']}  "
          f"png_downloaded={counters['png_downloaded']}  png_reused={counters['png_reused']}")
    print(f"  manifest: {man}")


if __name__ == "__main__":
    main()
