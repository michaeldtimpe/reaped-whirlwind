#!/usr/bin/env python3
"""
NWS-gated alerting service (Part C).

Every POLL_INTERVAL seconds:
  1. GET NWS /alerts/active for the KFWS point.
  2. Filter to active Tornado Warnings (event="Tornado Warning", expires > now).
  3. Read /status/inference_status.json — the model's latest score is an
     annotation only; the NWS warning is the alert.
  4. For each active TO.W not in the dedupe ledger, compose an email
     (NWS text first; mechanical model readout below), send via SMTP,
     and record in the ledger. Cap 5 per cycle; deferred alerts are
     NOT written to the ledger so they remain eligible next cycle.

CLI:
  python alert_service.py                       # service loop
  python alert_service.py --test-email          # fixture-based; sends one email; SKIPS NWS gate
  python alert_service.py --test-email --dry-run   # prints body, does not send

The model NEVER originates an email. No NWS warning → no email.
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
ALERT_TO   = os.environ.get("ALERT_TO", "")

NWS_ALERTS_URL      = f"https://api.weather.gov/alerts/active?point={KFWS_LAT},{KFWS_LON}"
PER_CYCLE_EMAIL_CAP = 5
LEDGER_PRUNE_HOURS  = 24

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_warning.json"


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


def filter_to_warnings(geojson, now):
    """Active Tornado Warnings only (event="Tornado Warning", expires > now)."""
    out = []
    for feat in geojson.get("features", []) or []:
        props = feat.get("properties", {}) or {}
        if props.get("event") != "Tornado Warning":
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
        # Treat as unavailable even if a numeric score is present (it's too old to act on).
        return "unavailable", score, THRESHOLD, age
    label = "elevated" if score >= THRESHOLD else "not elevated"
    return label, score, THRESHOLD, age


# ---- email composition (mechanical/numeric language only) --------------------
def compose_email(props, model_state, score, threshold, score_age, scan_delta):
    """Returns (subject, body). No 'agrees'/'sees'/'tornadic' language anywhere."""
    area        = props.get("areaDesc")    or "—"
    headline    = props.get("headline")    or "Tornado Warning"
    description = props.get("description") or "—"
    instruction = props.get("instruction") or "—"
    effective   = props.get("effective")   or "—"
    expires     = props.get("expires")     or "—"

    subject = f"Tornado Warning: {area} (model: {model_state})"

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

    body = (
        "TORNADO WARNING — National Weather Service\n"
        f"{headline}\n\n"
        f"Area:      {area}\n"
        f"Effective: {effective}\n"
        f"Expires:   {expires}\n\n"
        "INSTRUCTIONS (NWS):\n"
        f"{instruction}\n\n"
        "DESCRIPTION (NWS):\n"
        f"{description}\n\n"
        "────────────────────────────────────────────────────────────────\n"
        "EXPERIMENTAL MODEL READOUT — informational only, NOT an alert.\n\n"
        f"Radar-signature confidence: {model_state.upper()}\n"
        f"  Score:     {score_str}\n"
        f"  Threshold: {threshold:.2f}\n"
        f"  Radar age: {score_age_str}\n"
        f"  N0B/N0S Δ: {scan_delta_str}\n\n"
        f"{readout}\n\n"
        "This model analyzes a single radar snapshot and may miss tornadoes\n"
        "or flag non-tornadic severe storms. It does NOT override or qualify\n"
        "the NWS warning above. Heed NWS guidance regardless of this readout.\n"
        f"{stale_notice}"
    )
    return subject, body


# ---- SMTP --------------------------------------------------------------------
def smtp_send(subject, body):
    """Returns None on success, error string on failure. Hard 10s timeout."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = ALERT_FROM
    msg["To"] = ALERT_TO
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


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
    """Prune rows older than LEDGER_PRUNE_HOURS by sent_at (NOT by expires —
    NWS expires can be wrong/missing; sent_at is authoritative for our window)."""
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
            "active_tornado_warnings": 0,
            "emails_sent_total": 0,
            "emails_sent_this_cycle": 0,
            "last_email_id": None,
            "last_email_time": None,
            "model_state_last_email": None,
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

    active = filter_to_warnings(geojson, now)
    infer = load_inference_status()
    model_state, score, thr, score_age = classify_model_state(infer, now)
    scan_delta = (infer or {}).get("scan_delta_seconds")

    ledger = load_ledger()
    sent_ids = {r["alert_id"] for r in ledger}

    sent_this_cycle = 0
    last_id, last_time, last_state = None, None, None
    new_status = "running"

    for props in active:
        alert_id = props.get("id")
        if not alert_id or alert_id in sent_ids:
            continue
        if sent_this_cycle >= PER_CYCLE_EMAIL_CAP:
            # Deferred — NOT written to ledger; still eligible next cycle.
            state.record_error("cap", f"deferred alert_id={alert_id} (cap={PER_CYCLE_EMAIL_CAP})")
            continue
        subject, body = compose_email(props, model_state, score, thr, score_age, scan_delta)
        err = smtp_send(subject, body)
        if err is not None:
            state.record_error("smtp", f"alert_id={alert_id}: {err}")
            new_status = "smtp_error"
            continue   # do NOT append to ledger; retry next cycle
        ledger.append({
            "alert_id": alert_id,
            "sent_at": utc_iso(now),
            "model_state": model_state,
            "score": score,
            "threshold": thr,
        })
        sent_this_cycle += 1
        last_id, last_time, last_state = alert_id, utc_iso(now), model_state

    save_ledger(ledger, now)

    status = state.snapshot()
    status["status"] = new_status
    status["last_poll_time"] = utc_iso(now)
    status["active_tornado_warnings"] = len(active)
    status["emails_sent_total"] = status.get("emails_sent_total", 0) + sent_this_cycle
    status["emails_sent_this_cycle"] = sent_this_cycle
    if last_id:
        status["last_email_id"] = last_id
        status["last_email_time"] = last_time
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
def test_email_main(dry_run: bool):
    if not FIXTURE_PATH.exists():
        sys.stderr.write(f"FATAL: fixture not found at {FIXTURE_PATH}\n")
        sys.exit(1)
    fixture = json.loads(FIXTURE_PATH.read_text())
    props = fixture.get("properties", fixture)
    infer = load_inference_status()
    now = datetime.now(timezone.utc)
    model_state, score, thr, score_age = classify_model_state(infer, now)
    scan_delta = (infer or {}).get("scan_delta_seconds")
    subject, body = compose_email(props, model_state, score, thr, score_age, scan_delta)
    if dry_run:
        print(f"SUBJECT: {subject}\n")
        print(body)
        sys.exit(0)
    if not (SMTP_HOST and SMTP_USER and ALERT_FROM and ALERT_TO):
        sys.stderr.write("FATAL: SMTP_* / ALERT_FROM / ALERT_TO env vars not set\n")
        sys.exit(1)
    err = smtp_send(subject, body)
    if err:
        sys.stderr.write(f"SMTP error: {err}\n")
        sys.exit(1)
    print(f"sent: {subject}")
    sys.exit(0)


# ---- entrypoint --------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-email", action="store_true",
                    help="compose one email from the saved fixture; SKIPS NWS gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --test-email: print body to stdout instead of sending")
    args = ap.parse_args()

    if args.test_email:
        test_email_main(args.dry_run)
        return

    state = State()
    state.commit(state.last_status)
    app = make_app(state)
    t = threading.Thread(target=poll_loop, args=(state,), daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
