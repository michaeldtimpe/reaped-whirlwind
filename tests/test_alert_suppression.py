"""The load-bearing one: the alerting service's suppression policy.

Everything here runs through `alert_service.decide_alert()` — the pure seam
documented in that module's docstring — or through `run_cycle()` with the NWS
fetch and SMTP send stubbed out. No network, no SMTP, no docker, no files
outside tmp_path. The policy tests pin an explicit `now`; the run_cycle tests
anchor their fixtures to the real clock, since NWS expiry filtering uses it.

Invariants under test (documented in CLAUDE.md and services/alerting/AGENT.md):
  * no NWS warning in ALLOWED_EVENTS  =>  no email, ever
  * one notification per alert_id, ever
  * one notification per event type per DAILY_CAP_SECONDS (rolling), EXCEPT
    DAILY_CAP_BYPASS_EVENTS — an outbreak's 2nd tornado warning must get through
  * one notification per COOL_OFF_SECONDS globally, EXCEPT COOL_OFF_BYPASS_EVENTS
  * the model score annotates Tornado Warnings only
  * the SMS body stays inside one message
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from services.alerting import alert_service as alert


NOW = datetime(2026, 5, 27, 18, 0, 0, tzinfo=timezone.utc)
SMS_BODY_LIMIT = 160   # one GSM-7 segment; compose_sms's documented cap


def props(event="Tornado Warning", alert_id="urn:oid:2.49.0.1.840.0.abc",
          expires_in_h=1, **extra):
    p = {
        "id": alert_id,
        "event": event,
        "areaDesc": "Tarrant, TX; Dallas, TX",
        "headline": f"{event} issued May 27 at 1:00PM CDT until May 27 at 1:45PM CDT",
        "description": "At 100 PM CDT, a severe thunderstorm capable of producing a tornado...",
        "instruction": "TAKE COVER NOW!",
        "effective": alert.utc_iso(NOW),
        "expires": alert.utc_iso(NOW + timedelta(hours=expires_in_h)),
    }
    p.update(extra)
    return p


def live_props(**kw):
    """Same fixture, but anchored to the real clock — run_cycle filters on
    `expires` against datetime.now(), so a frozen NOW would look expired."""
    now = datetime.now(timezone.utc)
    p = props(**kw)
    p["effective"] = alert.utc_iso(now)
    p["expires"] = alert.utc_iso(now + timedelta(hours=1))
    return p


def sent_row(event, minutes_ago, alert_id="prev", outcome="sent"):
    return {
        "alert_id": alert_id,
        "event_type": event,
        "sent_at": alert.utc_iso(NOW - timedelta(minutes=minutes_ago)),
        "outcome": outcome,
    }


def decide(p, ledger=(), sent_ids=(), **kw):
    return alert.decide_alert(p, list(ledger), set(sent_ids), NOW, **kw)


# ---- gate 0: the allowlist is the only thing that originates an email --------

def test_non_allowlisted_event_never_alerts():
    d = decide(props(event="Tornado Watch"))
    assert d.action == alert.SKIP_NOT_ALLOWED


def test_filter_drops_events_outside_the_allowlist():
    feats = [{"properties": props(event=e, alert_id=e)} for e in
             ("Tornado Warning", "Tornado Watch", "Heat Advisory", "Flood Warning")]
    kept = [p["event"] for p in alert.filter_to_allowed_events(feats, NOW)]
    assert kept == ["Tornado Warning", "Flood Warning"]


def test_filter_drops_expired_and_undated_alerts():
    feats = [
        {"properties": props(alert_id="a", expires_in_h=-1)},
        {"properties": props(alert_id="b") | {"expires": None}},
        {"properties": props(alert_id="c", expires_in_h=1)},
    ]
    assert [p["id"] for p in alert.filter_to_allowed_events(feats, NOW)] == ["c"]


def test_the_eight_default_warnings_are_the_documented_ones():
    from common.nws import DEFAULT_ALLOWED_EVENTS
    assert len(DEFAULT_ALLOWED_EVENTS) == 8
    assert "Tornado Warning" in DEFAULT_ALLOWED_EVENTS
    assert not any("Watch" in e or "Advisory" in e for e in DEFAULT_ALLOWED_EVENTS)


# ---- gate a: per-alert_id dedupe --------------------------------------------

def test_a_fresh_alert_id_sends():
    assert decide(props()).action == alert.SEND


def test_an_already_handled_alert_id_is_skipped():
    p = props(alert_id="urn:oid:dup")
    assert decide(p, sent_ids={"urn:oid:dup"}).action == alert.SKIP_DUPLICATE


def test_an_alert_without_an_id_is_skipped():
    p = props()
    del p["id"]
    assert decide(p).action == alert.SKIP_NO_ID


# ---- per-cycle cap ----------------------------------------------------------

def test_per_cycle_cap_defers_rather_than_dropping():
    d = decide(props(), sent_this_cycle=alert.PER_CYCLE_EMAIL_CAP)
    assert d.action == alert.DEFER_CYCLE_CAP


def test_per_cycle_cap_is_checked_before_the_daily_cap():
    """A deferred alert must NOT be burned into the ledger — it retries next cycle."""
    ledger = [sent_row("Tornado Warning", minutes_ago=30)]
    d = decide(props(alert_id="new"), ledger=ledger, sent_this_cycle=99)
    assert d.action == alert.DEFER_CYCLE_CAP


# ---- gate b: per-event-type rolling daily cap -------------------------------

def test_same_type_within_the_daily_cap_is_suppressed():
    ledger = [sent_row("Severe Thunderstorm Warning", minutes_ago=60)]
    d = decide(props(event="Severe Thunderstorm Warning", alert_id="second"),
               ledger=ledger, cool_off_seconds=0)
    assert d.action == alert.SUPPRESS_DAILY_CAP
    assert "daily cap: last sent" in d.reason


def test_same_type_outside_the_daily_cap_sends_again():
    ledger = [sent_row("Severe Thunderstorm Warning", minutes_ago=25 * 60)]
    assert decide(props(event="Severe Thunderstorm Warning", alert_id="next-day"),
                  ledger=ledger).action == alert.SEND


# ---- daily-cap bypass (the P0 fix from docs/RETROSPECTIVE.md §3) -------------

def test_tornado_warning_bypasses_the_daily_cap():
    """An outbreak issues several sequential tornado warnings for one point.
    Warning #2 within 24 h must still be sent, not written off as a duplicate."""
    ledger = [sent_row("Tornado Warning", minutes_ago=20)]
    assert decide(props(alert_id="second"), ledger=ledger).action == alert.SEND


