# Part C post-deployment retrospective — inference + alerting

**Analysis date:** 2026-09-05 · **Window:** 2026-05-27 → 2026-09-06 UTC (101.2 days of data)
**Point:** 32.5728, −97.3031 (KFWS; the exact point both services query)

Reproduce everything here with `python3 analysis/retrospective.py` (inputs cached in
`analysis/cache/`; `--fetch` re-pulls from IEM and kappa). All kappa access was read-only.

**Verdict in one line:** the alerting path works and is correctly gated, but the model
annotation has never once been exercised in production, nothing about it was recorded, and a
24 h cap will silently swallow the second tornado warning of any outbreak.

---

## 0. What data actually exists

| Location (kappa) | Size | Span | Useful? |
|---|---|---|---|
| `/volume1/docker/service-status/inference_status.json` | 1.2 KB | **current cycle only** | 1 score + last 5 errors |
| `/volume1/docker/service-status/alerting_status.json` | 1.8 KB | **current cycle only** | counters + last 5 errors |
| `/volume1/docker/service-status/alerts_sent.json` | **2 bytes** (`[]`) | ≤48 h | pruned every cycle |
| `/volume1/docker/inference-logs/` | **0 bytes, 0 files** | — | never written |
| `/volume1/docker/alerting-logs/` | **0 bytes, 0 files** | — | never written |
| `/volume1/docker/inference-state/current/` | **2.47 GB**, 45,391 PNGs | 05-27 → 09-06 | filenames = scan timeline |
| `docker logs` (both) | 251,588 lines since 06-07 | — | **100 % Flask `GET /health`** |

**There is no score time series anywhere.** Neither service imports `logging`; every `print`
in `inference_service.py` / `alert_service.py` sits inside a `--once` or `--test-email` CLI
path, so the service loops emit nothing but werkzeug access lines. Score distribution, p95,
stuck-value and threshold-crossing statistics **cannot be computed** and are not estimated
below. What *is* reconstructable is the scan timeline, from the PNG cache filenames
(`fetch_pair()` writes `KFWS_{N0B,N0S}_{YYYYMMDDHHMM}.png` only after a magic-byte-validated
download, so the N0S filename set is a faithful record of what the CNN consumed).

---

## 1. Scoring coverage (proxy: PNG cache filenames)

| Metric | Value |
|---|---|
| Unique N0B scans / N0S scans | 22,721 / 22,670 |
| First → last scan | 2026-05-27 22:11Z → 2026-09-06 01:56Z |
| **5-min ticks with a score fresher than `MAX_SCORE_AGE`=1800 s** | **27,875 / 29,376 = 94.9 %** |
| Time in gaps > 1 h with no scored scan | **106.6 h = 4.4 %** of the window |
| N0B scans with no same-minute N0S | 1,135 (5.0 %) |
| Current score / threshold | 0.4096 @ 2026-09-06T02:19Z / 0.80 |
| Cycle timings (per-cycle, varies) | fetch ~0.8-0.9 s · preprocess ~13 ms · **infer ~7 ms** |

94.9 % is the number that matters — below it `classify_model_state()` returns `unavailable`
and an alert carries no readout.

**Outages (gaps > 1 h):**

| Gap begins after (UTC) | Duration | | Gap begins after (UTC) | Duration |
|---|---|---|---|---|
| 2026-06-04 14:19Z | 2.8 h | | 2026-08-14 21:34Z | **26.3 h** |
| 2026-06-07 09:02Z | **4.1 h** ← during the flood event | | 2026-08-16 17:49Z | 3.0 h |
| 2026-06-11 15:38Z | 1.2 h | | 2026-08-24 15:38Z | 1.9 h |
| 2026-08-02 09:53Z | **57.5 h** | | 2026-08-28 05:59Z | 8.2 h |
| | | | 2026-09-05 18:02Z | 1.5 h |

The 57.5 h (Aug 2–4) and 26.3 h (Aug 14–15) outages are the two large ones. **Cause is
unknown and unknowable** — there are no logs. No allowlisted warning occurred during either,
so nothing was missed, by luck rather than design.

