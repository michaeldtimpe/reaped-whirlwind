#!/usr/bin/env python3
"""
Weather Report Generator — fetches DFW weather from the NWS API every 10
minutes, writes a plain-text report to /logs, and serves an HTML dashboard
via a built-in HTTP server.
"""

import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Dict, Optional

import requests


# ── Configuration ────────────────────────────────────────────────────────────

class Config:
    REGION_NAME = os.getenv("NWS_REGION", "DFW")
    LATITUDE = float(os.getenv("NWS_LAT", "32.8968"))
    LONGITUDE = float(os.getenv("NWS_LON", "-97.0380"))
    NWS_API = "https://api.weather.gov"
    # NWS requires a real contact in the User-Agent — set NWS_UA in .env.
    NWS_UA = os.getenv("NWS_UA", "(reaped-whirlwind weather-reporter, weather@example.com)")
    LOG_DIR = Path("/logs") if Path("/logs").exists() else Path(__file__).parent / "logs"
    DASH_DIR = Path("/var/www/html")
    INTERVAL = int(os.getenv("WEATHER_INTERVAL", "600"))  # seconds


# ── NWS Fetcher ──────────────────────────────────────────────────────────────

class Fetcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.headers = {"User-Agent": cfg.NWS_UA, "Accept": "application/geo+json"}

    def _get(self, url: str) -> Optional[Dict]:
        try:
            r = requests.get(url, headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"API error: {e}")
            return None

    def grid(self) -> Optional[Dict]:
        return self._get(f"{self.cfg.NWS_API}/points/{self.cfg.LATITUDE},{self.cfg.LONGITUDE}")

    def observations(self, stations_url: str) -> Optional[Dict]:
        data = self._get(stations_url)
        if not data or not data.get("features"):
            return None
        sid = data["features"][0]["properties"]["stationIdentifier"]
        return self._get(f"{self.cfg.NWS_API}/stations/{sid}/observations/latest")

    def forecast(self, url: str) -> Optional[Dict]:
        return self._get(url)

    def alerts(self) -> Optional[Dict]:
        return self._get(
            f"{self.cfg.NWS_API}/alerts/active?point={self.cfg.LATITUDE},{self.cfg.LONGITUDE}"
        )


# ── Formatter ────────────────────────────────────────────────────────────────

def _c2f(c: Optional[float]) -> Optional[float]:
    return None if c is None else c * 9 / 5 + 32


def _fmt_temp(c: Optional[float]) -> str:
    if c is None:
        return "N/A"
    return f"{_c2f(c):.1f}°F ({c:.1f}°C)"


def format_current(obs: Dict) -> str:
    if not obs or "properties" not in obs:
        return "No current observation data.\n"
    p = obs["properties"]
    lines = ["=" * 60, "CURRENT WEATHER — DFW METROPLEX", "=" * 60]

    ts = p.get("timestamp")
    if ts:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        lines.append(f"Observation: {dt.strftime('%Y-%m-%d %I:%M %p %Z')}")

    station = p.get("station", "").split("/")[-1]
    lines.append(f"Station: {station}")
    lines.append(f"Temperature: {_fmt_temp(p.get('temperature', {}).get('value'))}")
    lines.append(f"Dewpoint: {_fmt_temp(p.get('dewpoint', {}).get('value'))}")

    rh = p.get("relativeHumidity", {}).get("value")
    if rh is not None:
        lines.append(f"Humidity: {rh:.0f}%")

    ws = p.get("windSpeed", {}).get("value")
    wd = p.get("windDirection", {}).get("value")
    if ws is not None:
        d = f" from {wd}°" if wd else ""
        lines.append(f"Wind: {ws * 0.621371:.1f} mph{d}")

    cond = p.get("textDescription")
    if cond:
        lines.append(f"Conditions: {cond}")

    bp = p.get("barometricPressure", {}).get("value")
    if bp:
        lines.append(f"Pressure: {bp / 3386.39:.2f} inHg")

    vis = p.get("visibility", {}).get("value")
    if vis:
        lines.append(f"Visibility: {vis / 1609.34:.1f} miles")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_forecast(data: Dict) -> str:
    if not data or "properties" not in data:
        return "No forecast data.\n"
    periods = data["properties"].get("periods", [])
    lines = ["\n" + "=" * 60, "FORECAST — DFW METROPLEX", "=" * 60]
    for p in periods[:5]:
        lines.append(f"\n{p.get('name', '?')}:")
        lines.append(f"  Temp: {p.get('temperature')}°{p.get('temperatureUnit', 'F')}")
        lines.append(f"  Wind: {p.get('windSpeed', 'N/A')} {p.get('windDirection', '')}")
        lines.append(f"  {p.get('shortForecast', '')}")
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def format_alerts(data: Dict) -> str:
    if not data:
        return "\nNo active alerts.\n"
    feats = data.get("features", [])
    if not feats:
        return "\nNo active alerts.\n"
    lines = ["\n" + "=" * 60, "ACTIVE ALERTS", "=" * 60]
    for f in feats:
        p = f.get("properties", {})
        lines.append(f"\n🚨 {p.get('event', '?')} ({p.get('severity', '?')})")
        hl = p.get("headline", "")
        if hl:
            lines.append(f"   {hl}")
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ── Dashboard HTML ───────────────────────────────────────────────────────────