def test_a_non_bypassed_type_is_still_capped():
    ledger = [sent_row("Flood Warning", minutes_ago=20)]
    assert decide(props(event="Flood Warning", alert_id="second"), ledger=ledger,
                  cool_off_seconds=0).action == alert.SUPPRESS_DAILY_CAP


def test_daily_cap_bypass_still_respects_dedupe():
    ledger = [sent_row("Tornado Warning", minutes_ago=20, alert_id="dup")]
    assert decide(props(alert_id="dup"), ledger=ledger,
                  sent_ids={"dup"}).action == alert.SKIP_DUPLICATE


def test_daily_cap_bypass_still_respects_the_per_cycle_cap():
    ledger = [sent_row("Tornado Warning", minutes_ago=20)]
    assert decide(props(alert_id="second"), ledger=ledger,
                  sent_this_cycle=alert.PER_CYCLE_EMAIL_CAP).action == alert.DEFER_CYCLE_CAP


def test_the_daily_cap_bypass_set_is_configurable():
    ledger = [sent_row("Tornado Warning", minutes_ago=20)]
    assert decide(props(alert_id="second"), ledger=ledger,
                  daily_cap_bypass_events=set(),
                  cool_off_seconds=0).action == alert.SUPPRESS_DAILY_CAP
    assert decide(props(event="Flood Warning", alert_id="x"), ledger=ledger,
                  daily_cap_bypass_events={"Flood Warning"},
                  cool_off_seconds=0).action == alert.SEND


