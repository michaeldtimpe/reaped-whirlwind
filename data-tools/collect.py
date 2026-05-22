#!/usr/bin/env python3
"""
Domain-matched radar collector for reaped-whirlwind (Part B).

Pulls per-station IEM RIDGE **N0B** (super-res base reflectivity) and **N0S**
(storm-relative velocity) — the product pair consistently available 2020-2025
and matching the current/live radar era. (Legacy N0Q/N0U existed only up to
~2022; the CONUS mosaic the original downloader used was a domain mismatch.)

Because products have different cadences (N0B ~2 min, N0S ~7 min), we LIST each
product's archive directory for the event day and pick the nearest-available
frame to each target time, then pair each N0B frame with the nearest N0S.

Events:
  - positives: SPC confirmed tornadoes (combined CSV)
  - negatives: SPC hail reports (per-year CSV) — severe, non-tornadic hard negatives

Output: <out>/raw/<class>/*.png + <out>/wld/<station>.wld + manifest.csv

Sample-pull checkpoint:
  python collect.py --years 2023 --sample 50 --max-scans 4 --out ../data/sample
"""
import argparse, csv, io, math, re, time
from datetime import datetime, timedelta
from pathlib import Path
import requests

SPC_TORNADO_URL  = "https://www.spc.noaa.gov/wcm/data/1950-2025_actual_tornadoes.csv"
SPC_HAIL_URL_FMT = "https://www.spc.noaa.gov/wcm/data/{year}_hail.csv"  # per-year, has header
ARCH = "https://mesonet.agron.iastate.edu/archive/data/{y}/{m:02d}/{d:02d}/GIS/ridge/{s}/{prod}/"

REFL_PROD, VEL_PROD = "N0B", "N0S"
WINDOW_BEFORE_MIN, WINDOW_AFTER_MIN = 30, 10
REFL_TOL_MIN, VEL_TOL_MIN = 4, 8       # nearest-frame tolerances (per-product cadence)
REQUEST_DELAY_SEC = 0.3
MAX_RANGE_KM = 150                      # beam spreading/clutter beyond this -> discard

NEXRAD_STATIONS = [
    ("KTLX",35.3331,-97.2778),("KOUN",35.2369,-97.4628),("KVNX",36.7408,-98.1278),
    ("KINX",36.1750,-95.5644),("KDDC",37.7608,-99.9689),("KICT",37.6544,-97.4428),
    ("KTWX",38.9969,-96.2325),("KEAX",38.8100,-94.2644),("KSGF",37.2353,-93.4006),
    ("KLSX",38.6989,-90.6828),("KDVN",41.6117,-90.5808),("KDMX",41.7311,-93.7228),
    ("KARX",43.8228,-91.1911),("KMKX",42.9678,-88.5506),("KLOT",41.6044,-88.0847),
    ("KIND",39.7075,-86.2803),("KIWX",41.3589,-85.7000),("KGRR",42.8939,-85.5447),
    ("KGRB",44.4986,-88.1111),("KMPX",44.8489,-93.5656),("KDLH",46.8369,-92.2097),
    ("KABR",45.4558,-98.4131),("KFSD",43.5878,-96.7294),("KUDX",44.1250,-102.8297),
    ("KBIS",46.7708,-100.7603),("KMVX",47.5278,-97.3253),("KLBB",33.6539,-101.8142),
    ("KAMA",35.2333,-101.7092),("KFWS",32.5728,-97.3031),("KEWX",29.7039,-98.0283),
    ("KHGX",29.4719,-95.0792),("KSHV",32.4508,-93.8411),("KLCH",30.1253,-93.2158),
    ("KLIX",30.3364,-89.8256),("KBMX",33.1722,-86.7700),("KHTX",34.9306,-86.0836),
    ("KOHX",36.2472,-86.5625),("KNQA",35.3447,-89.8733),("KJAX",30.4847,-81.7019),
    ("KTBW",27.7056,-82.4019),("KAMX",25.6111,-80.4128),("KMLB",28.1131,-80.6542),
]


def haversine_km(a, b, c, d):
    R = 6371.0
    dlat, dlon = math.radians(c - a), math.radians(d - b)
    h = math.sin(dlat/2)**2 + math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(dlon/2)**2
    return R*2*math.atan2(math.sqrt(h), math.sqrt(1-h))


def nearest_station(lat, lon, max_km=MAX_RANGE_KM):
    best, best_km = None, float("inf")
    for sid, sl, so in NEXRAD_STATIONS:
        km = haversine_km(lat, lon, sl, so)
        if km < best_km:
            best, best_km = sid, km
    return (best, best_km) if best_km <= max_km else (None, best_km)


def fetch_spc(url, years, label, min_ef=0, state=None):
    r = requests.get(url, timeout=120); r.raise_for_status()
    out, yset = [], set(years)
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            if int(row["yr"]) not in yset: continue
            mag = int(float(row.get("mag") or -9))   # hail "mag" is a float (size, in)
            if label == "tornado" and mag < min_ef: continue
            st = row.get("st", "").strip().upper()
            if state and st != state.upper(): continue
            lat, lon = float(row.get("slat", 0)), float(row.get("slon", 0))
            if lat == 0 or lon == 0: continue
            dt = datetime.strptime(f'{row.get("date","")} {row.get("time","00:00:00")}', "%Y-%m-%d %H:%M:%S")
            out.append({"dt": dt, "lat": lat, "lon": lon, "mag": mag, "st": st, "label": label})
        except (ValueError, KeyError):
            continue
    return out