_DASH_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>DFW Weather</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'DM Sans',sans-serif;background:#0b0f1a;color:#e2e8f0;
display:flex;justify-content:center;padding:2rem 1rem}}
.c{{max-width:740px;width:100%}}
header{{display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;
padding-bottom:1rem;border-bottom:1px solid #1e293b}}
.logo{{width:42px;height:42px;border-radius:10px;
background:linear-gradient(135deg,#38bdf8,#818cf8);display:flex;
align-items:center;justify-content:center;font-size:1.25rem;flex-shrink:0}}
h1{{font-size:1.3rem;font-weight:700}}
h1 span{{display:block;font-size:.78rem;font-weight:400;color:#64748b;margin-top:2px}}
.bar{{font-size:.8rem;color:#64748b;margin-bottom:1rem;display:flex;align-items:center;gap:.6rem}}
.pulse{{width:8px;height:8px;border-radius:50%;background:#34d399;
box-shadow:0 0 6px rgba(52,211,153,.6);animation:p 2s ease-in-out infinite}}
@keyframes p{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.card{{background:#111827;border:1px solid #1e293b;border-radius:12px;
box-shadow:0 4px 24px rgba(0,0,0,.3);overflow:hidden}}
pre{{font-family:'JetBrains Mono',monospace;font-size:.82rem;line-height:1.65;
padding:1.5rem;white-space:pre-wrap;word-wrap:break-word}}
footer{{margin-top:1.5rem;text-align:center;font-size:.75rem;color:#64748b}}
footer a{{color:#38bdf8;text-decoration:none}}
</style></head><body><div class="c">
<header><div class="logo">⛅</div>
<h1>DFW Weather Report<span>Dallas / Fort Worth</span></h1></header>
<div class="bar"><div class="pulse"></div>
<span>Updated {timestamp} · refreshes every 60 s</span></div>
<div class="card"><pre>{report}</pre></div>
<footer>Data from <a href="https://weather.gov">NWS</a></footer>
</div></body></html>"""


# ── Logger + Dashboard writer ────────────────────────────────────────────────

class Logger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)

    def write(self, text: str):
        now = datetime.now(timezone.utc)
        name = f"{self.cfg.REGION_NAME}_{now.strftime('%Y%m%d_%H%M%S')}.txt"
        (self.cfg.LOG_DIR / name).write_text(text, encoding="utf-8")


class DashWriter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.DASH_DIR.mkdir(parents=True, exist_ok=True)

    def write(self, report: str):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        h = _DASH_TEMPLATE.format(timestamp=ts, report=html.escape(report))
        (self.cfg.DASH_DIR / "index.html").write_text(h, encoding="utf-8")


# ── Status writer (for central dashboard) ───────────────────────────────────

def _write_status(cfg: Config, report: str, ok: bool):
    """Write a small JSON for the central monitoring dashboard."""
    status = {
        "status": "running" if ok else "error",
        "last_report_time": datetime.now(timezone.utc).isoformat(),
        "report_text": report[:4000],  # truncate for safety
        "region": cfg.REGION_NAME,
    }
    p = Path("/status/weather_status.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(status, indent=2))
    except OSError:
        pass


# ── Main generator ───────────────────────────────────────────────────────────

class Generator:
    def __init__(self):
        self.cfg = Config()
        self.fetcher = Fetcher(self.cfg)
        self.logger = Logger(self.cfg)
        try:
            self.dash = DashWriter(self.cfg)
        except Exception:
            self.dash = None

    def _cycle(self):
        parts = [f"Weather Report Generated: "
                 f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"]

        grid = self.fetcher.grid()
        if not grid:
            parts.append("Failed to fetch grid point.\n")
            report = "\n".join(parts)
            self.logger.write(report)
            _write_status(self.cfg, report, False)
            return

        props = grid.get("properties", {})

        stations = props.get("observationStations")
        if stations:
            obs = self.fetcher.observations(stations)
            if obs:
                parts.append(format_current(obs))

        furl = props.get("forecast")
        if furl:
            fc = self.fetcher.forecast(furl)
            if fc:
                parts.append(format_forecast(fc))

        alerts = self.fetcher.alerts()
        if alerts:
            parts.append(format_alerts(alerts))

        parts.append("\nData provided by National Weather Service (weather.gov)\n")
        report = "\n".join(parts)

        print(report)
        self.logger.write(report)
        if self.dash:
            self.dash.write(report)
        _write_status(self.cfg, report, True)

    def run(self):
        print(f"Weather Reporter started — region={self.cfg.REGION_NAME}, interval={self.cfg.INTERVAL}s")
        while True:
            try:
                self._cycle()
            except Exception as e:
                print(f"Cycle error: {e}")
            time.sleep(self.cfg.INTERVAL)


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    gen = Generator()

    # Serve dashboard HTML in background
    class Q(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(gen.cfg.DASH_DIR), **kw)
        def log_message(self, *_):
            pass  # silence access logs

    Thread(target=HTTPServer(("0.0.0.0", 9006), Q).serve_forever, daemon=True).start()
    print("Dashboard on http://0.0.0.0:9006")

    gen.run()


if __name__ == "__main__":
    main()