def test_the_two_bypass_lists_are_independent():
    """Daily-cap bypass must not imply cool-off bypass, or vice versa."""
    ledger = [sent_row("Flood Warning", minutes_ago=5),
              sent_row("High Wind Warning", minutes_ago=20)]
    p = props(event="High Wind Warning", alert_id="x")

    # Bypasses the cap, but the recent Flood Warning still holds the cool-off.
    assert decide(p, ledger=ledger, daily_cap_bypass_events={"High Wind Warning"},
                  cool_off_bypass_events=set()).action == alert.DEFER_COOL_OFF
    # Bypasses cool-off, but its own 20-min-old send still trips the daily cap.
    assert decide(p, ledger=ledger, daily_cap_bypass_events=set(),
                  cool_off_bypass_events={"High Wind Warning"}).action == alert.SUPPRESS_DAILY_CAP
    # Both -> through.
    assert decide(p, ledger=ledger, daily_cap_bypass_events={"High Wind Warning"},
                  cool_off_bypass_events={"High Wind Warning"}).action == alert.SEND


def test_the_defaults_put_tornado_warning_in_both_bypass_lists():
    """Daily cap: only Tornado Warning is exempt. Cool-off is cross-type, so
    every default Warning is exempt from it (retrospective rec #7) — it only
    throttles watches/advisories a user opts into."""
    from common.nws import DEFAULT_ALLOWED_EVENTS
    assert alert.DAILY_CAP_BYPASS_EVENTS == {"Tornado Warning"}
    assert alert.COOL_OFF_BYPASS_EVENTS == set(DEFAULT_ALLOWED_EVENTS)
    assert "Tornado Warning" in alert.COOL_OFF_BYPASS_EVENTS


def test_the_daily_cap_is_per_event_type():
    ledger = [sent_row("Flood Warning", minutes_ago=60)]
    assert decide(props(event="Severe Thunderstorm Warning", alert_id="x"),
                  ledger=ledger, cool_off_seconds=0).action == alert.SEND


def test_suppressed_rows_do_not_themselves_extend_the_daily_cap():
    """Only outcome=='sent' counts — a suppressed row must not keep the window open."""
    ledger = [sent_row("Flood Warning", minutes_ago=60, outcome="suppressed_daily_cap")]
    d = decide(props(event="Flood Warning", alert_id="x"), ledger=ledger,
               cool_off_seconds=0)
    assert d.action == alert.SEND


def test_daily_cap_window_is_configurable():
    ledger = [sent_row("Flood Warning", minutes_ago=90)]
    p = props(event="Flood Warning", alert_id="x")
    assert decide(p, ledger=ledger, daily_cap_seconds=3600,
                  cool_off_seconds=0).action == alert.SEND
    assert decide(p, ledger=ledger, daily_cap_seconds=7200,
                  cool_off_seconds=0).action == alert.SUPPRESS_DAILY_CAP


# ---- gate c: global cool-off ------------------------------------------------

def test_cool_off_defers_a_different_event_type():
    """An opted-in advisory is held back by a recent send of any other type."""
    ledger = [sent_row("Severe Thunderstorm Warning", minutes_ago=10)]
    d = decide(props(event="Tornado Watch", alert_id="x"), ledger=ledger,
               allowed_events={"Tornado Watch", "Severe Thunderstorm Warning"})
    assert d.action == alert.DEFER_COOL_OFF
    assert d.cool_off_until == (NOW - timedelta(minutes=10)
                                + timedelta(seconds=alert.COOL_OFF_SECONDS))
    assert "cool_off until" in d.reason


def test_cool_off_elapsed_lets_the_next_type_through():
    ledger = [sent_row("Severe Thunderstorm Warning", minutes_ago=31)]
    assert decide(props(event="Tornado Watch", alert_id="x"), ledger=ledger,
                  allowed_events={"Tornado Watch"}).action == alert.SEND


def test_default_warnings_are_not_delayed_by_each_other():
    """A Flash Flood Warning email must not hold a Severe Thunderstorm Warning
    back 30 min (retrospective rec #7)."""
    ledger = [sent_row("Flash Flood Warning", minutes_ago=1)]
    for ev in ("Severe Thunderstorm Warning", "High Wind Warning", "Flood Warning"):
        assert decide(props(event=ev, alert_id="x"), ledger=ledger).action == alert.SEND, ev


