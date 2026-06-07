# services/alerting — agent notes

**What it is.** NWS-gated multi-event alerter. Every 5 minutes, calls
`https://api.weather.gov/alerts/active?point=KFWS_LAT,KFWS_LON`, keeps
only alerts whose `event` is in `ALLOWED_EVENTS` (default = 8 standard
severe-weather Warnings), composes an email per unseen alert with the
NWS text first and (for Tornado Warning) a small model-readout
annotation below, plus a short SMS body, and sends via SMTP. Records
sent + suppressed alert IDs in `service-status/alerts_sent.json` so we
dedupe and respect the per-type daily cap.

**The single load-bearing invariant.** The model never originates an
email. If `filter_to_allowed_events` returns an empty list, the service
sends nothing — regardless of what the inference status says. The
inference score is purely an annotation on emails that an NWS warning
triggered, and only for Tornado Warning specifically (other event types
get a "N/A" model section).

**Anti-anthropomorphism.** No "agrees" / "disagrees" / "sees" /
"tornadic" in subject or body. Only mechanical phrasing — "elevated",
"not elevated", "unavailable", "score below threshold", "radar
features did not reach cutoff". This is so the user never reads a
model-disagrees email as "probably not serious." Edit `compose_email`
with care.

**Suppression order — get this exactly right.**
The order in `run_cycle` is intentional. Each rule has a different effect
on the ledger (write-suppressed vs defer) and getting the order wrong
will either send duplicates or starve a tornado:

1. `alert_id in sent_ids` → skip. (Both `sent` and `suppressed_daily_cap`
   rows count — once an id has been processed, never look at it again.)
2. Per-cycle cap (`PER_CYCLE_EMAIL_CAP=5`) → defer (NOT to ledger).
3. **Daily cap by event_type** (`has_recent_send_of_type`, window =
   `DAILY_CAP_SECONDS`) → write a row with `outcome="suppressed_daily_cap"`.
   Only `outcome=="sent"` rows count toward the lookback — so a row that's
   itself suppressed doesn't extend the suppression window.
4. **Cool-off** (`latest_sent_at`, window = `COOL_OFF_SECONDS`) → defer
   (NOT to ledger). Skipped entirely if `event_type ∈ COOL_OFF_BYPASS_EVENTS`.
   This is the bypass that protects Tornado Warning from getting stuck
   behind a Flood Warning's cool-off.
5. Send. Partial-failure rule unchanged from v1: if at least one recipient
   got the email, record `outcome="sent"`; total-failure → not recorded,
   retries next cycle.

