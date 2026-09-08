#!/usr/bin/env python3
"""
MRMS file discovery and download — the only networked module in services/rotation.

Two sources for the same files (docs/MRMS_MIGRATION.md §1):

  * **NCEP** `https://mrms.ncep.noaa.gov/2D/<product>/` — an Apache directory
    index of the last ~26.5 h, 1-2 min behind valid time. The live source.
  * **S3** `s3://noaa-mrms-pds/CONUS/<product>_00.50/<YYYYMMDD>/` — the archive
    back to 2020-10-14, and a mirror of the live feed. Listed over plain HTTPS
    with `?list-type=2&prefix=` and parsed as XML, so no boto3: the bucket is
    anonymous and the service image stays at flask+requests+numpy+eccodes.

Filenames carry the valid time and are identical in both places:
`MRMS_<product>_00.50_YYYYMMDD-HHMMSS.grib2.gz`. Every function returns
`(valid_datetime, name-or-key)` pairs sorted by time, so callers never parse
timestamps themselves.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger("rotation.fetch")

AZSHEAR_0_2 = "MergedAzShear_0-2kmAGL"
AZSHEAR_3_6 = "MergedAzShear_3-6kmAGL"
ROTATION_TRACK_30 = "RotationTrack30min"

# Every 2D MRMS product we use is published at this pseudo-level.
LEVEL = "00.50"

NCEP_BASE_URL = "https://mrms.ncep.noaa.gov/2D/"
S3_BASE_URL = "https://noaa-mrms-pds.s3.amazonaws.com/"
S3_PREFIX_FMT = "CONUS/{product}_{level}/{date:%Y%m%d}/"

DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
_S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

# Listing a day's prefix costs an HTTP round trip and returns ~720 keys; the
# backtest asks for the same day over and over. Process-lifetime memo only —
# the service polls NCEP, not S3, so this never goes stale in production.
_s3_listing_cache: Dict[Tuple[str, date], List[Tuple[datetime, str]]] = {}


def filename_pattern(product: str) -> "re.Pattern[str]":
    """Regex matching one product's filenames, capturing the YYYYMMDD-HHMMSS stamp."""
    return re.compile(
        rf"MRMS_{re.escape(product)}_{re.escape(LEVEL)}_(\d{{8}}-\d{{6}})\.grib2\.gz")


def filename_for(product: str, valid: datetime) -> str:
    return f"MRMS_{product}_{LEVEL}_{valid:%Y%m%d-%H%M%S}.grib2.gz"


def parse_valid_time(stamp: str) -> datetime:
    """'20220405-034009' -> aware UTC datetime."""
    return datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _get(url: str, *, params=None, timeout: int = DEFAULT_TIMEOUT,
         retries: int = DEFAULT_RETRIES, session: Optional[requests.Session] = None
         ) -> requests.Response:
    """GET with a small linear backoff. Raises the last exception (or HTTPError)
    once the retries are used up — callers own the error accounting, same
    contract as `common.nws.fetch_active_alerts`."""
    get = session.get if session is not None else requests.get
    last: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            r = get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:                                # noqa: BLE001
            last = e
            if attempt == retries:
                break
            wait = 1.0 * attempt
            log.warning("GET %s failed (attempt %d/%d): %s — retrying in %.0fs",
                        url, attempt, retries, e, wait)
            time.sleep(wait)
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NCEP (live)
# ---------------------------------------------------------------------------
def ncep_list(product: str, *, session: Optional[requests.Session] = None,
              timeout: int = DEFAULT_TIMEOUT) -> List[Tuple[datetime, str]]:
    """Every file currently in the NCEP directory index for `product`, as
    (valid_time, filename), oldest first. The `.latest.grib2.gz` symlink has no
    timestamp so it never matches the pattern and is silently skipped."""
    url = f"{NCEP_BASE_URL}{product}/"
    r = _get(url, timeout=timeout, session=session)
    stamps = filename_pattern(product).findall(r.text)
    out = sorted({(parse_valid_time(s), filename_for(product, parse_valid_time(s)))
                  for s in stamps})
    log.debug("ncep_list(%s): %d files", product, len(out))
    return out