def test_tornado_warning_bypasses_cool_off():
    """A tornado after a flood is never delayed by the global throttle."""
    ledger = [sent_row("Flood Warning", minutes_ago=1)]
    assert decide(props(event="Tornado Warning"), ledger=ledger).action == alert.SEND


def test_a_non_bypass_event_is_deferred_in_the_same_situation():
    ledger = [sent_row("Flood Warning", minutes_ago=1)]
    assert decide(props(event="High Wind Warning", alert_id="x"), ledger=ledger,
                  cool_off_bypass_events={"Tornado Warning"}).action == alert.DEFER_COOL_OFF


def test_the_bypass_set_is_configurable():
    ledger = [sent_row("Flood Warning", minutes_ago=1)]
    d = decide(props(event="Tornado Warning"), ledger=ledger,
               cool_off_bypass_events=set())
    assert d.action == alert.DEFER_COOL_OFF


# ---- model annotation -------------------------------------------------------

def test_tornado_warning_email_carries_the_numeric_model_readout():
    subject, body = alert.compose_email(props(), "Tornado Warning",
                                        "elevated", 0.91, 0.8, 120, 60)
    assert subject == "Tornado Warning: Tarrant, TX; Dallas, TX (model: elevated)"
    assert "Score:     0.91" in body
    assert "Threshold: 0.80" in body
    assert "EXPERIMENTAL MODEL READOUT" in body
    assert "TAKE COVER NOW!" in body          # NWS instruction still relayed verbatim


def test_non_tornado_email_shows_no_score_at_all():
    subject, body = alert.compose_email(props(event="Flood Warning"), "Flood Warning",
                                        "elevated", 0.91, 0.8, 120, 60)
    assert subject == "Flood Warning: Tarrant, TX; Dallas, TX"
    assert "model" not in subject
    assert "0.91" not in body
    assert "not applicable" in body
    assert "does not assess this event type" in body


def test_unavailable_model_state_is_stated_not_hidden():
    _, body = alert.compose_email(props(), "Tornado Warning", "unavailable", None, 0.8, None, None)
    assert "UNAVAILABLE" in body
    assert "Model readout suppressed" in body


@pytest.mark.parametrize("event", sorted(alert.ALLOWED_EVENTS))
def test_sms_body_fits_one_message_for_every_allowlisted_event(event):
    long_area = "; ".join(f"County{n}, TX" for n in range(12))
    subject, body = alert.compose_sms(props(event=event, areaDesc=long_area),
                                      event, "not elevated", 0.4321, 0.8)
    assert len(body) <= SMS_BODY_LIMIT, f"{event}: {len(body)} chars"
    assert subject.startswith(alert.event_abbrev(event))


def test_sms_annotates_only_tornado_warnings():
    """The SMS carries the state WORD only — a numeric score in 140 chars
    implies a calibration nothing has validated (retrospective rec #8)."""
    subj, tornado = alert.compose_sms(props(), "Tornado Warning", "elevated", 0.91, 0.8)
    _, flood = alert.compose_sms(props(event="Flood Warning"), "Flood Warning",
                                 "elevated", 0.91, 0.8)
    assert "Model ELEVATED." in tornado
    assert "0.91" not in tornado and "0.80" not in tornado
    assert "(model:ELEVATED)" in subj
    assert "0.91" not in flood
    assert "Heed NWS" in flood


# ---- MODEL_ANNOTATION=off: the readout is withdrawn, never scored ------------

