#!/usr/bin/env python3
"""
Domain-matched radar collector for reaped-whirlwind (Part B).

Pulls per-station IEM RIDGE **N0B** (super-res reflectivity) + **N0S** (storm-relative
velocity) — the consistent 2020-2025 / live-era product pair — for:
  POSITIVES : SPC confirmed tornadoes
  NEGATIVES : SPC hail + SPC wind (severe, non-tornadic) AND
              tornado-WARNINGS with NO confirmed tornado (the hardest negatives)

Stations: all CONUS WSR-88D (fetched live from IEM, with an embedded fallback).
Products have different cadences, so we list each product's archive dir and pair
nearest-available frames. 150 km range cutoff.

Full run (M4 Pro): see run_collection.sh.  Quick check: --sample 50
"""
import argparse, csv, io, math, re, time, zipfile, json, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import requests

SPC_TORNADO_URL  = "https://www.spc.noaa.gov/wcm/data/1950-2025_actual_tornadoes.csv"
SPC_HAIL_URL_FMT = "https://www.spc.noaa.gov/wcm/data/{year}_hail.csv"
SPC_WIND_URL_FMT = "https://www.spc.noaa.gov/wcm/data/{year}_wind.csv"
STATIONS_GEOJSON = "https://mesonet.agron.iastate.edu/geojson/network/NEXRAD.geojson"
WATCHWARN = ("https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py?accept=shapefile"
             "&year1={y}&month1=1&day1=1&hour1=0&minute1=0&year2={y2}&month2=1&day2=1&hour2=0&minute2=0"
             "&limitps=yes&phenomena=TO&significance=W")
ARCH = "https://mesonet.agron.iastate.edu/archive/data/{y}/{m:02d}/{d:02d}/GIS/ridge/{s}/{prod}/"

REFL_PROD, VEL_PROD = "N0B", "N0S"
WINDOW_BEFORE_MIN, WINDOW_AFTER_MIN = 30, 10
VEL_TOL_MIN = 8
REQUEST_DELAY_SEC = 0.25
MAX_RANGE_KM = 150

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
            dt = datetime.strptime(f'{row.get("date","")} {row.get("time","00:00:00")}', "%Y-%m-%d %H:%M:%S")
            out.append({"dt": dt, "lat": lat, "lon": lon, "mag": mag,
                        "st": row.get("st","").strip().upper(), "label": label, "subtype": subtype})
        except (ValueError, KeyError):
            continue
    return out


def fetch_tornadoes(years, min_ef):
    return _spc_rows(SPC_TORNADO_URL, years, "tornado", "tornado", min_ef=min_ef)


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


def list_times(s, prod, day):
    try:
        txt = requests.get(ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=prod), timeout=30).text
    except requests.RequestException:
        return {}
    out = {}
    for fn in re.findall(rf'{s}_{prod}_(\d{{12}})\.png', txt):
        try: out[datetime.strptime(fn, "%Y%m%d%H%M")] = f"{s}_{prod}_{fn}.png"
        except ValueError: pass
    return out


def nearest(times, target, tol_min):
    if not times: return None
    dt = min(times, key=lambda t: abs((t - target).total_seconds()))
    return dt if abs((dt - target).total_seconds()) <= tol_min*60 else None


def fetch_png(url):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and r.content[:4] == b"\x89PNG" and len(r.content) > 200:
            return r.content
    except requests.RequestException:
        pass
    return None


