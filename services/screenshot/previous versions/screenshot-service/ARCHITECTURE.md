# Screenshot Service — Architecture

## Overview

Single-process Python service that runs an infinite capture loop.

```
┌──────────────────────────────────────────────┐
│            Screenshot Service                │
│                                              │
│  ┌──────────┐    ┌───────────┐    ┌───────┐  │
│  │  Config   │───▶│  Capture  │───▶│  PNG  │  │
│  │  (YAML)  │    │   Loop    │    │ files │  │
│  └──────────┘    └─────┬─────┘    └───────┘  │
│                        │                     │
│                   ┌────▼────┐                │
│                   │ Status  │                │
│                   │  JSON   │                │
│                   └─────────┘                │
└──────────────────────────────────────────────┘
```

## Capture cycle

1. For each URL in `config.yaml`:
   a. Launch headless Chromium via Playwright.
   b. Navigate to URL, wait for `networkidle` + extra render delay.
   c. Save viewport screenshot to `/screenshots/{prefix}_{timestamp}.png`.
   d. Close the browser.
2. Write `/status/screenshot_status.json` with counters and last-capture info.
3. Sleep until the next interval.

## Status file schema

```json
{
  "total_captured": 42,
  "total_errors": 1,
  "last_capture_file": "radar_base_reflectivity_20260218_120000_UTC.png",
  "last_capture_time": "2026-02-18T12:00:05+00:00",
  "status": "running"
}
```

## Error handling

- Playwright timeout → logged, cycle continues to next URL.
- Unexpected exception → logged, stats updated, service sleeps and retries.
- Status file write failure → non-fatal (dashboard sees stale data).

## Dependencies

- **Playwright** (bundled in base Docker image) — headless Chromium.
- **PyYAML** — config parsing.
- Python stdlib (`json`, `time`, `logging`, `pathlib`, `datetime`).
