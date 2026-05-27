# alerting

NWS-gated multi-event alerter for **reaped-whirlwind**. Sends one email + one
SMS per active NWS warning whose `event` is in `ALLOWED_EVENTS` (default: the
eight standard severe-weather Warnings most relevant in DFW — Tornado, Severe
Thunderstorm, Flash Flood, Flood, High Wind, Winter Storm, Ice Storm, Extreme
Wind). Tornado Warning emails additionally carry the experimental CNN's score
as a numeric annotation; for other event types the model section reads
"N/A — CNN assesses tornado risk only." The model never originates an alert.

## How to run locally
```
docker-compose -p reaped-whirlwind up --build alerting
curl http://localhost:9009/health | jq
```

## Validate the email/SMS pipeline without a real warning
```
# Print what would be sent for any allowlisted event type — no SMTP traffic
docker-compose run --rm alerting python /app/alert_service.py \
  --test-email --dry-run --event "Severe Thunderstorm Warning"

# Actually send the fixture to ALERT_TO / ALERT_TO_SMS
docker-compose run --rm alerting python /app/alert_service.py \
  --test-email --event "Tornado Warning"
```

`--test-email` SKIPS the NWS gate AND all suppression (dedupe, daily cap,
cool-off). Fixtures live under `fixtures/sample_<event>.json` (per-event) and
fall back to `fixtures/sample_warning.json` (legacy Tornado Warning, used for
event types without a dedicated fixture). Every human-visible field says
"TEST FIXTURE — do not act" so a test email is unmistakable.

## Suppression — the layered rules

Every active alert in `ALLOWED_EVENTS` runs the gauntlet, in order:

1. **Dedupe (alert_id):** If the NWS `alert_id` is already in
   `/status/alerts_sent.json`, skip. One notification per id, ever (across the
   ledger's 48 h retention window).
2. **Per-cycle cap:** Hard limit of 5 sends per cycle. Excess are deferred
   (NOT written to the ledger) and retry next cycle.
3. **Daily cap (per event_type):** If a *successful send* (outcome=="sent")
   for this `event_type` exists in the ledger within the last
   `DAILY_CAP_SECONDS` (default 86400 = 24 h rolling), suppress. The alert is
   written with `outcome="suppressed_daily_cap"` so it isn't re-evaluated.
4. **Cool-off (global):** If any successful send exists in the ledger within
   the last `COOL_OFF_SECONDS` (default 1800 = 30 min), defer — UNLESS this
   alert's `event_type` is in `COOL_OFF_BYPASS_EVENTS` (default: just
   `Tornado Warning`). Deferred alerts are NOT written to the ledger.
5. **SMTP:** Send. If the SMTP connection fails entirely, the alert is NOT
   written to the ledger and retries next cycle. If at least one recipient
   succeeds, write to ledger (partial failures are NOT retried — that would
   double-send to recipients who already got it).

## Status JSON
Atomically written to `/status/alerting_status.json`.

| field | description |
|---|---|
| `status` | `uninitialized` / `running` / `nws_error` / `smtp_error` |
| `last_poll_time` | UTC ISO |
| `active_warnings` | total count of active alerts in `ALLOWED_EVENTS` this cycle |
| `active_tornado_warnings` | subset count (Tornado Warning only) — kept for backwards-compat |
| `active_by_type` | `{event_type: count}` map for the current cycle |
| `emails_sent_total` | counter since service start |
| `emails_sent_this_cycle` | actually-sent this poll |
| `suppressed_daily_cap_this_cycle` | suppressed by rule 3 this poll |
| `deferred_cool_off_this_cycle` | deferred by rule 4 this poll |
| `last_email_id`, `last_email_time`, `last_email_event_type`, `model_state_last_email` | last sent |
| `cool_off_until` | UTC ISO when cool-off elapses (null if no recent send) |
| `allowed_events`, `cool_off_bypass_events`, `cool_off_seconds`, `daily_cap_seconds` | current config |
| `errors` | ring buffer of last 5 errors (kinds: `nws`, `smtp`, `config`, `cap`, `cool_off`, `loop`) |

`/status/alerts_sent.json` is the ledger — a list of rows. New rows have
`outcome` ∈ {`sent`, `suppressed_daily_cap`}. Pruned at 48 h by `sent_at`.

## Env
| var | default | purpose |
|---|---|---|
| `PORT` | 9009 | Flask port |
| `POLL_INTERVAL` | 300 | seconds between cycles |
| `ALLOWED_EVENTS` | 8 Warnings | comma-separated NWS event types to alert on |
| `DAILY_CAP_SECONDS` | 86400 | per-event-type rolling cap window (24h) |
| `COOL_OFF_SECONDS` | 1800 | global throttle (30 min) |
| `COOL_OFF_BYPASS_EVENTS` | `Tornado Warning` | events that skip cool-off |
| `MAX_SCORE_AGE_SECONDS` | 1800 | beyond this, model state is "unavailable" |
| `KFWS_LAT`, `KFWS_LON` | KFWS coords | point queried at NWS |
| `NWS_UA` | placeholder | required by NWS API |
| `SMTP_HOST` | smtp.gmail.com | |
| `SMTP_PORT` | 587 | |
| `SMTP_USER`, `SMTP_PASS` | unset | required for sending (Gmail: App Password) |
| `ALERT_FROM` | unset | From: address |
| `ALERT_TO` | unset | comma-separated full-body recipients |
| `ALERT_TO_SMS` | unset | comma-separated SMS-gateway recipients (short body) |
| `MODEL_RISK_THRESHOLD` | from `.env` | Tornado Warning annotation threshold |
| `INFERENCE_STATUS_PATH` | `/status/inference_status.json` | input from inference service |
| `ALERTS_SENT_PATH` | `/status/alerts_sent.json` | ledger |
| `STATUS_PATH` | `/status/alerting_status.json` | output |

See `AGENT.md` for the suppression-order invariant, the anti-anthropomorphic
wording rules, and the model-state classification logic.
