# services/alerting — agent notes

**What it is.** NWS-gated email alerter. Every 5 minutes, calls
`https://api.weather.gov/alerts/active?point=KFWS_LAT,KFWS_LON`, keeps
only active Tornado Warnings (event="Tornado Warning", expires > now),
composes an email per unseen alert with the NWS text first and a small
model-readout annotation below, and sends via SMTP. Records sent alert
IDs in `service-status/alerts_sent.json` to dedupe.

**The single load-bearing invariant.** The model never originates an
email. If `filter_to_warnings` returns an empty list, the service sends
nothing — regardless of what the inference status says. The inference
score is purely an annotation on emails the NWS warning triggered.

**Anti-anthropomorphism.** No "agrees" / "disagrees" / "sees" /
"tornadic" in subject or body. Only mechanical phrasing — "elevated",
"not elevated", "unavailable", "score below threshold", "radar
features did not reach cutoff". This is so the user never reads a
model-disagrees email as "probably not serious." Edit `compose_email`
with care.

**Dedup and the 5-per-cycle cap — careful interaction.**
- Dedup key is `alert_id` (the NWS warning UUID). One email per id, ever.
- Cap is 5 emails per cycle. If active TO.Ws > 5, the excess are
  **NOT** written to the ledger. They get logged as `errors.cap` and
  remain eligible next cycle.
- On SMTP failure for one email, that alert is **NOT** added to the
  ledger — next cycle retries it. (Idempotency is the entire point of
  the ledger.)
- Ledger is pruned by `sent_at`, NOT by `expires` — NWS expires can be
  wrong or missing; `sent_at` is the authoritative timestamp for our
  dedup window.

**Model state classification.**
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
  Variables: `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS`
  `ALERT_FROM` `ALERT_TO`.

**CLI.**
- `python alert_service.py` — service loop + Flask /health.
- `python alert_service.py --test-email` — bypass NWS, send one
  fixture-based email to ALERT_TO. The fixture says "TEST FIXTURE"
  prominently in every human-visible field.
- `python alert_service.py --test-email --dry-run` — print email to
  stdout instead of sending. Use for unit-test-style validation.

**Files.**
- `alert_service.py` — main module.
- `Dockerfile`, `requirements.txt`.
- `fixtures/sample_warning.json` — committed test fixture.
- `README.md` — operator-facing summary.
