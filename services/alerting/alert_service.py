#!/usr/bin/env python3
"""
NWS-gated multi-event alerting service (Part C).

Every POLL_INTERVAL seconds:
  1. GET NWS /alerts/active for the KFWS point.
  2. Filter to active alerts whose `event` is in ALLOWED_EVENTS (e.g. Tornado
     Warning, Severe Thunderstorm Warning, Flash Flood Warning, ...).
     Watches and advisories are excluded by default.
  3. Apply layered suppression:
       a. Dedupe by NWS alert_id (one row per alert_id, ever).
       b. Daily cap: at most one notification per event_type per
          DAILY_CAP_SECONDS (rolling 24 h by default). Suppressed alerts
          ARE written to the ledger with outcome="suppressed_daily_cap"
          so we don't re-evaluate them every cycle. Events in
          DAILY_CAP_BYPASS_EVENTS (default: "Tornado Warning") skip this
          check entirely — an outbreak's 2nd and later tornado warnings
          must never be swallowed by a 24 h cap.
       c. Global cool-off: at most one notification per COOL_OFF_SECONDS
          (30 min by default) across all event_types. Events in
          COOL_OFF_BYPASS_EVENTS (default: every Warning in the default
          allowlist) skip this check — it is cross-type, so without the
          bypass a flood email would hold a tornado or severe-storm email
          back 30 min. It still throttles opted-in watches/advisories.
          Cool-off deferrals are NOT written to the ledger; they retry
          next cycle.
  4. Compose email (full body → ALERT_TO) and SMS (~140 char → ALERT_TO_SMS).
     NWS text first; for Tornado Warnings, the radar-rotation state is
     appended as an annotation: NOAA MRMS 0-2 km azimuthal shear read from
     ANNOTATION_STATUS_PATH (written by services/rotation). The email carries
     the number, location, 30-min track max and whether the strongest cell
     lies inside the warning polygon; the SMS carries the state word only.
     MODEL_ANNOTATION=off replaces it with "withdrawn" and no score. For
     non-tornado events the readout is suppressed ("N/A — rotation readout
     assesses tornado risk only").
  5. Send via SMTP. Per-cycle hard cap of 5; deferred alerts retry next cycle.
  6. Append every non-trivial outcome to DECISION_LOG_PATH (month-rotated
     JSONL). alerts_sent.json is pruned to 48 h, so that ledger cannot answer
     "was this email ever sent?" — the decision log can.

CLI:
  python alert_service.py                                # service loop
  python alert_service.py --test-email                   # fixture-based; SKIPS NWS gate AND suppression
  python alert_service.py --test-email --event "Severe Thunderstorm Warning"
  python alert_service.py --test-email --dry-run        # print, don't send

The model NEVER originates a notification. No active alert in ALLOWED_EVENTS
⇒ no email.

TESTING SEAM
  `decide_alert()` is the whole suppression policy as one pure function: it
  takes the alert props, the ledger rows, the set of already-handled alert_ids
  and an explicit `now`, and returns a Decision — no SMTP, no network, no
  clock, no file I/O. `run_cycle()` does nothing but execute that decision
  (record an error, append a ledger row, or send). Every knob is an optional
  keyword argument defaulting to the module-level env-derived value, so
  `tests/test_alert_suppression.py` can vary caps and windows without touching
  the environment. Behaviour is identical to the inline version this replaced.
"""
import argparse, json, logging, os, smtplib, ssl, sys, threading, time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import NamedTuple, Optional

from flask import Flask, jsonify

# Shared helpers are bind-mounted at /srv/reaped/common in the container (see
# docker-compose.yml); outside it they resolve relative to this file.
# `common` is a package, so its PARENT goes on the path.
sys.path.insert(0, "/srv/reaped" if Path("/srv/reaped/common").is_dir()
                else str(Path(__file__).resolve().parents[2]))
from common import geo, nws
from common.jsonlog import append_jsonl
from common.oplog import setup_logging
from common.status import atomic_write_json, deep_copy_json, utc_iso

log = logging.getLogger("alerting")

# ---- config from env (see docker-compose.yml) --------------------------------
PORT              = int(os.environ.get("PORT", "9009"))
POLL_INTERVAL     = int(os.environ.get("POLL_INTERVAL", "300"))     # 5 min
MAX_SCORE_AGE     = int(os.environ.get("MAX_SCORE_AGE_SECONDS", "1800"))
KFWS_LAT          = float(os.environ.get("KFWS_LAT",  str(nws.KFWS_LAT)))
KFWS_LON          = float(os.environ.get("KFWS_LON", str(nws.KFWS_LON)))
NWS_UA            = os.environ.get("NWS_UA", nws.NWS_USER_AGENT)
# Elevated/not-elevated cutoff. The rotation service's status file carries its
# own `threshold` (single source of truth, 0.015 s^-1 from the Phase 1 backtest);
# MODEL_RISK_THRESHOLD, when set, overrides it. DEFAULT_THRESHOLD is only the
# last resort when the status file is missing the key.
_thr_env          = os.environ.get("MODEL_RISK_THRESHOLD", "").strip()
THRESHOLD_OVERRIDE = float(_thr_env) if _thr_env else None
DEFAULT_THRESHOLD = 0.015
# api.weather.gov returns 502 / times out a few times a day. Retry inside the
# cycle (attempts = 1 + NWS_RETRIES, NWS_RETRY_BACKOFF_SECONDS between, doubling)
# rather than losing the whole POLL_INTERVAL; the streak is surfaced in /health.
NWS_RETRIES       = int(os.environ.get("NWS_RETRIES", "2"))
NWS_RETRY_BACKOFF = float(os.environ.get("NWS_RETRY_BACKOFF_SECONDS", "3"))
# MODEL_ANNOTATION=off withdraws the readout from Tornado Warning emails/SMS
# (state "withdrawn", no score). Set to off on kappa on 2026-09-06 when the CNN
# failed its replay smoke test (docs/MODEL_CARD.md "Invalidation"); it is
# switched back on at the MRMS cutover (docs/MRMS_MIGRATION.md Phase 4), when
# ANNOTATION_STATUS_PATH points at the rotation service's status file. The NWS
# relay is unaffected either way.
MODEL_ANNOTATION  = os.environ.get("MODEL_ANNOTATION", "on").strip().lower() not in ("off", "0", "false", "no")