def test_withdrawn_annotation_reports_no_score_anywhere():
    infer = {"status": "running", "last_score": 0.97,
             "last_score_time": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
    state, score, thr, age = alert.classify_model_state(infer, NOW, annotation_enabled=False)
    assert (state, score, age) == ("withdrawn", None, None)
    subj, body = alert.compose_email(props(), "Tornado Warning", state, score, thr, age, 120)
    assert "withdrawn" in subj and "WITHDRAWN" in body
    assert "0.97" not in body and "Score:" not in body
    assert "Heed" in body or "NWS" in body
    ssubj, sbody = alert.compose_sms(props(), "Tornado Warning", state, score, thr)
    assert "model" not in ssubj.lower() and "Model" not in sbody
    assert "0.97" not in sbody and len(sbody) <= SMS_BODY_LIMIT


def test_annotation_on_still_scores():
    infer = {"status": "running", "last_score": 0.97,
             "last_score_time": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
    state, score, _, _ = alert.classify_model_state(infer, NOW, annotation_enabled=True)
    assert (state, score) == ("elevated", 0.97)


# ---- run_cycle wiring (still offline) ---------------------------------------

@pytest.fixture
def cycle(tmp_path, monkeypatch):
    """run_cycle with the NWS fetch and SMTP stubbed; ledger/status in tmp_path."""
    monkeypatch.setattr(alert, "ALERTS_SENT_PATH", tmp_path / "alerts_sent.json")
    monkeypatch.setattr(alert, "STATUS_PATH", tmp_path / "alerting_status.json")
    monkeypatch.setattr(alert, "INFERENCE_STATUS", tmp_path / "inference_status.json")
    monkeypatch.setattr(alert, "DECISION_LOG_PATH", tmp_path / "logs" / "decisions.jsonl")
    monkeypatch.setattr(alert, "ALERT_TO_FULL", ["ops@example.com"])
    monkeypatch.setattr(alert, "ALERT_TO_SMS", ["5555555555@txt.example.net"])

    sent = []

    def fake_smtp(messages):
        sent.extend(messages)
        return [(r, None) for r, _, _ in messages]

    monkeypatch.setattr(alert, "smtp_send_many", fake_smtp)

    def run(features):
        monkeypatch.setattr(alert, "fetch_nws_alerts", lambda: features)
        return alert.run_cycle(alert.State()), sent

    return run


def test_no_allowlisted_warning_means_no_email(cycle):
    status, sent = cycle([{"properties": live_props(event="Tornado Watch", alert_id="w")}])
    assert sent == []
    assert status["emails_sent_this_cycle"] == 0
    assert status["active_warnings"] == 0
    assert status["status"] == "running"


def test_an_allowlisted_warning_emails_both_recipient_lists(cycle):
    status, sent = cycle([{"properties": live_props()}])
    assert [r for r, _, _ in sent] == ["ops@example.com", "5555555555@txt.example.net"]
    assert status["emails_sent_this_cycle"] == 1
    assert status["active_tornado_warnings"] == 1
    assert status["last_email_event_type"] == "Tornado Warning"


def test_a_repeat_of_the_same_alert_id_sends_once(cycle):
    feature = {"properties": live_props()}
    cycle([feature])
    status, sent = cycle([feature])
    assert status["emails_sent_this_cycle"] == 0
    assert len(sent) == 2          # only the first cycle's two messages


def test_nws_error_accounting_keeps_its_shape(cycle, monkeypatch):
    def boom():
        raise RuntimeError("503 Server Error")

    monkeypatch.setattr(alert, "fetch_nws_alerts", boom)
    state = alert.State()
    status = alert.run_cycle(state)
    assert status["status"] == "nws_error"
    assert status["errors"][-1]["kind"] == "nws"
    assert "RuntimeError: 503 Server Error" == status["errors"][-1]["msg"]
    assert status["last_poll_time"]
    assert status["nws_consecutive_failures"] == 1
    status = alert.run_cycle(state)
    assert status["nws_consecutive_failures"] == 2


def test_a_good_poll_resets_the_nws_failure_streak(cycle, monkeypatch):
    state = alert.State()
    monkeypatch.setattr(alert, "fetch_nws_alerts", lambda: (_ for _ in ()).throw(RuntimeError("502")))
    assert alert.run_cycle(state)["nws_consecutive_failures"] == 1
    monkeypatch.setattr(alert, "fetch_nws_alerts", lambda: [])
    status = alert.run_cycle(state)
    assert status["nws_consecutive_failures"] == 0
    assert status["status"] == "running"


def test_status_json_keeps_every_documented_key(cycle):
    status, _ = cycle([{"properties": live_props()}])
    for key in ("status", "last_poll_time", "active_warnings", "active_tornado_warnings",
                "active_by_type", "emails_sent_total", "emails_sent_this_cycle",
                "suppressed_daily_cap_this_cycle", "deferred_cool_off_this_cycle",
                "last_email_id", "last_email_time", "last_email_event_type",
                "model_state_last_email", "cool_off_until", "allowed_events",
                "daily_cap_bypass_events", "cool_off_bypass_events",
                "cool_off_seconds", "daily_cap_seconds", "errors"):
        assert key in status, key


# ---- decision log -----------------------------------------------------------

def read_decisions(tmp_path):
    from common.jsonlog import month_path
    path = month_path(tmp_path / "logs" / "decisions.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_send_is_recorded_in_the_decision_log(cycle, tmp_path):
    cycle([{"properties": live_props()}])
    rows = read_decisions(tmp_path)
    assert [r["outcome"] for r in rows] == ["sent"]
    row = rows[0]
    assert row["event_type"] == "Tornado Warning"
    assert row["alert_id"] == "urn:oid:2.49.0.1.840.0.abc"
    assert row["recipients_ok"] == ["ops@example.com", "5555555555@txt.example.net"]
    assert row["recipients_failed"] == []
    assert row["model_state"] == "unavailable"      # no inference status file
    assert row["ts"].endswith("Z")


def test_a_daily_cap_suppression_is_recorded(cycle, tmp_path):
    """Flood Warning is not in DAILY_CAP_BYPASS_EVENTS, so #2 is suppressed —
    and the suppression must leave a durable trace, not just a 48 h ledger row."""
    cycle([{"properties": live_props(event="Flood Warning", alert_id="fl-1")}])
    cycle([{"properties": live_props(event="Flood Warning", alert_id="fl-2")}])

    outcomes = [r["outcome"] for r in read_decisions(tmp_path)]
    assert outcomes == ["sent", "suppressed_daily_cap"]
    suppressed = read_decisions(tmp_path)[1]
    assert suppressed["alert_id"] == "fl-2"
    assert "daily cap: last sent" in suppressed["reason"]


def test_two_tornado_warnings_in_a_row_both_send(cycle, tmp_path):
    """End-to-end proof of the P0 fix, through run_cycle rather than decide_alert."""
    cycle([{"properties": live_props(alert_id="to-1")}])
    status, sent = cycle([{"properties": live_props(alert_id="to-2")}])

    assert status["emails_sent_this_cycle"] == 1
    assert [r["outcome"] for r in read_decisions(tmp_path)] == ["sent", "sent"]
    assert len(sent) == 4          # two recipients x two warnings


def test_an_nws_failure_is_recorded(cycle, tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("503 Server Error")

    monkeypatch.setattr(alert, "fetch_nws_alerts", boom)
    alert.run_cycle(alert.State())

    rows = read_decisions(tmp_path)
    assert rows[-1]["outcome"] == "nws_error"
    assert "RuntimeError: 503 Server Error" == rows[-1]["error"]


def test_a_total_smtp_failure_is_recorded(cycle, tmp_path, monkeypatch):
    monkeypatch.setattr(alert, "smtp_send_many",
                        lambda msgs: [(r, "SMTPAuthenticationError: 535") for r, _, _ in msgs])
    status, _ = cycle([{"properties": live_props()}])

    assert status["status"] == "smtp_error"
    assert status["emails_sent_this_cycle"] == 0
    rows = read_decisions(tmp_path)
    assert rows[-1]["outcome"] == "smtp_error"
    assert rows[-1]["recipients_failed"][0]["to"] == "ops@example.com"


def test_a_broken_log_mount_does_not_stop_alerting(cycle, tmp_path, monkeypatch):
    """A read-only or missing /logs must never cost us an email."""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("i am a file")
    monkeypatch.setattr(alert, "DECISION_LOG_PATH", blocked / "decisions.jsonl")

    status, sent = cycle([{"properties": live_props()}])
    assert status["emails_sent_this_cycle"] == 1
    assert len(sent) == 2
