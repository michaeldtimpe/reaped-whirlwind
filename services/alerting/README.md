# alerting

NWS-gated email alerter for **reaped-whirlwind**. Sends one email per
unseen NWS tornado warning for the KFWS point, with the experimental
model's score appended as a numeric annotation. The model never
originates an alert.

## How to run locally
```
docker-compose -p reaped-whirlwind up --build alerting
curl http://localhost:9009/health | jq
```

## Validate the email pipeline without a real warning
```
# Print what would be sent — no SMTP traffic
docker-compose run --rm alerting python /app/alert_service.py --test-email --dry-run

# Actually send to ALERT_TO (requires SMTP_*/ALERT_FROM/ALERT_TO env)
docker-compose run --rm alerting python /app/alert_service.py --test-email
```

The fixture's headline, description, and instruction all say "TEST
FIXTURE — do not act" so a test email is unmistakable.

## Status JSON
Atomically written to `/status/alerting_status.json`.

| field | description |
|---|---|
| `status` | `uninitialized` / `running` / `nws_error` / `smtp_error` |
| `last_poll_time` | UTC ISO |
| `active_tornado_warnings` | count from this cycle |
| `emails_sent_total` | counter since service start |
| `emails_sent_this_cycle` | sent in the most recent poll |
| `last_email_id`, `last_email_time`, `model_state_last_email` | last sent |
| `errors` | ring buffer of last 5 errors |

`/status/alerts_sent.json` is the dedup ledger — a list of
`{alert_id, sent_at, model_state, score, threshold}` rows, pruned at
24 h by `sent_at`.

## Env
| var | default | purpose |
|---|---|---|
| `PORT` | 9009 | Flask port |
| `POLL_INTERVAL` | 300 | seconds between cycles |
| `MAX_SCORE_AGE_SECONDS` | 1800 | beyond this, model state is "unavailable" |
| `KFWS_LAT`, `KFWS_LON` | KFWS coords | point queried at NWS |
| `NWS_UA` | placeholder | required by NWS API |
| `SMTP_HOST` | smtp.gmail.com | |
| `SMTP_PORT` | 587 | |
| `SMTP_USER`, `SMTP_PASS` | unset | required for sending (Gmail: App Password) |
| `ALERT_FROM` | unset | From: address |
| `ALERT_TO` | unset | comma-separated full-body recipients |
| `ALERT_TO_SMS` | unset | comma-separated SMS-gateway recipients (short body) |
| `MODEL_RISK_THRESHOLD` | from `.env` | annotation threshold |
| `INFERENCE_STATUS_PATH` | `/status/inference_status.json` | input from inference service |
| `ALERTS_SENT_PATH` | `/status/alerts_sent.json` | dedup ledger |
| `STATUS_PATH` | `/status/alerting_status.json` | output |

See `AGENT.md` for the dedup/cap interaction, the anti-anthropomorphic
wording rules, and the model-state classification logic.
