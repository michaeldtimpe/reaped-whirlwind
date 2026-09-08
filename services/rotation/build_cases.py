#!/usr/bin/env python3
"""Build the extended replay case set for the MRMS backtest gate.

Produces `replay_cases.json` (default location: alongside this script) consumed by
`services/rotation/replay.py` (docs/MRMS_MIGRATION.md §3). Three lists:

  positives       every SPC tornado EF1+ within 100 km of KFWS, 2020-10-14..2025-12-31,
                  plus the 5 "base cases" from docs/MRMS_MIGRATION.md §3.
  quiet_controls  UTC timestamps on days with no convective (SV/TO/FF/MA) VTEC activity
                  for WFO FWD (that date or the adjacent +-1 day), spread across months
                  and the diurnal cycle, 2021-2025.
  svw_controls    Severe Thunderstorm Warnings (SV.W) issued by WFO FWD whose polygon
                  centroid is within 100 km of KFWS, on days with no tornado (any EF)
                  within 150 km of KFWS and no TO.W from FWD that day.

Data sources (all cached under `data/mrms-cases/`, gitignored):
  - SPC tornado CSV: same URL data-tools/collect.py uses (SPC_TORNADO_URL).
  - IEM VTEC events JSON: mesonet.agron.iastate.edu/json/vtec_events.py?wfo=FWD&year=YYYY
    (the api/1/vtec/events.json path returned 404 as of 2026-09-07; this legacy CGI works).
  - IEM watchwarn shapefile export (same CGI collect.py's fetch_warnings_no_tornado uses),
    filtered server-side with &wfo=FWD&phenomena=SV&significance=W to keep downloads small.

Deterministic: quiet-control day/hour selection uses a seeded RNG (default seed 42), and
every other step is a plain filter/sort with no randomness, so re-running with the same
cache (or a fresh download of the same archives) reproduces the same file.

stdlib + requests + pyshp only (pyshp is already a data-tools/collect.py dependency).
"""
import argparse
import csv
import io
import json
import math
import random
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
from common.nws import KFWS_LAT, KFWS_LON          # noqa: E402
from common.places import DFW_PLACES, nearest_place  # noqa: E402

SPC_TORNADO_URL = "https://www.spc.noaa.gov/wcm/data/1950-2025_actual_tornadoes.csv"
VTEC_EVENTS_URL = "https://mesonet.agron.iastate.edu/json/vtec_events.py?wfo={wfo}&year={year}"
WATCHWARN_URL = (
    "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py?accept=shapefile"
    "&year1={y}&month1=1&day1=1&hour1=0&minute1=0&year2={y2}&month2=1&day2=1&hour2=0&minute2=0"
    "&limitps=yes&phenomena={phenomena}&significance={sig}&wfo={wfo}"
)

WFO = "FWD"
ARCHIVE_START = date(2020, 10, 14)     # S3 AzShear archive start; hard floor on every case time
POSITIVES_END = date(2025, 12, 31)
POSITIVE_RADIUS_KM = 100
SVW_RADIUS_KM = 100
TORNADO_EXCLUSION_KM = 150             # "no tornado (any EF) within 150 km" for SVW controls
NEAR_TOWN_MAX_KM = 20                  # nearest-town naming cutoff; beyond this, use county
QUIET_TARGET = 36
SVW_TARGET = 22
CONVECTIVE_PHENOM_SIG = {("SV", "W"), ("TO", "W"), ("SV", "A"), ("TO", "A"), ("FF", "W"), ("MA", "W")}

# SPC's `tz` column: 3 = CST (the value on effectively every modern row, year-round --
# SPC does NOT observe DST), 9 = GMT/UTC. Same table as data-tools/collect.py.
SPC_TZ_OFFSET_HOURS = {"3": 6, "9": 0}