**Fetch failures.** The only retained error class is `no_n0s_within_300s_of_n0b` — all 5 slots
of the `errors[]` ring buffer, all on 2026-09-05 (06:30, 07:05, 09:21, 09:56, 13:46Z), a clear
quiet day. This is the benign case: in clear-air VCP the N0S product is published less often
than N0B, so the pair legitimately fails and the cycle is correctly skipped rather than pairing
a stale velocity scan. Because the buffer holds only 5 entries there is **no way to measure the
real fetch-failure rate or its time-of-day/weather association.**

---

## 2. Ground truth — NWS warnings covering the point

Source: IEM `watchwarn.py` shapefile, WFO FWD, parsed and point-in-polygon filtered in
`analysis/retrospective.py` (`analysis/cache/fwd_vtec.zip`). 1,363 raw rows → **38 distinct
VTEC events** whose geometry contains the point; **16 in `ALLOWED_EVENTS`**, 22 not.

| # | VTEC | ETN | Issued (UTC) | Expires (UTC) | Geom | Event |
|---|---|---|---|---|---|---|
| 1 | SV.W | 204 | 2026-05-28 20:40 | 21:15 | C | Severe Thunderstorm Warning |
| 2 | SV.W | 205 | 2026-05-28 21:12 | 22:00 | C | Severe Thunderstorm Warning |
| 3 | SV.W | 206 | 2026-05-28 21:56 | 22:38 | C | Severe Thunderstorm Warning |
| 4 | SV.W | 211 | 2026-06-02 22:07 | 23:00 | C/P | Severe Thunderstorm Warning |
| 5 | SV.W | 218 | 2026-06-07 00:39 | 01:15 | C | Severe Thunderstorm Warning |
| 6 | FF.W | 46 | 2026-06-07 07:06 | 12:16 | C/P | Flash Flood Warning |
| 7 | FL.W | 25 | 2026-06-07 09:20 | 12:52 | C | Flood Warning |
| 8 | FF.W | 49 | 2026-06-07 12:12 | 15:08 | C | Flash Flood Warning |
| 9–11 | FA.W | 4,5,6 | 2026-06-07 12:14/12:16/12:18 | ~15:15 | C, C/P, C | Flood Warning |
| 12 | SV.W | 237 | 2026-07-07 00:45 | 01:30 | C | Severe Thunderstorm Warning |
| 13 | FF.W | 74 | 2026-07-13 10:43 | 15:15 | C/P | Flash Flood Warning |
| 14 | SV.W | 242 | 2026-07-22 22:39 | 22:52 | C/P | Severe Thunderstorm Warning |
| 15 | SV.W | 245 | 2026-08-26 22:32 | 23:30 | C | Severe Thunderstorm Warning |
| 16 | SV.W | 246 | 2026-08-26 22:58 | 23:38 | C | Severe Thunderstorm Warning |

Excluded and correctly silent: HT.Y ×9, FA.Y ×7, XH.W ×3, SV.A ×2, FA.A ×1 — all advisories or
watches.

**Tornado Warnings covering the point: 0.** WFO FWD issued only 2 TO.W polygons in the entire
window and neither covered KFWS. **Tornado LSRs anywhere in WFO FWD: 0** (of 197 LSRs; 20
within 40 km of KFWS — 6 hail, 6 wind damage, 5 flash flood, 3 wind gust). No tornado occurred.

**County/polygon mismatch — real and documented.** Only 5 of the 16 warnings had a storm-based
polygon over the point (`C/P`); the other 11 matched **county footprint only** (`C`). The NWS
API `?point=` resolves the point to its containing UGC (TXC439 Tarrant / TXZ118) and returns
every alert for that zone, so the service is notified for storms whose polygon never touches
the radar site. This is over-triggering by design of the NWS endpoint, not a bug here — but it
means "a warning covering the point" is a county-sized claim, not a 1 km one.

---

## 3. Alerting audit

**`emails_sent_total = 0` is not evidence of a fault, and not evidence of zero emails ever.**
The counter lives in the in-memory `State` object (`alert_service.py State.__init__`), is
initialised to 0 and never read back from disk; the container started 2026-08-31T21:34:09Z.
The last allowlisted warning at the point expired **2026-08-26 23:38Z** — before that restart.
So 0 is exactly what a correctly working service reports. Likewise `alerts_sent.json = []`
means only "no email in the last 48 h" (`save_ledger()` prunes to `LEDGER_PRUNE_HOURS=48`
every cycle). **No record survives that could confirm any specific past email was sent.**