def fetch_hail(years, state=None):
    out = []
    for y in years:
        try:
            out += fetch_spc(SPC_HAIL_URL_FMT.format(year=y), [y], "no_tornado", state=state)
        except Exception as e:
            print(f"  hail {y}: ERROR {e}")
    return out


def list_times(s, prod, day):
    """Return {datetime: filename} of available frames for one station/product/day."""
    url = ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=prod)
    try:
        txt = requests.get(url, timeout=30).text
    except requests.RequestException:
        return {}
    out = {}
    for fn in re.findall(rf'{s}_{prod}_(\d{{12}})\.png', txt):
        try:
            out[datetime.strptime(fn, "%Y%m%d%H%M")] = f"{s}_{prod}_{fn}.png"
        except ValueError:
            pass
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
        r = requests.get(url, timeout=20)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def collect(events, out: Path, max_scans, label):
    raw = out / "raw" / label; raw.mkdir(parents=True, exist_ok=True)
    wdir = out / "wld"; wdir.mkdir(parents=True, exist_ok=True)
    rows, seen_wld = [], set()
    for ei, ev in enumerate(events):
        station, dist = nearest_station(ev["lat"], ev["lon"])
        if not station:
            continue
        s = station[1:]
        day = ev["dt"].replace(hour=0, minute=0, second=0, microsecond=0)
        reflt = list_times(s, REFL_PROD, day)
        if not reflt:
            print(f"  [{ei+1}/{len(events)}] {station} {ev['dt']:%Y-%m-%d}: no {REFL_PROD}"); continue
        velt = list_times(s, VEL_PROD, day)
        lo, hi = ev["dt"] - timedelta(minutes=WINDOW_BEFORE_MIN), ev["dt"] + timedelta(minutes=WINDOW_AFTER_MIN)
        window = sorted([t for t in reflt if lo <= t <= hi], key=lambda t: abs((t - ev["dt"]).total_seconds()))
        chosen = sorted(window[:max_scans])
        eid = f'{label}_{ev["dt"]:%Y%m%d_%H%M}_{station}'
        n = 0
        base = ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=REFL_PROD)
        velbase = ARCH.format(y=day.year, m=day.month, d=day.day, s=s, prod=VEL_PROD)
        for t in chosen:
            refl = fetch_png(base + reflt[t]); time.sleep(REQUEST_DELAY_SEC)
            if refl is None: continue
            ts = t.strftime("%Y%m%d_%H%M")
            rp = raw / f"{eid}_{ts}_{REFL_PROD}.png"; rp.write_bytes(refl)
            vp = ""
            vt = nearest(velt, t, VEL_TOL_MIN)
            if vt:
                v = fetch_png(velbase + velt[vt]); time.sleep(REQUEST_DELAY_SEC)
                if v:
                    vpp = raw / f"{eid}_{ts}_{VEL_PROD}.png"; vpp.write_bytes(v); vp = str(vpp.relative_to(out))
            if station not in seen_wld:
                w = fetch_text(base + reflt[t].replace(".png", ".wld"))
                if w: (wdir / f"{station}.wld").write_text(w); seen_wld.add(station)
            rows.append({"event_id": eid, "label": 1 if label == "tornado" else 0, "class": label,
                         "station": station, "mag": ev["mag"], "event_lat": ev["lat"], "event_lon": ev["lon"],
                         "dist_km": round(dist, 1), "date": ev["dt"].strftime("%Y-%m-%d"),
                         "scan_time": t.strftime("%Y-%m-%dT%H:%MZ"),
                         "refl": str(rp.relative_to(out)), "vel": vp})
            n += 1
        print(f"  [{ei+1}/{len(events)}] {eid}: {n} scans")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, required=True)
    ap.add_argument("--sample", type=int, default=None, help="N tornado + N hail events")
    ap.add_argument("--min-ef", type=int, default=1)
    ap.add_argument("--state", default=None)
    ap.add_argument("--max-scans", type=int, default=4)
    ap.add_argument("--out", default="../data/sample")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    print("Fetching SPC events...")
    torn = fetch_spc(SPC_TORNADO_URL, args.years, "tornado", min_ef=args.min_ef, state=args.state)
    hail = fetch_hail(args.years, state=args.state)
    print(f"  tornado={len(torn)}  hail={len(hail)}")
    if args.sample:
        torn = torn[:: max(1, len(torn)//args.sample)][:args.sample]
        hail = hail[:: max(1, len(hail)//args.sample)][:args.sample]
        print(f"  sampled: tornado={len(torn)} hail={len(hail)}")

    rows = []
    print("Collecting tornado positives..."); rows += collect(torn, out, args.max_scans, "tornado")
    print("Collecting hail negatives...");    rows += collect(hail, out, args.max_scans, "no_tornado")

    man = out / "manifest.csv"
    with open(man, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event_id","label","class","station","mag",
            "event_lat","event_lon","dist_km","date","scan_time","refl","vel"])
        w.writeheader(); w.writerows(rows)
    npos = sum(1 for r in rows if r["label"] == 1); nvel = sum(1 for r in rows if r["vel"])
    print(f"\nDONE  scans={len(rows)}  pos={npos}  neg={len(rows)-npos}  with_velocity={nvel}")
    print(f"  events_with_data={len(set(r['event_id'] for r in rows))}  | manifest: {man}")


if __name__ == "__main__":
    main()