def ncep_fetch(product: str, filename: str, *,
               session: Optional[requests.Session] = None,
               timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """Raw (still gzip'd) bytes of one NCEP file."""
    return _get(f"{NCEP_BASE_URL}{product}/{filename}",
                timeout=timeout, session=session).content


# ---------------------------------------------------------------------------
# S3 (archive + mirror)
# ---------------------------------------------------------------------------
def s3_prefix(product: str, day: date) -> str:
    return S3_PREFIX_FMT.format(product=product, level=LEVEL, date=day)


def s3_list(product: str, day, *, session: Optional[requests.Session] = None,
            timeout: int = DEFAULT_TIMEOUT,
            use_cache: bool = True) -> List[Tuple[datetime, str]]:
    """All keys under one UTC day's prefix, as (valid_time, key), oldest first.

    Follows `NextContinuationToken` — a day holds ~720 keys, comfortably inside
    the 1000-key page, but a truncated page would silently drop the end of the
    day and that is exactly the kind of gap a backtest must not have.
    """
    if isinstance(day, datetime):
        day = day.astimezone(timezone.utc).date()
    cache_key = (product, day)
    if use_cache and cache_key in _s3_listing_cache:
        return _s3_listing_cache[cache_key]

    prefix = s3_prefix(product, day)
    pattern = filename_pattern(product)
    out: List[Tuple[datetime, str]] = []
    token: Optional[str] = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        r = _get(S3_BASE_URL, params=params, timeout=timeout, session=session)
        root = ET.fromstring(r.text)
        for contents in root.findall("s3:Contents", _S3_NAMESPACE):
            node = contents.find("s3:Key", _S3_NAMESPACE)
            key = node.text if node is not None else None
            if not key:
                continue
            m = pattern.search(key)
            if m:
                out.append((parse_valid_time(m.group(1)), key))
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=_S3_NAMESPACE)
        token = root.findtext("s3:NextContinuationToken", namespaces=_S3_NAMESPACE)
        if truncated.lower() != "true" or not token:
            break
    out.sort()
    log.debug("s3_list(%s, %s): %d keys", product, day, len(out))
    if use_cache:
        _s3_listing_cache[cache_key] = out
    return out


def s3_fetch(key: str, *, session: Optional[requests.Session] = None,
             timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """Raw (still gzip'd) bytes of one S3 object."""
    return _get(S3_BASE_URL + key, timeout=timeout, session=session).content


def _days_spanning(start: datetime, end: datetime) -> List[date]:
    """Every UTC day touched by [start, end], inclusive."""
    d = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    days = []
    while d <= last:
        days.append(d)
        d += timedelta(days=1)
    return days


def s3_window(product: str, start: datetime, end: datetime, *,
              session: Optional[requests.Session] = None
              ) -> List[Tuple[datetime, str]]:
    """Every file with a valid time in [start, end], crossing UTC midnight if
    the window does."""
    seen: Dict[datetime, str] = {}
    for day in _days_spanning(start, end):
        for valid, key in s3_list(product, day, session=session):
            if start <= valid <= end:
                seen[valid] = key
    return sorted(seen.items())


def s3_nearest(product: str, when: datetime, max_delta_s: int = 180, *,
               session: Optional[requests.Session] = None
               ) -> Optional[Tuple[datetime, str]]:
    """The single file whose valid time is closest to `when`, or None when the
    closest is further than `max_delta_s`.

    Lists the neighbouring UTC day too when `when` sits near midnight, so a
    03:41Z case never silently misses the 23:59Z file that was actually nearest.
    """
    window = timedelta(seconds=max_delta_s)
    candidates = s3_window(product, when - window, when + window, session=session)
    if not candidates:
        return None
    best = min(candidates, key=lambda kv: abs((kv[0] - when).total_seconds()))
    if abs((best[0] - when).total_seconds()) > max_delta_s:
        return None
    return best