Replaying the deployed suppression ladder (dedupe → 24 h per-type cap → 30 min global cool-off)
against the ground-truth timeline:

| Outcome | Count | Which |
|---|---|---|
| **sent** | **9 / 16** | SV.W 204, 211, 218, 237, 242, 245 · FF.W 46, 74 · FL.W 25 |
| **suppressed_daily_cap** | **7 / 16** | SV.W 205, 206, 246 · FF.W 49 · FA.W 4, 5, 6 |
| deferred by cool-off | 0 | — |

- **Allowlist correctness: clean.** 0 warnings that should have emailed were dropped by the
  allowlist; 0 emails for non-allowlisted events. The NWS `/alerts` archive cross-check (only
  3 features retained: 1 Heat Advisory, 2 Air Quality Alert, 2026-08-30 → 09-05) agrees — none
  allowlisted, none emailed.
- **Delay is fine.** Issuance → email: n=9, min 0.0 / median 1.0 / max 4.0 min, bounded by the
  300 s poll as designed.
- **The daily cap is doing most of the work, and it is blunt.** 7 of 16 suppressed. On
  2026-06-07 the cap collapsed six flood products into two emails — partly desirable, but note
  FL.W and FA.W both surface as the NWS event string `"Flood Warning"` and therefore share one
  cap bucket.
- **Model readout would have been unavailable for 4 of 16** (FF.W 49, FA.W 4/5/6) — all inside
  the 4.1 h outage beginning 2026-06-07 09:02Z. The inference service was down during the most
  active severe episode of the window.

### P0 defect: Tornado Warning is subject to the 24 h daily cap

`run_cycle()` (deployed `alert_service.py:471`, md5 `0abc95b5…`, verified identical inside the
container) applies `has_recent_send_of_type(..., DAILY_CAP_SECONDS)` to **every** event type
*before* the cool-off check at `:490`. `COOL_OFF_BYPASS_EVENTS` exempts Tornado Warning from
**cool-off only** — the code comment at `:91` says so explicitly ("still respect daily cap"),
so this is a deliberate design choice, not an oversight. It should still be revisited.
Consequence: the 2nd and every later Tornado Warning within 24 h of the first is written to the
ledger as `suppressed_daily_cap` and **never sent**. A DFW outbreak routinely
produces several sequential tornado warnings for one point. This did not bite in this window
solely because there were zero tornado warnings — it is unexercised, not proven safe.

---

## 4. Model vs. reality

| | |
|---|---|
| Tornado Warnings covering the point | **0** |
| Tornado LSRs in WFO FWD | **0** |
| Scores persisted for any past moment | **0** |

**The model annotation has never been exercised in production.** The only path that renders a
numeric score into a message is the Tornado Warning branch of `compose_email` / `compose_sms`;
with zero tornado warnings at the point it has run zero times outside `--test-email`. The nine
emails the replay says were sent all carried *"model readout N/A — CNN assesses tornado risk
only."*

Cannot be concluded (needs the missing score series): score distribution or p95; false-alarm
behaviour on hot quiet days vs SVR days; whether scores are stuck, drifting or degenerate;
scores inside warning windows vs quiet periods.

Can be concluded: the service is alive and self-consistent (last scan 2.7 min before last
score; `preprocess_version` 1.0 and `model_sha256 c0500889…` match the manifest, so the
startup fingerprint chain and the no-train/serve-skew guarantee are intact); the one
observable score, 0.4096 on a quiet night, is comfortably below the 0.80 threshold, which is
at least not obviously broken. The 9 Severe Thunderstorm Warnings over the point were exactly
the `warning_no_tornado` hard-negative class the model card reports 5 % FP on — the single
most informative scores to have had, and none were recorded.

**Is the annotation earning its keep? Not yet — but it is nearly free** (one container, 7 ms
per cycle) **and structurally harmless** (the NWS gate means it can never originate an alert).
Keep it; instrument it. The honest position is that after 101 days it has produced no evidence
either way, and that is a recording failure, not a model failure.

---

## 5. Recommendations (ranked)

