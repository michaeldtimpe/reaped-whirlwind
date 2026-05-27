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
          so we don't re-evaluate them every cycle.
       c. Global cool-off: at most one notification per COOL_OFF_SECONDS
          (30 min by default) across all event_types. Events in
          COOL_OFF_BYPASS_EVENTS (default: "Tornado Warning") skip this
          check — a tornado after a flood is never delayed by cool-off.
          Cool-off deferrals are NOT written to the ledger; they retry
          next cycle.
  4. Compose email (full body → ALERT_TO) and SMS (~140 char → ALERT_TO_SMS).
     NWS text first; for Tornado Warnings, the model score is appended as a
     numeric annotation. For non-tornado events the model readout is
     suppressed ("N/A — model assesses tornado risk only").
  5. Send via SMTP. Per-cycle hard cap of 5; deferred alerts retry next cycle.

CLI:
  python alert_service.py                                # service loop
  python alert_service.py --test-email                   # fixture-based; SKIPS NWS gate AND suppression
  python alert_service.py --test-email --event "Severe Thunderstorm Warning"
  python alert_service.py --test-email --dry-run        # print, don't send

The model NEVER originates a notification. No active alert in ALLOWED_EVENTS
⇒ no email.
"""
import argparse, json, os, smtplib, ssl, sys, threading, time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import requests
from flask import Flask, jsonify

# ---- config from env (see docker-compose.yml) --------------------------------
PORT              = int(os.environ.get("PORT", "9009"))
POLL_INTERVAL     = int(os.environ.get("POLL_INTERVAL", "300"))     # 5 min
MAX_SCORE_AGE     = int(os.environ.get("MAX_SCORE_AGE_SECONDS", "1800"))
KFWS_LAT          = float(os.environ.get("KFWS_LAT",  "32.5728"))
KFWS_LON          = float(os.environ.get("KFWS_LON", "-97.3031"))
NWS_UA            = os.environ.get("NWS_UA", "reaped-whirlwind/0.1 (alerting)")
THRESHOLD         = float(os.environ.get("MODEL_RISK_THRESHOLD", "0.8"))

INFERENCE_STATUS  = Path(os.environ.get("INFERENCE_STATUS_PATH", "/status/inference_status.json"))
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

# Event allowlist. Default = the eight standard severe-weather Warnings most
# relevant in DFW. Watches and advisories are excluded by default — the user
# can opt in by extending ALLOWED_EVENTS in .env. Each entry is the literal
# NWS `properties.event` string.
DEFAULT_ALLOWED_EVENTS = ",".join([
    "Tornado Warning",
    "Severe Thunderstorm Warning",
    "Flash Flood Warning",
    "Flood Warning",
    "High Wind Warning",
    "Winter Storm Warning",
    "Ice Storm Warning",
    "Extreme Wind Warning",
])
ALLOWED_EVENTS = set(_split_csv(os.environ.get("ALLOWED_EVENTS", DEFAULT_ALLOWED_EVENTS)))

# Rate-limit knobs.
#   DAILY_CAP_SECONDS:  per-event-type rolling-window cap. 86400 = 24 h.
#   COOL_OFF_SECONDS:   global throttle across all event types. 1800 = 30 min.
#   COOL_OFF_BYPASS_EVENTS: events that skip cool-off (still respect daily cap).
DAILY_CAP_SECONDS      = int(os.environ.get("DAILY_CAP_SECONDS", "86400"))
COOL_OFF_SECONDS       = int(os.environ.get("COOL_OFF_SECONDS", "1800"))
COOL_OFF_BYPASS_EVENTS = set(_split_csv(os.environ.get("COOL_OFF_BYPASS_EVENTS", "Tornado Warning")))

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

NWS_ALERTS_URL      = f"https://api.weather.gov/alerts/active?point={KFWS_LAT},{KFWS_LON}"
PER_CYCLE_EMAIL_CAP = 5
LEDGER_PRUNE_HOURS  = 48   # keep slightly past DAILY_CAP_SECONDS so the per-type lookback always has data

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---- helpers -----------------------------------------------------------------
def utc_iso(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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


def atomic_write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def event_abbrev(event_type: str) -> str:
    """Compact code for SMS subject. Falls back to first letters of each word."""
    if event_type in EVENT_ABBREV:
        return EVENT_ABBREV[event_type]
    return ("".join(w[0] for w in event_type.split())[:4] or "ALRT").upper()


# ---- NWS fetch + filter ------------------------------------------------------
def fetch_nws_alerts():
    """Raises on HTTP error. Returns the raw GeoJSON FeatureCollection."""
    r = requests.get(
        NWS_ALERTS_URL,
        headers={"User-Agent": NWS_UA, "Accept": "application/geo+json"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def filter_to_allowed_events(geojson, now):
    """Active alerts whose `event` is in ALLOWED_EVENTS and not yet expired."""
    out = []
    for feat in geojson.get("features", []) or []:
        props = feat.get("properties", {}) or {}
        if props.get("event") not in ALLOWED_EVENTS:
            continue
        expires = parse_iso(props.get("expires"))
        if expires is None or expires <= now:
            continue
        out.append(props)
    return out


# ---- model state classification ---------------------------------------------
def load_inference_status():
    if not INFERENCE_STATUS.exists():
        return None
    try:
        return json.loads(INFERENCE_STATUS.read_text())
    except Exception:
        return None


def classify_model_state(infer, now):
    """Return (state_label, score, threshold, score_age_seconds). Labels:
    'elevated', 'not elevated', 'unavailable'."""
    if not infer:
        return "unavailable", None, THRESHOLD, None
    score = infer.get("last_score")
    if score is None or infer.get("status") in (None, "uninitialized", "error"):
        return "unavailable", None, THRESHOLD, None
    score_time = parse_iso(infer.get("last_score_time"))
    age = int((now - score_time).total_seconds()) if score_time else None
    if age is None or age > MAX_SCORE_AGE or infer.get("status") == "stale":
        return "unavailable", score, THRESHOLD, age
    label = "elevated" if score >= THRESHOLD else "not elevated"
    return label, score, THRESHOLD, age


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


# ---- email composition (mechanical/numeric language only) --------------------
def _tornado_model_readout(model_state, score, threshold, score_age, scan_delta):
    score_str      = f"{score:.2f}" if score is not None else "—"
    score_age_str  = f"{score_age}s" if score_age is not None else "—"
    scan_delta_str = f"{scan_delta}s" if scan_delta is not None else "—"

    if model_state == "elevated":
        readout = ("Score at or above threshold: radar features in this scan reach "
                   "the model's tornadic-structure cutoff. This is NOT confirmation "
                   "of a tornado on the ground — it is a single-frame radar-morphology "
                   "score.")
    elif model_state == "not elevated":
        readout = ("Score below threshold: radar features in this scan did not reach "
                   "the model's tornadic-structure cutoff. This does NOT reduce the "
                   "threat. Heed NWS guidance.")
    else:
        readout = "Model readout suppressed (see note below)."

    stale_notice = ""
    if model_state == "unavailable":
        if score is not None and score_age and score_age > MAX_SCORE_AGE:
            stale_notice = (f"\nNote: most recent radar scan available to the model is "
                            f"{score_age}s old (>{MAX_SCORE_AGE}s threshold). "
                            "The model readout is suppressed.\n")
        else:
            stale_notice = "\nNote: the model has no recent score (service may be starting up).\n"

    return (
        "EXPERIMENTAL MODEL READOUT — informational only, NOT an alert.\n\n"
        f"Radar-signature confidence: {model_state.upper()}\n"
        f"  Score:     {score_str}\n"
        f"  Threshold: {threshold:.2f}\n"
        f"  Radar age: {score_age_str}\n"
        f"  N0B/N0S Δ: {scan_delta_str}\n\n"
        f"{readout}\n"
        f"{stale_notice}"
    )


_NON_TORNADO_MODEL_READOUT = (
    "EXPERIMENTAL MODEL READOUT — not applicable.\n\n"
    "The companion CNN scores tornadic radar signatures on KFWS only.\n"
    "It does not assess this event type. Treat this email as a direct\n"
    "relay of the NWS warning above.\n"
)


def compose_email(props, event_type, model_state, score, threshold, score_age, scan_delta):
    """Returns (subject, body). No 'agrees'/'sees'/'tornadic' language for the
    model readout. For non-tornado events the model readout is N/A."""
    area        = props.get("areaDesc")    or "—"
    headline    = props.get("headline")    or event_type
    description = props.get("description") or "—"
    instruction = props.get("instruction") or "—"
    effective   = props.get("effective")   or "—"
    expires     = props.get("expires")     or "—"

    if event_type == "Tornado Warning":
        subject     = f"Tornado Warning: {area} (model: {model_state})"
        model_block = _tornado_model_readout(model_state, score, threshold, score_age, scan_delta)
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
        "model annotation.\n"
    )
    return subject, body


def compose_sms(props, event_type, model_state, score, threshold):
    """SMS-friendly variant. ~160-char body cap. For non-tornado events the
    model annotation is omitted (it is not meaningful for non-tornado events)."""
    area    = props.get("areaDesc") or "—"
    expires = (props.get("expires") or "")[:16]
    abbrev  = event_abbrev(event_type)

    if event_type == "Tornado Warning":
        state_short = {"elevated": "ELEVATED", "not elevated": "NOT ELEV",
                       "unavailable": "N/A"}.get(model_state, "N/A")
        score_str = f"{score:.2f}" if score is not None else "—"
        subject = f"{abbrev} {area[:35]} (model:{state_short})"
        body    = (f"Tornado Warning until {expires}. Model {state_short} "
                   f"({score_str} vs {threshold:.2f}). Heed NWS. Full details emailed.")
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
            "cool_off_bypass_events": sorted(COOL_OFF_BYPASS_EVENTS),
            "cool_off_seconds": COOL_OFF_SECONDS,
            "daily_cap_seconds": DAILY_CAP_SECONDS,
            "errors": [],
        }

    def record_error(self, kind, msg):
        with self.lock:
            errs = list(self.last_status.get("errors", []))
            errs.append({"time": utc_iso(), "kind": kind, "msg": str(msg)[:200]})
            self.last_status["errors"] = errs[-5:]

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.last_status))

    def commit(self, status):
        with self.lock:
            self.last_status = status
            atomic_write_json(STATUS_PATH, status)


# ---- cycle -------------------------------------------------------------------
def run_cycle(state: State) -> dict:
    now = datetime.now(timezone.utc)
    try:
        geojson = fetch_nws_alerts()
    except Exception as e:
        state.record_error("nws", f"{type(e).__name__}: {e}")
        status = state.snapshot()
        status["status"] = "nws_error"
        status["last_poll_time"] = utc_iso(now)
        state.commit(status)
        return status

    active = filter_to_allowed_events(geojson, now)
    infer = load_inference_status()
    model_state, score, thr, score_age = classify_model_state(infer, now)
    scan_delta = (infer or {}).get("scan_delta_seconds")

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
        if not alert_id or alert_id in sent_ids:
            continue
        if sent_this_cycle >= PER_CYCLE_EMAIL_CAP:
            state.record_error("cap", f"deferred alert_id={alert_id} (cap={PER_CYCLE_EMAIL_CAP})")
            continue

        # (b) per-type daily cap — already sent this type recently?
        recent_hit, recent_at = has_recent_send_of_type(ledger, event_type, now, DAILY_CAP_SECONDS)
        if recent_hit:
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
                "reason":           f"daily cap: last sent {utc_iso(recent_at)}",
            })
            sent_ids.add(alert_id)
            suppressed_daily += 1
            continue

        # (c) global cool-off, unless event bypasses
        if event_type not in COOL_OFF_BYPASS_EVENTS:
            co_last = latest_sent_at(ledger, now, COOL_OFF_SECONDS)
            if co_last is not None:
                # Defer — NOT written to ledger; retry next cycle when cool-off elapses.
                until = co_last + timedelta(seconds=COOL_OFF_SECONDS)
                state.record_error("cool_off",
                                   f"deferred alert_id={alert_id} ({event_type}); "
                                   f"cool_off until {utc_iso(until)}")
                deferred_cool_off += 1
                continue

        # Send.
        full_subj, full_body = compose_email(props, event_type, model_state, score, thr, score_age, scan_delta)
        sms_subj,  sms_body  = compose_sms(props, event_type, model_state, score, thr)
        messages = ([(r, full_subj, full_body) for r in ALERT_TO_FULL]
                    + [(r, sms_subj, sms_body) for r in ALERT_TO_SMS])
        if not messages:
            state.record_error("config", "no ALERT_TO / ALERT_TO_SMS recipients configured")
            new_status = "smtp_error"
            continue
        results = smtp_send_many(messages)
        any_ok = any(err is None for _, err in results)
        failed = [(r, e) for r, e in results if e is not None]
        if not any_ok:
            state.record_error("smtp", f"alert_id={alert_id}: all sends failed: {failed}")
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
    status["active_tornado_warnings"] = by_type.get("Tornado Warning", 0)
    status["active_by_type"] = by_type
    status["emails_sent_total"] = status.get("emails_sent_total", 0) + sent_this_cycle
    status["emails_sent_this_cycle"] = sent_this_cycle
    status["suppressed_daily_cap_this_cycle"] = suppressed_daily
    status["deferred_cool_off_this_cycle"] = deferred_cool_off
    status["cool_off_until"] = cool_off_until
    if last_id:
        status["last_email_id"] = last_id
        status["last_email_time"] = last_time
        status["last_email_event_type"] = last_event_type
        status["model_state_last_email"] = last_state
    state.commit(status)
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
        return data.get("properties", data), candidate
    legacy = FIXTURE_DIR / "sample_warning.json"
    if not legacy.exists():
        raise FileNotFoundError(f"No fixture found for {event_type} (tried {candidate}, {legacy})")
    data = json.loads(legacy.read_text())
    props = dict(data.get("properties", data))
    props["event"] = event_type
    props["headline"] = f"TEST FIXTURE {event_type} (no per-event fixture; legacy fallback)"
    return props, legacy


def test_email_main(event_type: str, dry_run: bool):
    try:
        props, source = _load_fixture_for_event(event_type)
    except FileNotFoundError as e:
        sys.stderr.write(f"FATAL: {e}\n")
        sys.exit(1)

    infer = load_inference_status()
    now = datetime.now(timezone.utc)
    model_state, score, thr, score_age = classify_model_state(infer, now)
    scan_delta = (infer or {}).get("scan_delta_seconds")

    full_subj, full_body = compose_email(props, event_type, model_state, score, thr, score_age, scan_delta)
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

    state = State()
    state.commit(state.last_status)
    app = make_app(state)
    t = threading.Thread(target=poll_loop, args=(state,), daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