**Why "rolling 24 h" instead of calendar-day for the daily cap.** A
calendar-day boundary at midnight is arbitrary — a TO.W issued at 11:55
PM and another at 12:05 AM would count as two different days, which is
not what "1 per day" usually means. Rolling 24 h ("within the last
86400 s") removes the timezone question entirely and matches the
intuitive "wait a day before alerting again" reading.

**Why Tornado Warning bypasses cool-off.** If we just sent a Flood
Warning and a tornado pops up 5 min later, holding the tornado alert for
another 25 min is unacceptable. Cool-off is for chattiness control on
lower-urgency events; tornadoes opt out. The bypass set is env-tunable
(`COOL_OFF_BYPASS_EVENTS`).

**Why the per-type cap is "successful sends only".** A
`suppressed_daily_cap` row in the ledger should NOT itself extend the
suppression window (that would mean a single send produces a 24 h+ ban
for any later event that gets suppressed in turn). The lookback checks
`outcome=="sent"` so the window decays cleanly 24 h after the actual
send.

**Model state classification.** Used only for Tornado Warning.
- `last_score == None` OR inference status `uninitialized`/`error` → `unavailable`
- `score_age > MAX_SCORE_AGE_SECONDS` OR inference status `stale` →
  `unavailable` (even if a numeric score is present)
- `score >= threshold` → `elevated`
- otherwise → `not elevated`

The threshold is a numeric annotation, not a safety threshold. Lowering
it makes "elevated" easier to reach; it does not affect whether an email
is sent.

**SMTP details.**
- Explicit `timeout=10` on `smtplib.SMTP(...)` — smtplib otherwise
  defaults to blocking forever, which would freeze the poll loop.
- STARTTLS + login is the default flow (Gmail App Password, etc).
  Variables: `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `ALERT_FROM`.
- Two recipient lists (both comma-separated):
  - `ALERT_TO` — gets the **full** email body.
  - `ALERT_TO_SMS` — gets the **short** body (subject + ~140-char body)
    via `compose_sms()` for email→SMS carrier gateways like
    `<number>@msg.fi.google.com` (Google Fi),
    `<number>@txt.att.net` (AT&T), `<number>@vtext.com` (Verizon),
    `<number>@tmomail.net` (T-Mobile).
- Both lists are sent over **one SMTP connection** per cycle for
  efficiency (`smtp_send_many`).
- **Partial-failure rule:** if at least one recipient succeeds, the
  alert is recorded in the ledger (recipients_ok / recipients_failed
  fields). Other recipients are NOT retried — retrying would
  duplicate-send to the recipients who already got it. Total-failure
  (every send fails) → NOT recorded; next cycle retries all.
- **Gotcha — "got the SMS but no email" almost always means a wrong
  `ALERT_TO` address, not a bug.** SMS gateways are just email addresses,
  so SMS and the full email ride the same `smtp_send_many` batch. A typo
  in an `ALERT_TO` address (e.g. `michaeltimpe@gmail.com` instead of
  `michaeldtimpe@gmail.com` — Gmail ignores dots but NOT a missing
  letter) is still *accepted* by the Gmail submission server: `send_message`
  returns success, the ledger records `recipients_ok`, and the bounce (if
  any) arrives asynchronously to `ALERT_FROM` — never to you. So the SMS
  lands, the ledger looks clean, and the email silently goes to the wrong
  inbox. When email is missing: (1) verify each `ALERT_TO` address
  character-for-character against the inbox you actually read; (2) check
  the recipient's Spam/Promotions (first contact from `ALERT_FROM`);
  (3) only then suspect SMTP. Confirm a fix end-to-end with
  `--test-email` overriding recipients to just yourself:
  `docker exec -e ALERT_TO=you@gmail.com -e ALERT_TO_SMS= alerting-service python alert_service.py --test-email --event "Flood Warning"`
  (fixed 2026-06-07 — recipient was the host-username spelling).

**Event composition matrix.**
| event_type        | email subject style                          | email model block      | SMS prefix      |
|---|---|---|---|
| Tornado Warning   | `Tornado Warning: <area> (model: <state>)`   | full tornado readout   | `TO.W <area> (model:<STATE>)` + score line |
| Severe T-storm    | `Severe Thunderstorm Warning: <area>`        | "N/A — tornado only"   | `SVR <area>` |
| Flash Flood       | `Flash Flood Warning: <area>`                | "N/A — tornado only"   | `FFW <area>` |
| (other allowed)   | `<event_type>: <area>`                       | "N/A — tornado only"   | abbrev fallback (initials, max 4 ch) |

**CLI.**
- `python alert_service.py` — service loop + Flask /health.
- `python alert_service.py --test-email [--event "<type>"]` — bypass NWS
  AND suppression, send one fixture-based email/SMS. `--event` selects
  which fixture to use (defaults to "Tornado Warning"). The fixture file
  is `fixtures/sample_<lowercase_underscored>.json`; falls back to
  `fixtures/sample_warning.json` (legacy TO.W) with `event` overridden.
- `python alert_service.py --test-email --dry-run` — print email +
  SMS to stdout instead of sending. Use for unit-test-style validation.

**Files.**
- `alert_service.py` — main module.
- `Dockerfile`, `requirements.txt`.
- `fixtures/sample_warning.json` — legacy Tornado Warning fixture.
- `fixtures/sample_severe_thunderstorm_warning.json` — SVR fixture.
- `fixtures/sample_flash_flood_warning.json` — FFW fixture.
- `README.md` — operator-facing summary.