| # | Pri | Change |
|---|---|---|
| 1 | **P0** | **Exempt Tornado Warning from the daily cap.** Add `DAILY_CAP_BYPASS_EVENTS` (default `Tornado Warning`) and skip `has_recent_send_of_type()` for it, mirroring `COOL_OFF_BYPASS_EVENTS`. Today warning #2 of an outbreak is silently dropped. |
| 2 | **P0** | **Persist a score time series.** One JSON line per cycle to `/logs/scores-YYYY-MM.jsonl` — the `inference-logs` bind mount already exists and is empty: `{ts, scan_time, score, scan_delta, status, error}`. ≈105 KB/month at 5-min cadence; retain 24 months. Without it every question in §1 and §4 stays unanswerable next time. |
| 3 | **P1** | **Persist alerting decisions.** Keep `alerts_sent.json` as the 48 h dedupe set, but append every outcome (`sent` / `suppressed_daily_cap` / `deferred_cool_off`) to `/logs/alerts-YYYY.jsonl`, and derive `emails_sent_total` from it so it survives restarts. |
| 4 | P1 | **Bound the PNG cache.** `inference-state/current` is 2.47 GB / 45,391 files after 101 days, growing ~24 MB/day with nothing pruning it; it passes 10 GB within a year. Unlink files older than ~24 h at end of cycle, or use a tmpfs. |
| 5 | P2 | **Log operationally at all.** 251,588 log lines since June, none about weather. Add a per-cycle INFO line; filter the werkzeug `/health` access noise so `docker logs` becomes an audit trail. |
| 6 | P2 | **Retry NWS inside the cycle.** All 5 retained alerting errors are `api.weather.gov` 502 / ReadTimeout (most recent 2026-09-06T02:00:49Z). A failed poll is skipped for a full 300 s. Add 2 retries with backoff and surface consecutive-failure count in `/health`. |
| 7 | P2 | **Reconsider the global cool-off for warnings.** It delayed nothing here, but it is cross-type: a Flash Flood Warning can push a Severe Thunderstorm Warning back 30 min. Sensible for advisories, questionable for warnings. |
| 8 | P3 | **Don't imply the score is calibrated.** Until the series exists, drop the numeric score from the SMS body — `ELEVATED` / `NOT ELEV` is honest; `0.83 vs 0.80` implies a precision nothing has validated. |

Not recommended: changing the 5-min poll interval (median delay 1.0 min is fine), or dropping
the annotation (it is free and gated).


---

## Addendum (2026-09-06): all eight recommendations shipped — and the model is invalid

**Recommendations.** #1-#4 were implemented in the session that wrote this document; #5-#8
followed on 2026-09-06 (`common/oplog.py` per-cycle INFO lines with `/health` access noise
filtered; NWS retries with doubling backoff and `nws_consecutive_failures` / `nws_last_attempts`
in `/health`; every default Warning now bypasses the cross-type cool-off; the SMS body carries
the state word only). All deployed to kappa the same day.

**The bigger finding.** §4 said the annotation had "produced no evidence either way". The first
thing done with the replay tool that section called for (`services/inference/replay_smoke.py`)
was to score real KFWS archive scans from six SPC-confirmed tornadoes within 45 km of the radar,
through the service's own code path. Tornado scans scored 0.11-0.30; quiet sky scored 0.46-0.72;
an all-zero tensor scored 0.65. The same tornado days scored 0.59-0.74 when sampled six hours
early — which is exactly what the training collector had done: SPC times are CST and were used
as UTC. The model learned the artifact, the held-out eval measured the artifact, and the 5 %
`warning_no_tornado` FP rate this document called "the single most informative number" was the
artifact's clearest fingerprint (those negatives were the only class sampled at the right time).

**Lesson for the retrospective itself.** §4 argued the annotation was "structurally harmless"
and should be kept and instrumented. Harmless to the *alert path*, yes; but a model that says
NOT ELEVATED during a real tornado warning is not harmless to the reader. The annotation is now
withdrawn (`MODEL_ANNOTATION=off`) until a retrained model passes the replay, and the replay is
part of the gate. The general point: an offline eval on the same collection pipeline cannot
catch a pipeline bug — only an end-to-end check against independently-timed ground truth can.