def fetch_text(url):
    try:
        r = requests.get(url, timeout=20); return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def collect(events, out: Path, max_scans, label):
    raw = out / "raw" / label; raw.mkdir(parents=True, exist_ok=True)
    wdir = out / "wld"; wdir.mkdir(parents=True, exist_ok=True)
    rows, seen_wld = [], set()
    for ei, ev in enumerate(events):
        station, dist = nearest_station(ev["lat"], ev["lon"])
        if not station: continue
        s = station[1:]
        day = ev["dt"].replace(hour=0, minute=0, second=0, microsecond=0)
        reflt = list_times(s, REFL_PROD, day)
        if not reflt:
            continue
        velt = list_times(s, VEL_PROD, day)
        lo, hi = ev["dt"] - timedelta(minutes=WINDOW_BEFORE_MIN), ev["dt"] + timedelta(minutes=WINDOW_AFTER_MIN)
        window = sorted([t for t in reflt if lo <= t <= hi], key=lambda t: abs((t - ev["dt"]).total_seconds()))
        chosen = sorted(window[:max_scans])
        eid = f'{ev["subtype"]}_{ev["dt"]:%Y%m%d_%H%M}_{station}'
        base = ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=REFL_PROD)
        vbase = ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=VEL_PROD)
        n = 0
        for t in chosen:
            refl = fetch_png(base + reflt[t]); time.sleep(REQUEST_DELAY_SEC)
            if refl is None: continue
            ts = t.strftime("%Y%m%d_%H%M")
            rp = raw / f"{eid}_{ts}_{REFL_PROD}.png"; rp.write_bytes(refl)
            vp = ""; vt = nearest(velt, t, VEL_TOL_MIN)
            if vt:
                v = fetch_png(vbase + velt[vt]); time.sleep(REQUEST_DELAY_SEC)
                if v:
                    vpp = raw / f"{eid}_{ts}_{VEL_PROD}.png"; vpp.write_bytes(v); vp = str(vpp.relative_to(out))
            if station not in seen_wld:
                w = fetch_text(base + reflt[t].replace(".png", ".wld"))
                if w: (wdir / f"{station}.wld").write_text(w); seen_wld.add(station)
            rows.append({"event_id": eid, "label": 1 if label == "tornado" else 0, "class": label,
                         "subtype": ev["subtype"], "station": station, "mag": ev["mag"],
                         "event_lat": ev["lat"], "event_lon": ev["lon"], "dist_km": round(dist, 1),
                         "date": ev["dt"].strftime("%Y-%m-%d"), "scan_time": t.strftime("%Y-%m-%dT%H:%MZ"),
                         "refl": str(rp.relative_to(out)), "vel": vp})
            n += 1
        if (ei + 1) % 50 == 0:
            print(f"  ...{label}: {ei+1}/{len(events)} events, {len(rows)} scans so far")
    return rows


def sub(lst, n):
    return lst[:: max(1, len(lst)//n)][:n] if n else lst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, default=[2020,2021,2022,2023,2024,2025])
    ap.add_argument("--min-ef", type=int, default=1)
    ap.add_argument("--cap-pos", type=int, default=2500, help="max tornado events")
    ap.add_argument("--cap-neg-each", type=int, default=1000, help="max events per negative source")
    ap.add_argument("--sample", type=int, default=None, help="quick test: N events per class-source")
    ap.add_argument("--max-scans", type=int, default=5)
    ap.add_argument("--out", default="../data/full")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    global STATIONS
    print("Loading CONUS NEXRAD stations..."); STATIONS = load_stations(); print(f"  stations: {len(STATIONS)}")
    cap_pos = args.sample or args.cap_pos
    cap_neg = args.sample or args.cap_neg_each

    years = [max(args.years)] if args.sample else args.years   # sample = 1 recent year (fast)
    print(f"Fetching events for years {years}...")
    torn_all = fetch_tornadoes(years, args.min_ef)
    hail = fetch_spc_negs(years, SPC_HAIL_URL_FMT, "hail")
    wind = fetch_spc_negs(years, SPC_WIND_URL_FMT, "wind")
    warn = fetch_warnings_no_tornado(years, torn_all)   # uses FULL tornado list to exclude
    print(f"  tornado={len(torn_all)} hail={len(hail)} wind={len(wind)} warn_no_torn={len(warn)}")

    pos = sub(torn_all, cap_pos)
    neg = sub(hail, cap_neg) + sub(wind, cap_neg) + sub(warn, cap_neg)
    print(f"  collecting: pos={len(pos)}  neg={len(neg)} "
          f"(hail {min(len(hail),cap_neg)} / wind {min(len(wind),cap_neg)} / warn {min(len(warn),cap_neg)})")

    rows = []
    print("Collecting positives (tornado)..."); rows += collect(pos, out, args.max_scans, "tornado")
    print("Collecting negatives...");           rows += collect(neg, out, args.max_scans, "no_tornado")

    man = out / "manifest.csv"
    with open(man, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event_id","label","class","subtype","station","mag",
            "event_lat","event_lon","dist_km","date","scan_time","refl","vel"])
        w.writeheader(); w.writerows(rows)
    npos = sum(1 for r in rows if r["label"] == 1); nvel = sum(1 for r in rows if r["vel"])
    nev = len(set(r["event_id"] for r in rows))
    print(f"\nDONE  scans={len(rows)}  pos={npos}  neg={len(rows)-npos}  with_velocity={nvel}  events={nev}")
    print(f"  manifest: {man}")


if __name__ == "__main__":
    main()
