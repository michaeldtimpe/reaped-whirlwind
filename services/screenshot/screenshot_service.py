#!/usr/bin/env python3
"""
Screenshot Service — Captures weather radar screenshots on a timed interval.

Visits configured URLs via headless Chromium (Playwright), saves timestamped
PNG screenshots to an output directory.  Designed to run as a long-lived
Docker container on a Synology NAS.
"""

import sys
import yaml
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Shared helpers are bind-mounted at /srv/reaped/common in the container (see
# docker-compose.yml); outside it they resolve relative to this file.
# `common` is a package, so its PARENT goes on the path.
sys.path.insert(0, "/srv/reaped" if Path("/srv/reaped/common").is_dir()
                else str(Path(__file__).resolve().parents[2]))
from common.status import atomic_write_json


# ── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging(level_name: str = "INFO") -> logging.Logger:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("ScreenshotService")


# ── Service ──────────────────────────────────────────────────────────────────

class ScreenshotService:
    """Continuously captures screenshots from configured URLs."""

    STATUS_FILE = "/status/screenshot_status.json"

    def __init__(self, config_path: str = "/app/config.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        self.log = _setup_logging(self.config.get("logging", {}).get("level", "INFO"))
        self.log.info("Initialized — %d URL(s) configured", len(self.config["urls"]))

        # Running stats (also written to STATUS_FILE for the dashboard)
        self._stats = {
            "total_captured": 0,
            "total_errors": 0,
            "last_capture_file": None,
            "last_capture_time": None,
            "status": "starting",
        }

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")

    def _write_status(self):
        """Persist lightweight status JSON so the dashboard can read it.
        Atomic (tmp + os.replace) so the dashboard never reads a partial file."""
        try:
            atomic_write_json(Path(self.STATUS_FILE), self._stats)
        except OSError as exc:
            # non-fatal — dashboard will just see stale data
            self.log.debug("status write failed: %s", exc)

    # ── capture ───────────────────────────────────────────────────────────

    def _capture(self, url: str, dest: Path, cfg: dict) -> bool:
        """Launch a headless browser, navigate to *url*, and save a screenshot."""
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={
                    "width": cfg["viewport_width"],
                    "height": cfg["viewport_height"],
                })
                page = ctx.new_page()
                page.goto(url, timeout=cfg["timeout"] * 1000, wait_until="networkidle")
                page.wait_for_timeout(cfg["wait_time"] * 1000)

                dest.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(dest), full_page=False)
                browser.close()

            size = dest.stat().st_size
            self.log.info("  saved %s (%s bytes)", dest.name, f"{size:,}")
            return True

        except PlaywrightTimeoutError:
            self.log.error("  timeout loading %s", url)
        except Exception as exc:
            self.log.error("  error: %s: %s", type(exc).__name__, exc)
        return False

    # ── main loop ─────────────────────────────────────────────────────────

    def _cycle(self):
        output_dir = Path(self.config["output_dir"])
        cfg = self.config["screenshot"]

        for entry in self.config["urls"]:
            prefix = entry["prefix"]
            filename = f"{prefix}_{self._timestamp()}.png"
            dest = output_dir / filename
            self.log.info("Capturing: %s", prefix)

            if self._capture(entry["url"], dest, cfg):
                self._stats["total_captured"] += 1
                self._stats["last_capture_file"] = filename
                self._stats["last_capture_time"] = datetime.now(timezone.utc).isoformat()
            else:
                self._stats["total_errors"] += 1

    def run(self):
        interval = self.config["interval_seconds"]
        self.log.info("Starting capture loop — interval %ds", interval)
        self._stats["status"] = "running"
        self._write_status()

        while True:
            try:
                t0 = time.time()
                self._cycle()
                elapsed = time.time() - t0
                self.log.info("Cycle done in %.1fs", elapsed)

                self._stats["status"] = "running"
                self._write_status()

                sleep = max(0, interval - elapsed)
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    self.log.warning("Cycle exceeded interval (%.1fs > %ds)", elapsed, interval)

            except KeyboardInterrupt:
                self.log.info("Shutdown requested")
                self._stats["status"] = "stopped"
                self._write_status()
                break
            except Exception as exc:
                self.log.error("Unexpected error: %s", exc)
                self._stats["status"] = "error"
                self._write_status()
                time.sleep(interval)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ScreenshotService().run()