# The annotation's status file: services/rotation writes /status/rotation_status.json
# with the same ★ keys the CNN inference service used (status, last_score,
# last_score_time) plus threshold / max_location / max_track30 /
# coverage_nonzero_fraction / product. INFERENCE_STATUS_PATH is the deprecated
# alias from the CNN era — honoured for one release when ANNOTATION_STATUS_PATH is unset.
ANNOTATION_STATUS = Path(os.environ.get("ANNOTATION_STATUS_PATH")
                         or os.environ.get("INFERENCE_STATUS_PATH")
                         or "/status/rotation_status.json")
DECISION_LOG_PATH = Path(os.environ.get("DECISION_LOG_PATH", "/logs/decisions.jsonl"))
ALERTS_SENT_PATH  = Path(os.environ.get("ALERTS_SENT_PATH", "/status/alerts_sent.json"))
STATUS_PATH       = Path(os.environ.get("STATUS_PATH", "/status/alerting_status.json"))

SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
ALERT_FROM = os.environ.get("ALERT_FROM", "")


def _split_csv(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


ALERT_TO_FULL = _split_csv(os.environ.get("ALERT_TO", ""))
ALERT_TO_SMS  = _split_csv(os.environ.get("ALERT_TO_SMS", ""))

# Event allowlist. The default (the eight standard severe-weather Warnings most
# relevant in DFW) is canonical in common/nws.py; watches and advisories are
# excluded by default — the user can opt in by extending ALLOWED_EVENTS in .env.
# Each entry is the literal NWS `properties.event` string.
DEFAULT_ALLOWED_EVENTS = ",".join(nws.DEFAULT_ALLOWED_EVENTS)
ALLOWED_EVENTS = set(_split_csv(os.environ.get("ALLOWED_EVENTS", DEFAULT_ALLOWED_EVENTS)))

# Rate-limit knobs.
#   DAILY_CAP_SECONDS:  per-event-type rolling-window cap. 86400 = 24 h.
#   COOL_OFF_SECONDS:   global throttle across all event types. 1800 = 30 min.
#   DAILY_CAP_BYPASS_EVENTS: events that skip the daily cap entirely.
#   COOL_OFF_BYPASS_EVENTS:  events that skip the global cool-off.
# The two bypass lists are independent: an event may skip one, both or neither.
# Tornado Warning skips the daily cap by default — a DFW outbreak issues several
# sequential tornado warnings for one point and #2 onward must still reach the
# phone. The cool-off is CROSS-TYPE (a Flash Flood Warning email would hold a
# Severe Thunderstorm Warning back 30 min), which is sensible for advisories a
# user opts into but not for Warnings, so every default-allowlisted Warning
# bypasses it (2026-09-06; retrospective rec #7). Cool-off still applies to any
# watch/advisory added to ALLOWED_EVENTS.
DAILY_CAP_SECONDS       = int(os.environ.get("DAILY_CAP_SECONDS", "86400"))
COOL_OFF_SECONDS        = int(os.environ.get("COOL_OFF_SECONDS", "1800"))
DAILY_CAP_BYPASS_EVENTS = set(_split_csv(os.environ.get("DAILY_CAP_BYPASS_EVENTS", "Tornado Warning")))
COOL_OFF_BYPASS_EVENTS  = set(_split_csv(os.environ.get("COOL_OFF_BYPASS_EVENTS", DEFAULT_ALLOWED_EVENTS)))

# Compact NWS-style abbreviations for SMS subjects.
EVENT_ABBREV = {
    "Tornado Warning":             "TO.W",
    "Severe Thunderstorm Warning": "SVR",
    "Flash Flood Warning":         "FFW",
    "Flood Warning":               "FLW",
    "High Wind Warning":           "HWW",
    "Winter Storm Warning":        "WSW",
    "Ice Storm Warning":           "ISW",
    "Extreme Wind Warning":        "EWW",
}

PER_CYCLE_EMAIL_CAP = 5
LEDGER_PRUNE_HOURS  = 48   # keep slightly past DAILY_CAP_SECONDS so the per-type lookback always has data

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---- helpers -----------------------------------------------------------------
# utc_iso / atomic_write_json / deep_copy_json come from common.status.
def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def event_abbrev(event_type: str) -> str:
    """Compact code for SMS subject. Falls back to first letters of each word."""
    if event_type in EVENT_ABBREV:
        return EVENT_ABBREV[event_type]
    return ("".join(w[0] for w in event_type.split())[:4] or "ALRT").upper()


# ---- NWS fetch + filter ------------------------------------------------------
class _FetchStats:
    """How many HTTP attempts the most recent fetch_nws_alerts() call made —
    surfaced in /health as nws_last_attempts (a rising number = NWS flaky)."""
    attempts = 0


FETCH_STATS = _FetchStats()


def fetch_nws_alerts(retries=None, backoff=None, sleep=time.sleep):
    """GeoJSON `features` list. Retries transient failures (any exception:
    502s, ReadTimeouts, connection resets) `retries` times with doubling
    backoff, then re-raises the LAST error — run_cycle counts that as one
    'nws' error and sets status 'nws_error'."""
    retries = NWS_RETRIES if retries is None else retries
    backoff = NWS_RETRY_BACKOFF if backoff is None else backoff
    attempts = 0
    while True:
        attempts += 1
        FETCH_STATS.attempts = attempts
        try:
            return nws.fetch_active_alerts(KFWS_LAT, KFWS_LON, user_agent=NWS_UA, timeout=10)
        except Exception as e:
            if attempts > retries:
                raise
            log.warning("nws fetch attempt %d/%d failed: %s: %s — retrying in %.0fs",
                        attempts, retries + 1, type(e).__name__, e, backoff)
            sleep(backoff)
            backoff *= 2


def filter_to_allowed_events(features, now):
    """Active alerts whose `event` is in ALLOWED_EVENTS and not yet expired."""
    out = []
    for feat in features or []:
        props = feat.get("properties", {}) or {}
        if props.get("event") not in ALLOWED_EVENTS:
            continue
        expires = parse_iso(props.get("expires"))
        if expires is None or expires <= now:
            continue
        # Keep the alert polygon for the readout's "inside the warning polygon"
        # line. Private key: nothing else in props starts with "_".
        props = dict(props)
        props["_geometry"] = feat.get("geometry")
        out.append(props)
    return out


# ---- annotation state classification ----------------------------------------
def load_annotation_status():
    if not ANNOTATION_STATUS.exists():
        return None
    try:
        return json.loads(ANNOTATION_STATUS.read_text())
    except Exception:
        return None


def effective_threshold(status):
    """MODEL_RISK_THRESHOLD env wins; else the status file's own `threshold`;
    else DEFAULT_THRESHOLD."""
    if THRESHOLD_OVERRIDE is not None:
        return THRESHOLD_OVERRIDE
    try:
        t = (status or {}).get("threshold")
        return float(t) if t is not None else DEFAULT_THRESHOLD
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def classify_model_state(status, now, annotation_enabled=None):
    """Return (state_label, score, threshold, score_age_seconds). Labels:
    'elevated', 'not elevated', 'unavailable', or 'withdrawn' when the
    annotation is switched off (MODEL_ANNOTATION=off) — no score is surfaced
    then, so a known-bad annotation can never colour an email. `score` is the
    domain-max 0-2 km azimuthal shear in s^-1; `score_age` is measured from the
    product's valid time."""
    enabled = MODEL_ANNOTATION if annotation_enabled is None else annotation_enabled
    threshold = effective_threshold(status)
    if not enabled:
        return "withdrawn", None, threshold, None
    if not status:
        return "unavailable", None, threshold, None
    score = status.get("last_score")
    if score is None or status.get("status") in (None, "uninitialized", "error"):
        return "unavailable", None, threshold, None
    score_time = parse_iso(status.get("last_score_time"))
    age = int((now - score_time).total_seconds()) if score_time else None
    if age is None or age > MAX_SCORE_AGE or status.get("status") == "stale":
        return "unavailable", score, threshold, age
    label = "elevated" if score >= threshold else "not elevated"
    return label, score, threshold, age


def annotation_details(status):
    """The readout's extra lines, lifted from the rotation status file. Every
    key is present (None when the file lacks it) so the readout never KeyErrors
    on an older status file."""
    s = status or {}
    loc = s.get("max_location") if isinstance(s.get("max_location"), dict) else None
    return {
        "source":     "mrms" if s.get("product") else ("cnn" if s.get("scan_delta_seconds") is not None else None),
        "product":    s.get("product"),
        "valid_time": s.get("product_valid_time") or s.get("last_score_time"),
        "location":   loc,
        "track30":    s.get("max_track30"),
        "coverage":   s.get("coverage_nonzero_fraction"),
        "cells_ge_threshold": s.get("cells_ge_threshold"),
    }


# ---- ledger queries (suppression) -------------------------------------------
def has_recent_send_of_type(ledger, event_type, now, window_seconds):
    """Did we successfully send (outcome=='sent') for this event_type within
    the last `window_seconds`? Suppressed rows don't count."""
    cutoff = now - timedelta(seconds=window_seconds)
    for r in ledger:
        if r.get("event_type") != event_type:
            continue
        if r.get("outcome") != "sent":
            continue
        sent = parse_iso(r.get("sent_at"))
        if sent and sent >= cutoff:
            return True, sent
    return False, None


def latest_sent_at(ledger, now, window_seconds):
    """Latest sent_at for outcome=='sent' within window. Returns dt or None."""
    cutoff = now - timedelta(seconds=window_seconds)
    latest = None
    for r in ledger:
        if r.get("outcome") != "sent":
            continue
        sent = parse_iso(r.get("sent_at"))
        if sent and sent >= cutoff and (latest is None or sent > latest):
            latest = sent
    return latest


# ---- suppression policy (pure; see "TESTING SEAM" in the module docstring) ---
SEND               = "send"
SKIP_NO_ID         = "skip_no_id"
SKIP_DUPLICATE     = "skip_duplicate"
SKIP_NOT_ALLOWED   = "skip_not_allowed"
DEFER_CYCLE_CAP    = "defer_cycle_cap"
SUPPRESS_DAILY_CAP = "suppress_daily_cap"
DEFER_COOL_OFF     = "defer_cool_off"


class Decision(NamedTuple):
    action: str
    reason: str = ""
    cool_off_until: Optional[datetime] = None


def decide_alert(props, ledger, sent_ids, now, *, sent_this_cycle=0,
                 allowed_events=None, per_cycle_cap=None, daily_cap_seconds=None,
                 daily_cap_bypass_events=None, cool_off_seconds=None,
                 cool_off_bypass_events=None) -> Decision:
    """Decide what to do with ONE active alert. Pure: no I/O, no clock, no SMTP.

    Layers, in the order run_cycle applies them:
      1. no alert_id, or alert_id already in `sent_ids`  -> skip silently
      2. event not in the allowlist                      -> skip (belt-and-braces;
         filter_to_allowed_events has already dropped these)
      3. per-cycle email cap reached                      -> defer to next cycle
      4. per-event-type rolling cap (DAILY_CAP_SECONDS), unless the event type
         is in DAILY_CAP_BYPASS_EVENTS                    -> suppress + ledger row
      5. global cool-off (COOL_OFF_SECONDS), unless the event type is in
         COOL_OFF_BYPASS_EVENTS                           -> defer, no ledger row

    The two bypass sets are independent; membership in one says nothing about
    the other.
      6. otherwise                                        -> send
    """
    allowed_events         = ALLOWED_EVENTS if allowed_events is None else allowed_events
    per_cycle_cap          = PER_CYCLE_EMAIL_CAP if per_cycle_cap is None else per_cycle_cap
    daily_cap_seconds      = DAILY_CAP_SECONDS if daily_cap_seconds is None else daily_cap_seconds
    cool_off_seconds       = COOL_OFF_SECONDS if cool_off_seconds is None else cool_off_seconds
    daily_cap_bypass_events = (DAILY_CAP_BYPASS_EVENTS if daily_cap_bypass_events is None
                               else daily_cap_bypass_events)
    cool_off_bypass_events = (COOL_OFF_BYPASS_EVENTS if cool_off_bypass_events is None
                              else cool_off_bypass_events)

    alert_id   = props.get("id")
    event_type = props.get("event")

    if not alert_id:
        return Decision(SKIP_NO_ID, "no alert id")
    if alert_id in sent_ids:
        return Decision(SKIP_DUPLICATE, "alert_id already handled")
    if event_type not in allowed_events:
        return Decision(SKIP_NOT_ALLOWED, f"{event_type!r} not in ALLOWED_EVENTS")
    if sent_this_cycle >= per_cycle_cap:
        return Decision(DEFER_CYCLE_CAP, f"per-cycle cap {per_cycle_cap} reached")

    if event_type not in daily_cap_bypass_events:
        recent_hit, recent_at = has_recent_send_of_type(ledger, event_type, now, daily_cap_seconds)
        if recent_hit:
            return Decision(SUPPRESS_DAILY_CAP, f"daily cap: last sent {utc_iso(recent_at)}")

    if event_type not in cool_off_bypass_events:
        co_last = latest_sent_at(ledger, now, cool_off_seconds)
        if co_last is not None:
            until = co_last + timedelta(seconds=cool_off_seconds)
            return Decision(DEFER_COOL_OFF, f"cool_off until {utc_iso(until)}", until)

    return Decision(SEND)


# ---- email composition (mechanical/numeric language only) --------------------
def _fmt_shear(v):
    return f"{v:.4f} s^-1" if v is not None else "—"


def _location_line(loc):
    """'near Crowley, 14 km SW of KFWS' from the status file's max_location."""
    if not loc:
        return None
    near, km, bearing = loc.get("near"), loc.get("km"), loc.get("bearing")
    parts = []
    if near:
        parts.append(f"near {near}")
    if km is not None and bearing:
        parts.append(f"{km:.0f} km {bearing} of KFWS")
    elif km is not None:
        parts.append(f"{km:.0f} km from KFWS")
    return ", ".join(parts) if parts else None


def polygon_check(loc, geometry):
    """(label, inside) for the readout: inside is True/False/None per common.geo."""
    if not loc or loc.get("lat") is None or loc.get("lon") is None:
        return "polygon check n/a (no rotation cell)", None
    inside = geo.point_in_geometry(loc.get("lat"), loc.get("lon"), geometry)
    if inside is None:
        return "polygon unavailable", None
    return ("INSIDE the warning polygon" if inside else "OUTSIDE the warning polygon"), inside


def _tornado_model_readout(model_state, score, threshold, score_age, details=None, geometry=None):
    """The MRMS rotation block of a Tornado Warning email. Mechanical wording
    only: numbers, places, distances — never 'agrees', 'sees', 'tornadic'."""
    d = details or {}
    loc = d.get("location")
    coverage = d.get("coverage")
    score_str      = _fmt_shear(score)
    threshold_str  = _fmt_shear(threshold)
    score_age_str  = f"{score_age}s" if score_age is not None else "—"
    valid_str      = d.get("valid_time") or "—"
    track_str      = _fmt_shear(d.get("track30")) if d.get("track30") is not None else "—"
    where          = _location_line(loc) or "—"

    if model_state == "elevated":
        readout = ("Azimuthal shear at or above threshold: at least one 0-2 km grid "
                   "cell within 100 km of KFWS reached the rotation cutoff. This is "
                   "NOT confirmation of a tornado on the ground — it is a single-"
                   "frame low-level rotation reading.")
    elif model_state == "not elevated":
        readout = ("Azimuthal shear below threshold: no 0-2 km grid cell within "
                   "100 km of KFWS reached the rotation cutoff in this frame. This "
                   "does NOT reduce the threat. Heed NWS guidance.")
    elif model_state == "withdrawn":
        readout = ("Annotation WITHDRAWN: the radar annotation is switched off and "
                   "is not being reported. Treat this email as a direct relay of "
                   "the NWS warning above.")
    else:
        readout = "Rotation readout suppressed (see note below)."

    if model_state == "withdrawn":
        return ("EXPERIMENTAL RADAR ROTATION READOUT — withdrawn.\n\n"
                f"{readout}\n")

    stale_notice = ""
    if model_state == "unavailable":
        if score is not None and score_age and score_age > MAX_SCORE_AGE:
            stale_notice = (f"\nNote: most recent MRMS frame is {score_age}s old "
                            f"(>{MAX_SCORE_AGE}s threshold). The readout is suppressed.\n")
        else:
            stale_notice = "\nNote: no recent MRMS frame (rotation service may be starting up or NCEP is behind).\n"

    extra = ""
    if model_state in ("elevated", "not elevated"):
        if coverage is not None and coverage == 0:
            extra = "  Signal:    no rotation signal in domain (radar coverage unverified)\n"
        else:
            label, _ = polygon_check(loc, geometry)
            extra = (f"  Location:  {where}\n"
                     f"  Polygon:   strongest cell {label}\n"
                     f"  30-min max: {track_str}\n")

    return (
        "RADAR ROTATION READOUT (NOAA MRMS, experimental annotation) — informational only, NOT an alert.\n\n"
        f"Low-level rotation: {model_state.upper()}\n"
        f"  Max 0-2 km azimuthal shear: {score_str}\n"
        f"  Threshold: {threshold_str}\n"
        f"  Valid:     {valid_str} ({score_age_str} ago)\n"
        f"{extra}\n"
        f"{readout}\n"
        f"{stale_notice}"
    )


_NON_TORNADO_MODEL_READOUT = (
    "RADAR ROTATION READOUT — not applicable.\n\n"
    "The companion readout reports NOAA MRMS low-level azimuthal shear\n"
    "near KFWS only. It does not assess this event type. Treat this email\n"
    "as a direct relay of the NWS warning above.\n"
)


def compose_email(props, event_type, model_state, score, threshold, score_age, details=None):
    """Returns (subject, body). No 'agrees'/'sees'/'tornadic' language for the
    rotation readout. For non-tornado events the readout is N/A. `details`
    is `annotation_details()`; the warning polygon comes from props['_geometry']."""
    area        = props.get("areaDesc")    or "—"
    headline    = props.get("headline")    or event_type
    description = props.get("description") or "—"
    instruction = props.get("instruction") or "—"
    effective   = props.get("effective")   or "—"
    expires     = props.get("expires")     or "—"

    if event_type == "Tornado Warning":
        subject     = f"Tornado Warning: {area} (rotation: {model_state})"
        model_block = _tornado_model_readout(model_state, score, threshold, score_age,
                                             details, props.get("_geometry"))
    else:
        subject     = f"{event_type}: {area}"
        model_block = _NON_TORNADO_MODEL_READOUT

    body = (
        f"{event_type.upper()} — National Weather Service\n"
        f"{headline}\n\n"
        f"Area:      {area}\n"
        f"Effective: {effective}\n"
        f"Expires:   {expires}\n\n"
        "INSTRUCTIONS (NWS):\n"
        f"{instruction}\n\n"
        "DESCRIPTION (NWS):\n"
        f"{description}\n\n"
        "────────────────────────────────────────────────────────────────\n"
        f"{model_block}\n"
        "This email is an automatic relay of an active NWS warning.\n"
        "The NWS guidance above is the authority. Heed it regardless of any\n"
        "radar annotation.\n"
    )
    return subject, body


def compose_sms(props, event_type, model_state, score, threshold):
    """SMS-friendly variant. ~160-char body cap. For non-tornado events the
    model annotation is omitted (it is not meaningful for non-tornado events)."""
    area    = props.get("areaDesc") or "—"
    expires = (props.get("expires") or "")[:16]
    abbrev  = event_abbrev(event_type)

    if event_type == "Tornado Warning" and model_state != "withdrawn":
        # State word only — a "0.83 vs 0.80" in an SMS implies a calibration
        # nothing has validated (retrospective rec #8). The number is in the email.
        state_short = {"elevated": "ELEVATED", "not elevated": "NOT ELEV",
                       "unavailable": "N/A"}.get(model_state, "N/A")
        subject = f"{abbrev} {area[:35]} (model:{state_short})"
        body    = (f"Tornado Warning until {expires}. Model {state_short}. "
                   f"Heed NWS. Full details emailed.")
    else:
        subject = f"{abbrev} {area[:40]}"
        body    = (f"{event_type} until {expires}. Heed NWS. "
                   f"Full details emailed.")
    return subject, body


# ---- SMTP --------------------------------------------------------------------
def smtp_send_many(messages):
    """Send a list of (recipient, subject, body) over one SMTP connection.
    Returns list of (recipient, None|error_str)."""
    if not messages:
        return []
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(SMTP_USER, SMTP_PASS)
            results = []
            for recipient, subject, body in messages:
                msg = MIMEText(body)
                msg["Subject"] = subject
                msg["From"] = ALERT_FROM
                msg["To"] = recipient
                try:
                    s.send_message(msg)
                    results.append((recipient, None))
                except Exception as e:
                    results.append((recipient, f"{type(e).__name__}: {e}"))
            return results
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        return [(r, err) for r, _, _ in messages]


# ---- dedupe ledger -----------------------------------------------------------
def load_ledger():
    if not ALERTS_SENT_PATH.exists():
        return []
    try:
        data = json.loads(ALERTS_SENT_PATH.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_ledger(rows, now):
    """Prune rows older than LEDGER_PRUNE_HOURS by sent_at."""
    cutoff = now - timedelta(hours=LEDGER_PRUNE_HOURS)
    kept = []
    for r in rows:
        sent = parse_iso(r.get("sent_at"))
        if sent and sent >= cutoff:
            kept.append(r)
    atomic_write_json(ALERTS_SENT_PATH, kept)
    return kept


# ---- decision log ------------------------------------------------------------
def log_decision(outcome, now, *, props=None, event_type=None, model_state=None,
                 score=None, threshold=None, reason=None, recipients_ok=None,
                 recipients_failed=None, error=None, details=None):
    """Append one decision to DECISION_LOG_PATH (month-rotated). Durable history:
    alerts_sent.json is pruned to LEDGER_PRUNE_HOURS, so it can never answer
    "was this email ever sent?". Never raises; never affects emails_sent_total."""
    props = props or {}
    append_jsonl(DECISION_LOG_PATH, {
        "ts":                utc_iso(now),
        "outcome":           outcome,
        "alert_id":          props.get("id"),
        "event_type":        event_type or props.get("event"),
        "area":              props.get("areaDesc"),
        "effective":         props.get("effective"),
        "expires":           props.get("expires"),
        "model_state":       model_state,
        "score":             score,
        "threshold":         threshold,
        "reason":            reason,
        "recipients_ok":     recipients_ok or [],
        "recipients_failed": recipients_failed or [],
        "error":             error,
        "annotation_source": (details or {}).get("source"),
        "max_location_near": ((details or {}).get("location") or {}).get("near"),
    })


# ---- service state -----------------------------------------------------------
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_status = {
            "status": "uninitialized",
            "last_poll_time": None,
            "active_warnings": 0,
            "active_tornado_warnings": 0,
            "active_by_type": {},
            "emails_sent_total": 0,
            "emails_sent_this_cycle": 0,
            "suppressed_daily_cap_this_cycle": 0,
            "deferred_cool_off_this_cycle": 0,
            "last_email_id": None,
            "last_email_time": None,
            "last_email_event_type": None,
            "model_state_last_email": None,
            "cool_off_until": None,
            "allowed_events": sorted(ALLOWED_EVENTS),
            "daily_cap_bypass_events": sorted(DAILY_CAP_BYPASS_EVENTS),
            "cool_off_bypass_events": sorted(COOL_OFF_BYPASS_EVENTS),
            "cool_off_seconds": COOL_OFF_SECONDS,
            "daily_cap_seconds": DAILY_CAP_SECONDS,
            "model_annotation": "on" if MODEL_ANNOTATION else "off",
            "annotation_status_path": str(ANNOTATION_STATUS),
            "annotation_source": None,
            "nws_consecutive_failures": 0,
            "nws_last_attempts": None,
            "errors": [],
        }

    def record_error(self, kind, msg):
        with self.lock:
            errs = list(self.last_status.get("errors", []))
            errs.append({"time": utc_iso(), "kind": kind, "msg": str(msg)[:200]})
            self.last_status["errors"] = errs[-5:]

    def snapshot(self):
        with self.lock:
            return deep_copy_json(self.last_status)

    def commit(self, status):
        with self.lock:
            self.last_status = status
            atomic_write_json(STATUS_PATH, status)


# ---- cycle -------------------------------------------------------------------
def run_cycle(state: State) -> dict:
    now = datetime.now(timezone.utc)
    FETCH_STATS.attempts = 0
    try:
        features = fetch_nws_alerts()
        attempts = FETCH_STATS.attempts
    except Exception as e:
        state.record_error("nws", f"{type(e).__name__}: {e}")
        log_decision("nws_error", now, error=f"{type(e).__name__}: {e}")
        status = state.snapshot()
        status["status"] = "nws_error"
        status["last_poll_time"] = utc_iso(now)
        status["nws_consecutive_failures"] = status.get("nws_consecutive_failures", 0) + 1
        status["nws_last_attempts"] = FETCH_STATS.attempts
        state.commit(status)
        log.error("cycle status=nws_error consecutive_failures=%d attempts=%d err=%s: %s",
                  status["nws_consecutive_failures"], FETCH_STATS.attempts, type(e).__name__, e)
        return status

    active = filter_to_allowed_events(features, now)
    ann = load_annotation_status()
    model_state, score, thr, score_age = classify_model_state(ann, now)
    details = annotation_details(ann)

    ledger = load_ledger()
    sent_ids = {r["alert_id"] for r in ledger if r.get("alert_id")}

    by_type = {}
    for props in active:
        by_type[props.get("event")] = by_type.get(props.get("event"), 0) + 1

    sent_this_cycle = 0
    suppressed_daily = 0
    deferred_cool_off = 0
    last_id, last_time, last_state, last_event_type = None, None, None, None
    new_status = "running"

    for props in active:
        alert_id   = props.get("id")
        event_type = props.get("event")
        decision = decide_alert(props, ledger, sent_ids, now, sent_this_cycle=sent_this_cycle)

        if decision.action in (SKIP_NO_ID, SKIP_DUPLICATE, SKIP_NOT_ALLOWED):
            continue

        if decision.action == DEFER_CYCLE_CAP:
            state.record_error("cap", f"deferred alert_id={alert_id} (cap={PER_CYCLE_EMAIL_CAP})")
            continue

        # (b) per-type daily cap — suppressed rows ARE written to the ledger so we
        # don't re-evaluate this alert_id every cycle.
        if decision.action == SUPPRESS_DAILY_CAP:
            ledger.append({
                "alert_id":         alert_id,
                "event_type":       event_type,
                "sent_at":          utc_iso(now),
                "outcome":          "suppressed_daily_cap",
                "model_state":      model_state,
                "score":            score,
                "threshold":        thr,
                "recipients_ok":    [],
                "recipients_failed": [],
                "reason":           decision.reason,
            })
            log_decision("suppressed_daily_cap", now, props=props, event_type=event_type,
                         model_state=model_state, score=score, threshold=thr, details=details,
                         reason=decision.reason)
            sent_ids.add(alert_id)
            suppressed_daily += 1
            continue

        # (c) global cool-off — NOT written to the ledger; retries next cycle.
        if decision.action == DEFER_COOL_OFF:
            state.record_error("cool_off",
                               f"deferred alert_id={alert_id} ({event_type}); "
                               f"{decision.reason}")
            log_decision("deferred_cool_off", now, props=props, event_type=event_type,
                         model_state=model_state, score=score, threshold=thr, details=details,
                         reason=decision.reason)
            deferred_cool_off += 1
            continue

        # Send.
        full_subj, full_body = compose_email(props, event_type, model_state, score, thr, score_age, details)
        sms_subj,  sms_body  = compose_sms(props, event_type, model_state, score, thr)
        messages = ([(r, full_subj, full_body) for r in ALERT_TO_FULL]
                    + [(r, sms_subj, sms_body) for r in ALERT_TO_SMS])
        if not messages:
            state.record_error("config", "no ALERT_TO / ALERT_TO_SMS recipients configured")
            log_decision("config_error", now, props=props, event_type=event_type,
                         error="no ALERT_TO / ALERT_TO_SMS recipients configured")
            new_status = "smtp_error"
            continue
        results = smtp_send_many(messages)
        any_ok = any(err is None for _, err in results)
        failed = [(r, e) for r, e in results if e is not None]
        if not any_ok:
            state.record_error("smtp", f"alert_id={alert_id}: all sends failed: {failed}")
            log_decision("smtp_error", now, props=props, event_type=event_type,
                         model_state=model_state, score=score, threshold=thr, details=details,
                         recipients_failed=[{"to": r, "err": e} for r, e in failed],
                         error="all sends failed")
            new_status = "smtp_error"
            continue
        if failed:
            state.record_error("smtp", f"alert_id={alert_id}: partial failure: {failed}")
            new_status = "smtp_error"

        ledger.append({
            "alert_id":         alert_id,
            "event_type":       event_type,
            "sent_at":          utc_iso(now),
            "outcome":          "sent",
            "model_state":      model_state,
            "score":            score,
            "threshold":        thr,
            "recipients_ok":    [r for r, e in results if e is None],
            "recipients_failed": [{"to": r, "err": e} for r, e in failed],
        })
        log_decision("sent", now, props=props, event_type=event_type,
                     model_state=model_state, score=score, threshold=thr, details=details,
                     recipients_ok=[r for r, e in results if e is None],
                     recipients_failed=[{"to": r, "err": e} for r, e in failed])
        sent_ids.add(alert_id)
        sent_this_cycle += 1
        last_id, last_time, last_state, last_event_type = alert_id, utc_iso(now), model_state, event_type

    save_ledger(ledger, now)

    co_last = latest_sent_at(ledger, now, COOL_OFF_SECONDS)
    cool_off_until = utc_iso(co_last + timedelta(seconds=COOL_OFF_SECONDS)) if co_last else None

    status = state.snapshot()
    status["status"] = new_status
    status["last_poll_time"] = utc_iso(now)
    status["active_warnings"] = len(active)
    status["annotation_source"] = details.get("source")
    status["active_tornado_warnings"] = by_type.get("Tornado Warning", 0)
    status["active_by_type"] = by_type
    status["emails_sent_total"] = status.get("emails_sent_total", 0) + sent_this_cycle
    status["emails_sent_this_cycle"] = sent_this_cycle
    status["suppressed_daily_cap_this_cycle"] = suppressed_daily
    status["deferred_cool_off_this_cycle"] = deferred_cool_off
    status["cool_off_until"] = cool_off_until
    status["nws_consecutive_failures"] = 0
    status["nws_last_attempts"] = attempts
    if last_id:
        status["last_email_id"] = last_id
        status["last_email_time"] = last_time
        status["last_email_event_type"] = last_event_type
        status["model_state_last_email"] = last_state
    state.commit(status)
    log.info("cycle status=%s active=%d by_type=%s sent=%d suppressed_daily=%d "
             "deferred_cool_off=%d model=%s score=%s nws_attempts=%d",
             new_status, len(active), json.dumps(by_type, sort_keys=True), sent_this_cycle,
             suppressed_daily, deferred_cool_off, model_state,
             "-" if score is None else f"{score:.3f}", attempts)
    return status


# ---- loop + Flask /health ----------------------------------------------------
def poll_loop(state):
    while True:
        try:
            run_cycle(state)
        except Exception as e:
            state.record_error("loop", f"{type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)


def make_app(state):
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify(state.snapshot())

    return app


# ---- --test-email mode -------------------------------------------------------
def _load_fixture_for_event(event_type):
    """Return (props, source_path). Prefers a per-event fixture file; falls
    back to the legacy sample_warning.json with `event` field overridden."""
    safe = event_type.lower().replace(" ", "_")
    candidate = FIXTURE_DIR / f"sample_{safe}.json"
    if candidate.exists():
        data = json.loads(candidate.read_text())
        props = dict(data.get("properties", data))
        props["_geometry"] = data.get("geometry")
        return props, candidate
    legacy = FIXTURE_DIR / "sample_warning.json"
    if not legacy.exists():
        raise FileNotFoundError(f"No fixture found for {event_type} (tried {candidate}, {legacy})")
    data = json.loads(legacy.read_text())
    props = dict(data.get("properties", data))
    props["_geometry"] = data.get("geometry")
    props["event"] = event_type
    props["headline"] = f"TEST FIXTURE {event_type} (no per-event fixture; legacy fallback)"
    return props, legacy


def test_email_main(event_type: str, dry_run: bool):
    try:
        props, source = _load_fixture_for_event(event_type)
    except FileNotFoundError as e:
        sys.stderr.write(f"FATAL: {e}\n")
        sys.exit(1)

    ann = load_annotation_status()
    now = datetime.now(timezone.utc)
    model_state, score, thr, score_age = classify_model_state(ann, now)
    details = annotation_details(ann)

    full_subj, full_body = compose_email(props, event_type, model_state, score, thr, score_age, details)
    sms_subj,  sms_body  = compose_sms(props, event_type, model_state, score, thr)

    if dry_run:
        print("=" * 64)
        print(f"Fixture: {source}")
        print(f"Event type: {event_type}")
        print("=" * 64)
        print("FULL  (one per ALERT_TO recipient):")
        print(f"  TO: {ALERT_TO_FULL}")
        print(f"  SUBJECT: {full_subj}\n")
        print(full_body)
        print("=" * 64)
        print(f"SMS   (one per ALERT_TO_SMS recipient; body len={len(sms_body)} chars):")
        print(f"  TO: {ALERT_TO_SMS}")
        print(f"  SUBJECT: {sms_subj}")
        print(f"  BODY:    {sms_body}")
        sys.exit(0)

    if not (SMTP_HOST and SMTP_USER and ALERT_FROM):
        sys.stderr.write("FATAL: SMTP_HOST / SMTP_USER / ALERT_FROM env vars not set\n")
        sys.exit(1)
    if not (ALERT_TO_FULL or ALERT_TO_SMS):
        sys.stderr.write("FATAL: neither ALERT_TO nor ALERT_TO_SMS is set\n")
        sys.exit(1)
    messages = ([(r, full_subj, full_body) for r in ALERT_TO_FULL]
                + [(r, sms_subj, sms_body) for r in ALERT_TO_SMS])
    results = smtp_send_many(messages)
    failed = [(r, e) for r, e in results if e is not None]
    for r, e in results:
        print(f"  {'OK' if e is None else 'FAIL'}  {r}{'' if e is None else ': ' + e}")
    sys.exit(0 if not failed else 1)


# ---- entrypoint --------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-email", action="store_true",
                    help="compose one email from the saved fixture; SKIPS NWS gate and suppression")
    ap.add_argument("--event", default="Tornado Warning",
                    help="event type to use with --test-email (must match the literal NWS string)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --test-email: print body to stdout instead of sending")
    args = ap.parse_args()

    if args.test_email:
        test_email_main(args.event, args.dry_run)
        return

    setup_logging()
    log.info("alerting starting: allowed=%s daily_cap_bypass=%s cool_off_bypass=%s "
             "model_annotation=%s nws_retries=%d",
             sorted(ALLOWED_EVENTS), sorted(DAILY_CAP_BYPASS_EVENTS),
             sorted(COOL_OFF_BYPASS_EVENTS), "on" if MODEL_ANNOTATION else "off", NWS_RETRIES)
    state = State()
    state.commit(state.last_status)
    app = make_app(state)
    t = threading.Thread(target=poll_loop, args=(state,), daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