# The 5 "base cases" docs/MRMS_MIGRATION.md Sec.3 carries over from replay_smoke.py.
# Approximate city-centre fallback coordinates are used ONLY if no matching SPC row is
# found (they are not expected to be needed -- all 5 matched a SPC row within a minute
# during development; see build report).
BASE_POSITIVES = [
    {"id": "2022-04-05-crowley",   "time": "2022-04-05T03:41:00Z", "ef": 2,
     "fallback_lat": 32.5787, "fallback_lon": -97.3620},
    {"id": "2020-11-25-arlington", "time": "2020-11-25T02:51:00Z", "ef": 2,
     "fallback_lat": 32.7357, "fallback_lon": -97.1081},
    {"id": "2022-12-13-fort-worth", "time": "2022-12-13T14:14:00Z", "ef": 1,
     "fallback_lat": 32.7555, "fallback_lon": -97.3308},
    {"id": "2023-03-16-irving",    "time": "2023-03-16T21:47:00Z", "ef": 1,
     "fallback_lat": 32.8140, "fallback_lon": -96.9489},
    {"id": "2025-03-04-dallas",    "time": "2025-03-04T11:24:00Z", "ef": 1,
     "fallback_lat": 32.7767, "fallback_lon": -96.7970},
]

BASE_QUIET = [
    {"time": "2023-08-15T21:00:00Z", "note": "base control (docs/MRMS_MIGRATION.md §3)"},
    {"time": "2024-01-20T06:00:00Z", "note": "base control (docs/MRMS_MIGRATION.md §3)"},
    {"time": "2024-04-10T20:00:00Z", "note": "base control (docs/MRMS_MIGRATION.md §3)"},
    {"time": "2022-10-05T15:00:00Z", "note": "base control (docs/MRMS_MIGRATION.md §3)"},
]

# Texas county FIPS (3-digit, i.e. the last 3 digits of the 5-digit 48XXX code) -> name.
# Full table (not just DFW-area) so the fallback never silently drops a legitimate event
# just because it landed in an unanticipated county near the edge of the 100 km disc.
TX_COUNTY_FIPS = {
    1: "Anderson", 3: "Andrews", 5: "Angelina", 7: "Aransas", 9: "Archer", 11: "Armstrong",
    13: "Atascosa", 15: "Austin", 17: "Bailey", 19: "Bandera", 21: "Bastrop", 23: "Baylor",
    25: "Bee", 27: "Bell", 29: "Bexar", 31: "Blanco", 33: "Borden", 35: "Bosque",
    37: "Bowie", 39: "Brazoria", 41: "Brazos", 43: "Brewster", 45: "Briscoe", 47: "Brooks",
    49: "Brown", 51: "Burleson", 53: "Burnet", 55: "Caldwell", 57: "Calhoun", 59: "Callahan",
    61: "Cameron", 63: "Camp", 65: "Carson", 67: "Cass", 69: "Castro", 71: "Chambers",
    73: "Cherokee", 75: "Childress", 77: "Clay", 79: "Cochran", 81: "Coke", 83: "Coleman",
    85: "Collin", 87: "Collingsworth", 89: "Colorado", 91: "Comal", 93: "Comanche",
    95: "Concho", 97: "Cooke", 99: "Coryell", 101: "Cottle", 103: "Crane", 105: "Crockett",
    107: "Crosby", 109: "Culberson", 111: "Dallam", 113: "Dallas", 115: "Dawson",
    117: "Deaf Smith", 119: "Delta", 121: "Denton", 123: "DeWitt", 125: "Dickens",
    127: "Dimmit", 129: "Donley", 131: "Duval", 133: "Eastland", 135: "Ector",
    137: "Edwards", 139: "Ellis", 141: "El Paso", 143: "Erath", 145: "Falls",
    147: "Fannin", 149: "Fayette", 151: "Fisher", 153: "Floyd", 155: "Foard",
    157: "Fort Bend", 159: "Franklin", 161: "Freestone", 163: "Frio", 165: "Gaines",
    167: "Galveston", 169: "Garza", 171: "Gillespie", 173: "Glasscock", 175: "Goliad",
    177: "Gonzales", 179: "Gray", 181: "Grayson", 183: "Gregg", 185: "Grimes",
    187: "Guadalupe", 189: "Hale", 191: "Hall", 193: "Hamilton", 195: "Hansford",
    197: "Hardeman", 199: "Hardin", 201: "Harris", 203: "Harrison", 205: "Hartley",
    207: "Haskell", 209: "Hays", 211: "Hemphill", 213: "Henderson", 215: "Hidalgo",
    217: "Hill", 219: "Hockley", 221: "Hood", 223: "Hopkins", 225: "Houston",
    227: "Howard", 229: "Hudspeth", 231: "Hunt", 233: "Hutchinson", 235: "Irion",
    237: "Jack", 239: "Jackson", 241: "Jasper", 243: "Jeff Davis", 245: "Jefferson",
    247: "Jim Hogg", 249: "Jim Wells", 251: "Johnson", 253: "Jones", 255: "Karnes",
    257: "Kaufman", 259: "Kendall", 261: "Kenedy", 263: "Kent", 265: "Kerr",
    267: "Kimble", 269: "King", 271: "Kinney", 273: "Kleberg", 275: "Knox",
    277: "Lamar", 279: "Lamb", 281: "Lampasas", 283: "La Salle", 285: "Lavaca",
    287: "Lee", 289: "Leon", 291: "Liberty", 293: "Limestone", 295: "Lipscomb",
    297: "Live Oak", 299: "Llano", 301: "Loving", 303: "Lubbock", 305: "Lynn",
    307: "McCulloch", 309: "McLennan", 311: "McMullen", 313: "Madison", 315: "Marion",
    317: "Martin", 319: "Mason", 321: "Matagorda", 323: "Maverick", 325: "Medina",
    327: "Menard", 329: "Midland", 331: "Milam", 333: "Mills", 335: "Mitchell",
    337: "Montague", 339: "Montgomery", 341: "Moore", 343: "Morris", 345: "Motley",
    347: "Nacogdoches", 349: "Navarro", 351: "Newton", 353: "Nolan", 355: "Nueces",
    357: "Ochiltree", 359: "Oldham", 361: "Orange", 363: "Palo Pinto", 365: "Panola",
    367: "Parker", 369: "Parmer", 371: "Pecos", 373: "Polk", 375: "Potter",
    377: "Presidio", 379: "Rains", 381: "Randall", 383: "Reagan", 385: "Real",
    387: "Red River", 389: "Reeves", 391: "Refugio", 393: "Roberts", 395: "Robertson",
    397: "Rockwall", 399: "Runnels", 401: "Rusk", 403: "Sabine", 405: "San Augustine",
    407: "San Jacinto", 409: "San Patricio", 411: "San Saba", 413: "Schleicher",
    415: "Scurry", 417: "Shackelford", 419: "Shelby", 421: "Sherman", 423: "Smith",
    425: "Somervell", 427: "Starr", 429: "Stephens", 431: "Sterling", 433: "Stonewall",
    435: "Sutton", 437: "Swisher", 439: "Tarrant", 441: "Taylor", 443: "Terrell",
    445: "Terry", 447: "Throckmorton", 449: "Titus", 451: "Tom Green", 453: "Travis",
    455: "Trinity", 457: "Tyler", 459: "Upshur", 461: "Upton", 463: "Uvalde",
    465: "Val Verde", 467: "Van Zandt", 469: "Victoria", 471: "Walker", 473: "Waller",
    475: "Ward", 477: "Washington", 479: "Webb", 481: "Wharton", 483: "Wheeler",
    485: "Wichita", 487: "Wilbarger", 489: "Willacy", 491: "Williamson", 493: "Wilson",
    495: "Winkler", 497: "Wise", 499: "Wood", 501: "Yoakum", 503: "Young",
    505: "Zapata", 507: "Zavala",
}


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def slugify(name):
    out = []
    for ch in name.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " _-/":
            out.append("-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "unk"


def name_for(lat, lon, st, county_fips):
    """Nearest DFW town within NEAR_TOWN_MAX_KM, else the TX county name, else 'unk'."""
    town = nearest_place(lat, lon, DFW_PLACES)
    if town is not None:
        for n, plat, plon in DFW_PLACES:
            if n == town:
                if haversine_km(lat, lon, plat, plon) <= NEAR_TOWN_MAX_KM:
                    return slugify(town)
                break
    if st == "TX" and county_fips is not None:
        try:
            cty = TX_COUNTY_FIPS.get(int(county_fips))
        except (TypeError, ValueError):
            cty = None
        if cty:
            return slugify(f"{cty}-county")
    return "unk"


# --------------------------------------------------------------------------- caching

def cache_get(url, cache_path: Path, label, force=False):
    if cache_path.exists() and not force:
        return cache_path.read_bytes()
    print(f"  fetching {label} ...")
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(r.content)
    return r.content


# --------------------------------------------------------------------------- SPC tornadoes

def spc_local_to_utc(dt, tz):
    off = SPC_TZ_OFFSET_HOURS.get(str(tz or "").strip())
    if off is None:
        return None
    return dt + timedelta(hours=off)


def load_spc_tornadoes(cache_dir: Path):
    """Every SPC tornado row (any EF, any year) with a valid start point, dt = UTC."""
    raw = cache_get(SPC_TORNADO_URL, cache_dir / "spc_tornadoes.csv", "SPC tornado CSV")
    text = raw.decode("utf-8", "replace")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            mag = int(float(row.get("mag") or -9))
            lat, lon = float(row.get("slat", 0)), float(row.get("slon", 0))
            if lat == 0 or lon == 0:
                continue
            dt = spc_local_to_utc(
                datetime.strptime(f'{row.get("date", "")} {row.get("time", "00:00:00")}',
                                   "%Y-%m-%d %H:%M:%S"),
                row.get("tz"))
            if dt is None:
                continue
            dt = dt.replace(tzinfo=timezone.utc)
            out.append({
                "dt": dt, "lat": lat, "lon": lon, "mag": mag,
                "st": (row.get("st") or "").strip().upper(),
                "f1": row.get("f1"),
            })
        except (ValueError, KeyError):
            continue
    return out


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_positives(spc_tornadoes):
    candidates = []
    for t in spc_tornadoes:
        if t["mag"] < 1:
            continue
        d = t["dt"].date()
        if d < ARCHIVE_START or d > POSITIVES_END:
            continue
        km = haversine_km(KFWS_LAT, KFWS_LON, t["lat"], t["lon"])
        if km > POSITIVE_RADIUS_KM:
            continue
        candidates.append(t | {"km": round(km, 1)})

    # Match each base case to a candidate within 5 minutes (all 5 matched to the
    # minute during development -- see build report); consume the match so it is
    # not also emitted under its auto-generated id.
    used = set()
    base_out = []
    match_log = []
    for base in BASE_POSITIVES:
        target = datetime.strptime(base["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        best, best_dt = None, None
        for i, c in enumerate(candidates):
            if i in used:
                continue
            diff = abs((c["dt"] - target).total_seconds())
            if diff <= 300 and (best is None or diff < best_dt):
                best, best_dt = i, diff
        if best is not None:
            used.add(best)
            c = candidates[best]
            base_out.append({
                "id": base["id"], "time": iso_z(c["dt"]), "lat": c["lat"], "lon": c["lon"],
                "ef": c["mag"], "km_from_kfws": c["km"], "source": "spc",
            })
            match_log.append((base["id"], "matched", best_dt))
        else:
            km = round(haversine_km(KFWS_LAT, KFWS_LON, base["fallback_lat"], base["fallback_lon"]), 1)
            base_out.append({
                "id": base["id"], "time": base["time"], "lat": base["fallback_lat"],
                "lon": base["fallback_lon"], "ef": base["ef"], "km_from_kfws": km,
                "source": "replay_smoke",
                "note": "no matching SPC row found within 5 min; using replay_smoke.py coordinates",
            })
            match_log.append((base["id"], "NOT MATCHED -- using replay_smoke fallback", None))

    auto_out = []
    seen_ids = {b["id"] for b in base_out}
    for i, c in enumerate(candidates):
        if i in used:
            continue
        d = c["dt"].date()
        slug = name_for(c["lat"], c["lon"], c["st"], c["f1"])
        cid = f"{d.isoformat()}-{slug}"
        n = 2
        base_cid = cid
        while cid in seen_ids:
            cid = f"{base_cid}-{n}"
            n += 1
        seen_ids.add(cid)
        auto_out.append({
            "id": cid, "time": iso_z(c["dt"]), "lat": c["lat"], "lon": c["lon"],
            "ef": c["mag"], "km_from_kfws": c["km"], "source": "spc",
        })

    positives = base_out + auto_out
    positives.sort(key=lambda p: p["time"])
    return positives, match_log


# --------------------------------------------------------------------------- IEM VTEC

def load_vtec_year(cache_dir: Path, year, wfo=WFO):
    url = VTEC_EVENTS_URL.format(wfo=wfo, year=year)
    raw = cache_get(url, cache_dir / f"vtec_{wfo}_{year}.json", f"VTEC events {wfo} {year}")
    try:
        return json.loads(raw)["events"]
    except (json.JSONDecodeError, KeyError):
        return []


def build_busy_dates(cache_dir: Path, years):
    """UTC calendar dates with a convective VTEC event (SV/TO.W/A, FF.W, MA.W) for WFO."""
    busy = set()
    tow_dates = set()
    for y in years:
        for e in load_vtec_year(cache_dir, y):
            key = (e.get("phenomena"), e.get("significance"))
            try:
                issue = datetime.strptime(e["issue"], "%Y-%m-%dT%H:%M:%SZ").date()
                expire = datetime.strptime(e["expire"], "%Y-%m-%dT%H:%M:%SZ").date()
            except (ValueError, KeyError, TypeError):
                continue
            if key == ("TO", "W"):
                d = issue
                while d <= expire:
                    tow_dates.add(d)
                    d += timedelta(days=1)
            if key in CONVECTIVE_PHENOM_SIG:
                d = issue
                while d <= expire:
                    busy.add(d)
                    d += timedelta(days=1)
    return busy, tow_dates


def is_quiet_day(d, busy_dates):
    return not any((d + timedelta(days=off)) in busy_dates for off in (-1, 0, 1))


def build_quiet_controls(cache_dir: Path, years, target=QUIET_TARGET, seed=42):
    busy, _ = build_busy_dates(cache_dir, list(range(min(years) - 1, max(years) + 2)))

    base_out = []
    for b in BASE_QUIET:
        t = datetime.strptime(b["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        ok = is_quiet_day(t.date(), busy)
        note = b["note"] if ok else b["note"] + " -- WARNING: does NOT satisfy the quiet rule, kept anyway"
        if not ok:
            print(f"  WARNING base quiet control {b['time']} does not satisfy the quiet-day rule")
        base_out.append({
            "id": f"quiet-{t.date().isoformat()}T{t.hour:02d}",
            "time": iso_z(t), "note": note,
        })

    rng = random.Random(seed)
    hours = [3, 9, 15, 21]
    out = list(base_out)
    seen_ids = {o["id"] for o in out}
    per_month_needed = max(1, -(-((target - len(out)) // 12)))  # ceil division, >=1
    months = list(range(1, 13))
    draw = 0
    attempts = 0
    while len(out) < target and attempts < target * 500:
        attempts += 1
        m = months[draw % 12]
        y = years[(draw // 12) % len(years)]
        hh = hours[draw % 4]
        draw += 1
        # pick a random day in that month/year, retry within the month until quiet or exhausted
        if m == 12:
            days_in_month = 31
        else:
            days_in_month = (date(y, m + 1, 1) - date(y, m, 1)).days
        day = rng.randint(1, days_in_month)
        d = date(y, m, day)
        if d < ARCHIVE_START or d > POSITIVES_END:
            continue
        if not is_quiet_day(d, busy):
            continue
        cid = f"quiet-{d.isoformat()}T{hh:02d}"
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        t = datetime(y, m, day, hh, 0, 0, tzinfo=timezone.utc)
        out.append({"id": cid, "time": iso_z(t),
                    "note": f"quiet day (no SV/TO/FF/MA VTEC for {WFO} within +-1 day), seed={seed}"})

    out.sort(key=lambda o: o["time"])
    return out


# --------------------------------------------------------------------------- SVW controls

def load_svw_year(cache_dir: Path, year, wfo=WFO):
    cache_path = cache_dir / f"svw_{wfo}_{year}.zip"
    url = WATCHWARN_URL.format(y=year, y2=year + 1, phenomena="SV", sig="W", wfo=wfo)
    raw = cache_get(url, cache_path, f"SV.W shapefile {wfo} {year}")
    import shapefile
    z = zipfile.ZipFile(io.BytesIO(raw))
    shp = [n for n in z.namelist() if n.endswith(".shp")][0]
    dbf = [n for n in z.namelist() if n.endswith(".dbf")][0]
    rd = shapefile.Reader(shp=io.BytesIO(z.read(shp)), dbf=io.BytesIO(z.read(dbf)))
    fields = [f[0] for f in rd.fields[1:]]
    out = []
    for sr in rd.iterShapeRecords():
        d = dict(zip(fields, sr.record))
        if d.get("STATUS") != "NEW":
            continue
        pts = sr.shape.points if sr.shape.points else []
        if d.get("GTYPE") == "P" and pts:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            geo_note = None
        else:
            # No polygon -- fall back to a county centroid via the UGC (not expected
            # in practice: SV.W has been polygon-based since ~2007).
            cx = cy = None
            geo_note = "no polygon; county-centroid fallback unavailable/skipped"
        if cx is None:
            continue
        try:
            issue = datetime.strptime(str(d["ISSUED"]), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        out.append({
            "issue": issue, "lat": cy, "lon": cx,
            "wfo": d.get("WFO"), "etn": d.get("ETN"), "vtec_year": d.get("VTEC_YR"),
            "prod_id": d.get("PROD_ID"), "geo_note": geo_note,
        })
    return out


def build_svw_controls(cache_dir: Path, spc_tornadoes, years, tow_dates_by_year, target=SVW_TARGET):
    tornado_days = set()
    for t in spc_tornadoes:
        km = haversine_km(KFWS_LAT, KFWS_LON, t["lat"], t["lon"])
        if km <= TORNADO_EXCLUSION_KM:
            tornado_days.add(t["dt"].date())

    all_tow_dates = set()
    for s in tow_dates_by_year.values():
        all_tow_dates |= s

    candidates = []
    for y in years:
        for w in load_svw_year(cache_dir, y):
            km = haversine_km(KFWS_LAT, KFWS_LON, w["lat"], w["lon"])
            if km > SVW_RADIUS_KM:
                continue
            d = w["issue"].date()
            if d < ARCHIVE_START or d > POSITIVES_END:
                continue
            if d in tornado_days or d in all_tow_dates:
                continue
            candidates.append(w | {"km": round(km, 1), "date": d})

    candidates.sort(key=lambda w: w["issue"])
    by_day = {}
    for c in candidates:
        by_day.setdefault(c["date"], c)   # first (earliest) warning that day wins -> dedupe to 1/day

    picked = sorted(by_day.values(), key=lambda w: w["issue"])

    out = []
    for w in picked:
        t = w["issue"] + timedelta(minutes=10)
        near = nearest_place(w["lat"], w["lon"], DFW_PLACES) or "unk"
        wid = f"{w['wfo']}-SVW-{w['etn']}-{w['vtec_year']}"
        note = f"issuance+10min; centroid {w['km']} km from KFWS, nearest town {near}; product {w['prod_id']}"
        out.append({
            "id": f"svw-{w['issue'].date().isoformat()}T{w['issue'].hour:02d}{w['issue'].minute:02d}",
            "time": iso_z(t), "warning_id": wid, "note": note,
        })

    if len(out) > target:
        # Deterministic spread: evenly-strided subsample across the chronological list
        # so years/months are not all front-loaded, rather than an arbitrary head-cut.
        stride = len(out) / target
        idx = sorted({int(i * stride) for i in range(target)})
        out = [out[i] for i in idx]

    out.sort(key=lambda o: o["time"])
    return out


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "replay_cases.json"))
    ap.add_argument("--cache-dir", default=str(_REPO / "data" / "mrms-cases"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quiet-target", type=int, default=QUIET_TARGET)
    ap.add_argument("--svw-target", type=int, default=SVW_TARGET)
    ap.add_argument("--force-refetch", action="store_true", help="ignore the on-disk cache")
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if args.force_refetch:
        for p in cache_dir.glob("*"):
            p.unlink()

    print("Loading SPC tornado CSV (all years, all EF) ...")
    spc_tornadoes = load_spc_tornadoes(cache_dir)
    print(f"  {len(spc_tornadoes)} tornado rows total")

    print("Building positives (EF1+, 100 km of KFWS, >= 2020-10-14) ...")
    positives, match_log = build_positives(spc_tornadoes)
    for bid, status, diff in match_log:
        if diff is None:
            print(f"  base case {bid}: {status}")
        else:
            print(f"  base case {bid}: {status} (Δt={diff:.0f}s)")
    print(f"  positives: {len(positives)}")

    years = list(range(2021, 2026))
    print(f"Building quiet controls (target {args.quiet_target}, years {years[0]}-{years[-1]}) ...")
    quiet = build_quiet_controls(cache_dir, years, target=args.quiet_target, seed=args.seed)
    print(f"  quiet_controls: {len(quiet)}")

    print(f"Building SV.W controls (target {args.svw_target}, years {years[0]}-{years[-1]}) ...")
    tow_dates_by_year = {}
    for y in range(years[0] - 1, years[-1] + 2):
        _, tow = build_busy_dates(cache_dir, [y])
        tow_dates_by_year[y] = tow
    svw = build_svw_controls(cache_dir, spc_tornadoes, years, tow_dates_by_year, target=args.svw_target)
    print(f"  svw_controls: {len(svw)}")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = (
        "Replay case set for the MRMS AzShear backtest gate (docs/MRMS_MIGRATION.md §3, "
        "docs/ROADMAP.md Phase 4.1), built by services/rotation/build_cases.py. Positives: "
        f"SPC tornado CSV ({SPC_TORNADO_URL}), EF1+ within {POSITIVE_RADIUS_KM} km of KFWS "
        f"({KFWS_LAT},{KFWS_LON}), {ARCHIVE_START.isoformat()}..{POSITIVES_END.isoformat()} "
        "(the S3 AzShear archive start is the hard floor), SPC local time (CST, tz=3) shifted "
        "+6h to UTC per data-tools/collect.py's fix, plus the 5 base cases from the migration "
        "doc matched back to their SPC row where possible. Quiet controls: UTC hours spread "
        "across all 12 months and the 03/09/15/21Z diurnal cycle, 2021-2025, on days with no "
        "SV.W/TO.W/SV.A/TO.A/FF.W/MA.W VTEC event for WFO FWD on that date or the adjacent day "
        "(source: mesonet.agron.iastate.edu/json/vtec_events.py), seeded-random day-of-month for "
        "reproducibility. SV.W controls: WFO FWD Severe Thunderstorm Warnings (IEM watchwarn "
        "shapefile export, STATUS=NEW polygon centroid) within 100 km of KFWS, deduped to one "
        "per UTC day, on days with no SPC tornado (any EF) within 150 km of KFWS and no FWD "
        "TO.W that day; time = issuance + 10 min. Nearest-place naming uses common/places.py's "
        "DFW town table (<=20 km) falling back to the containing Texas county."
    )

    doc = {
        "generated": generated,
        "notes": notes,
        "positives": positives,
        "quiet_controls": quiet,
        "svw_controls": svw,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"\nWrote {out_path}")

    print("\n--- Positives (id, ef, km_from_kfws, source) ---")
    for p in positives:
        print(f"  {p['id']:<32} EF{p['ef']}  {p['km_from_kfws']:>6.1f} km  {p['source']}")

    def year_breakdown(items):
        c = defaultdict(int)
        for it in items:
            c[it["time"][:4]] += 1
        return dict(sorted(c.items()))

    print("\n--- Per-year breakdown ---")
    print("  positives:", year_breakdown(positives))
    print("  quiet_controls:", year_breakdown(quiet))
    print("  svw_controls:", year_breakdown(svw))

    print(f"\nFINAL COUNTS  positives={len(positives)}  quiet_controls={len(quiet)}  "
          f"svw_controls={len(svw)}")


if __name__ == "__main__":
    main()
