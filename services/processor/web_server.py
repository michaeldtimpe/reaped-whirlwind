#!/usr/bin/env python3
"""
Web Server for Radar Image Processor
Provides monitoring dashboard and API endpoints.
"""

from flask import Flask, render_template_string, jsonify
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psutil
import os


# Central Time handling (no pytz needed)
class CentralTime:
    """Simple Central Time converter without external dependencies."""
    @staticmethod
    def from_utc(dt_utc):
        """Convert a UTC datetime to Central Time (auto CST/CDT)."""
        # CDT: second Sunday of March to first Sunday of November
        year = dt_utc.year
        # Second Sunday of March
        mar1 = datetime(year, 3, 1)
        cdt_start = datetime(year, 3, 8 + (6 - mar1.weekday()) % 7, 2, 7, 0, 0, tzinfo=timezone.utc)
        # First Sunday of November
        nov1 = datetime(year, 11, 1)
        cdt_end = datetime(year, 11, 1 + (6 - nov1.weekday()) % 7, 2, 6, 0, 0, tzinfo=timezone.utc)

        if cdt_start <= dt_utc.replace(tzinfo=timezone.utc) < cdt_end:
            offset = timedelta(hours=-5)
            abbr = "CDT"
        else:
            offset = timedelta(hours=-6)
            abbr = "CST"
        return dt_utc + offset, abbr


# HTML Template for dashboard
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Radar Image Processor</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        /* ─── Monokai Dark Palette ─── */
        :root {
            --bg-base:       #272822;
            --bg-card:       #3e3836;
            --bg-card-hover: #4a4640;
            --bg-log:        #1a1a18;
            --border:        #49483e;

            --text-primary:  #f8f8f2;
            --text-secondary:#a59f85;
            --text-muted:    #75715e;

            --green:         #a6e22e;
            --green-dim:     #6b8e1b;
            --pink:          #f92672;
            --orange:        #fd971f;
            --yellow:        #e6db74;
            --blue:          #66d9ef;
            --purple:        #ae81ff;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: var(--bg-base);
            color: var(--text-primary);
            padding: 24px;
            min-height: 100vh;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        /* ─── Header ─── */
        header {
            background: var(--bg-card);
            padding: 20px 24px;
            border-radius: 10px;
            margin-bottom: 22px;
            border: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        h1 {
            color: var(--text-primary);
            font-size: 22px;
            font-weight: 600;
            letter-spacing: 0.3px;
        }

        .status-badge {
            display: inline-block;
            padding: 7px 20px;
            border-radius: 20px;
            font-size: 16px;
            font-weight: 600;
            letter-spacing: 0.5px;
        }

        .status-idle        { background: rgba(102,217,239,0.15); color: var(--blue); border: 1px solid rgba(102,217,239,0.3); }
        .status-processing  { background: rgba(253,151,31,0.15);  color: var(--orange); border: 1px solid rgba(253,151,31,0.3); }
        .status-complete    { background: rgba(166,226,46,0.15);  color: var(--green); border: 1px solid rgba(166,226,46,0.3); }
        .status-error       { background: rgba(249,38,114,0.15);  color: var(--pink); border: 1px solid rgba(249,38,114,0.3); }

        /* ─── Metrics Grid ─── */
        .metrics-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 2fr;
            gap: 18px;
            margin-bottom: 22px;
        }

        .metric-card {
            background: var(--bg-card);
            padding: 20px;
            border-radius: 10px;
            border: 1px solid var(--border);
            transition: border-color 0.2s;
        }

        .metric-card:hover {
            border-color: var(--text-muted);
        }

        .metric-card h3 {
            color: var(--text-muted);
            font-size: 11px;
            font-weight: 600;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .metric-value {
            font-size: 32px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .metric-label {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* Last Processed card: filename in monospace, wraps naturally */
        .last-processed-value {
            font-size: 16px;
            font-weight: 600;
            color: var(--green);
            font-family: 'Courier New', monospace;
            word-break: break-all;
            line-height: 1.4;
        }

        .last-processed-time {
            font-size: 13px;
            color: var(--text-secondary);
            margin-top: 6px;
            font-family: 'Courier New', monospace;
        }

        /* ─── Folder Stats ─── */
        .folder-stats {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 18px;
            margin-bottom: 22px;
        }

        .stat-card {
            background: var(--bg-card);
            padding: 20px;
            border-radius: 10px;
            border: 1px solid var(--border);
        }

        .stat-card h3 {
            color: var(--text-primary);
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 14px;
        }

        .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 9px 0;
            border-bottom: 1px solid var(--border);
        }

        .stat-row:last-child {
            border-bottom: none;
        }

        .stat-label {
            color: var(--text-muted);
        }

        .stat-value {
            font-weight: 600;
            color: var(--text-secondary);
        }

        /* ─── Log Section ─── */
        .log-section {
            background: var(--bg-card);
            padding: 20px;
            border-radius: 10px;
            border: 1px solid var(--border);
        }

        .log-section h2 {
            color: var(--text-primary);
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 12px;
        }

        .log-container {
            background: var(--bg-log);
            padding: 14px;
            border-radius: 6px;
            max-height: 380px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 13px;
            border: 1px solid var(--border);
        }

        .log-container::-webkit-scrollbar {
            width: 6px;
        }
        .log-container::-webkit-scrollbar-track {
            background: var(--bg-log);
        }
        .log-container::-webkit-scrollbar-thumb {
            background: var(--border);
            border-radius: 3px;
        }

        .log-entry {
            margin-bottom: 6px;
            padding: 4px 0 4px 10px;
            border-left: 3px solid var(--border);
        }

        .log-INFO    { border-left-color: var(--blue);   color: var(--text-secondary); }
        .log-SUCCESS { border-left-color: var(--green);  color: var(--green); }
        .log-WARNING { border-left-color: var(--orange); color: var(--orange); }
        .log-ERROR   { border-left-color: var(--pink);   color: var(--pink); }

        .log-timestamp {
            color: var(--text-muted);
            margin-right: 10px;
        }

        .auto-refresh {
            text-align: right;
            color: var(--text-muted);
            font-size: 11px;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="container">

        <!-- Header with title left, status badge right -->
        <header>
            <h1>🛰️ Radar Image Processor</h1>
            <span id="status-badge" class="status-badge">Loading...</span>
        </header>

        <!-- Metrics: Total Processed | Errors | Last Processed (wide) -->
        <div class="metrics-grid">
            <div class="metric-card">
                <h3>Total Processed</h3>
                <div class="metric-value" id="total-processed">-</div>
                <div class="metric-label">Images converted</div>
            </div>
            <div class="metric-card">
                <h3>Errors</h3>
                <div class="metric-value" id="total-errors">-</div>
                <div class="metric-label">Processing failures</div>
            </div>
            <div class="metric-card">
                <h3>Last Processed</h3>
                <div class="last-processed-value" id="last-processed">—</div>
                <div class="last-processed-time" id="last-processed-time">—</div>
            </div>
        </div>

        <!-- Folder Stats -->
        <div class="folder-stats">
            <div class="stat-card">
                <h3>📥 Input Folder (Raw Images)</h3>
                <div class="stat-row">
                    <span class="stat-label">Files</span>
                    <span class="stat-value" id="input-count">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Size</span>
                    <span class="stat-value" id="input-size">-</span>
                </div>
            </div>
            <div class="stat-card">
                <h3>📤 Output Folder (Processed JSON)</h3>
                <div class="stat-row">
                    <span class="stat-label">Files</span>
                    <span class="stat-value" id="output-count">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Size</span>
                    <span class="stat-value" id="output-size">-</span>
                </div>
            </div>
        </div>

        <!-- Processing Log -->
        <div class="log-section">
            <h2>Processing Log</h2>
            <div class="log-container" id="log-container">
                <div class="log-entry log-INFO">
                    <span class="log-timestamp">Loading...</span>
                    <span>Initializing dashboard...</span>
                </div>
            </div>
            <div class="auto-refresh">Auto-refreshing every 5 seconds</div>
        </div>
    </div>

    <script>
        /**
         * Parse a UTC timestamp string and convert to Central Time.
         * Handles "YYYY-MM-DD HH:MM:SS UTC" format from the log entries.
         */
        function utcToCentral(utcStr) {
            if (!utcStr) return '—';

            // Parse the UTC string
            const clean = utcStr.replace(' UTC', '').trim();
            const dt = new Date(clean + 'Z'); // append Z so JS treats as UTC
            if (isNaN(dt.getTime())) return utcStr; // fallback if unparseable

            // Determine CDT vs CST using US rules
            const year = dt.getUTCFullYear();

            // Second Sunday of March at 2:00 AM local (08:00 UTC)
            const mar1 = new Date(Date.UTC(year, 2, 1));
            const marOffset = (7 - mar1.getUTCDay()) % 7; // days until first Sunday
            const cdtStart = new Date(Date.UTC(year, 2, 8 + marOffset, 8, 0, 0));

            // First Sunday of November at 2:00 AM local (07:00 UTC)
            const nov1 = new Date(Date.UTC(year, 10, 1));
            const novOffset = (7 - nov1.getUTCDay()) % 7;
            const cdtEnd = new Date(Date.UTC(year, 10, 1 + novOffset, 7, 0, 0));

            const isDCT = (dt >= cdtStart && dt < cdtEnd);
            const offsetHrs = isDCT ? -5 : -6;
            const abbr = isDCT ? 'CDT' : 'CST';

            const local = new Date(dt.getTime() + offsetHrs * 3600000);

            const pad = n => String(n).padStart(2, '0');
            return `${local.getUTCFullYear()}-${pad(local.getUTCMonth()+1)}-${pad(local.getUTCDate())} ` +
                   `${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}:${pad(local.getUTCSeconds())} ${abbr}`;
        }

        /**
         * Calculate minutes elapsed since a UTC timestamp string.
         */
        function minutesSince(utcStr) {
            if (!utcStr) return null;
            const clean = utcStr.replace(' UTC', '').trim();
            const dt = new Date(clean + 'Z');
            if (isNaN(dt.getTime())) return null;
            return Math.floor((Date.now() - dt.getTime()) / 60000);
        }

        function updateDashboard() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    // Status badge
                    const statusBadge = document.getElementById('status-badge');
                    const status = data.current_status;

                    // Check staleness — if no processing in 20+ minutes, show warning
                    const minsAgo = minutesSince(data.last_processed_time);
                    let displayStatus = status;
                    if (minsAgo !== null && minsAgo > 20 && status === 'Idle') {
                        displayStatus = 'Stale';
                        statusBadge.textContent = 'Stale (' + minsAgo + 'm ago)';
                        statusBadge.className = 'status-badge status-error';
                    } else {
                        statusBadge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
                        statusBadge.className = 'status-badge status-' + status.toLowerCase();
                    }

                    // Metrics
                    document.getElementById('total-processed').textContent = data.total_processed;
                    document.getElementById('total-errors').textContent = data.total_errors;
                    document.getElementById('last-processed').textContent = data.last_processed || '—';

                    // Last run time — use the proper timestamp field from the API
                    const lastTime = data.last_processed_time;
                    if (lastTime) {
                        const centralTime = utcToCentral(lastTime);
                        const ago = minsAgo !== null ? ' (' + minsAgo + 'm ago)' : '';
                        document.getElementById('last-processed-time').textContent =
                            'Last run: ' + centralTime + ago;
                    } else {
                        document.getElementById('last-processed-time').textContent = '—';
                    }

                    // Folder stats
                    document.getElementById('input-count').textContent  = data.folders.input.count;
                    document.getElementById('input-size').textContent   = data.folders.input.size_mb + ' MB';
                    document.getElementById('output-count').textContent = data.folders.output.count;
                    document.getElementById('output-size').textContent  = data.folders.output.size_mb + ' MB';

                    // Logs
                    const logContainer = document.getElementById('log-container');
                    logContainer.innerHTML = '';

                    data.logs.slice().reverse().forEach(log => {
                        const logEntry = document.createElement('div');
                        logEntry.className = 'log-entry log-' + log.level;
                        logEntry.innerHTML =
                            '<span class="log-timestamp">' + utcToCentral(log.timestamp) + '</span>' +
                            '<span>' + log.message + '</span>';
                        logContainer.appendChild(logEntry);
                    });
                })
                .catch(error => {
                    console.error('Error fetching status:', error);
                });
        }

        updateDashboard();
        setInterval(updateDashboard, 5000);
    </script>
</body>
</html>
"""


def start_web_server(processor, host='0.0.0.0', port=8080):
    """Start the Flask web server."""
    app = Flask(__name__)

    @app.route('/')
    def dashboard():
        """Render the main dashboard."""
        return render_template_string(DASHBOARD_HTML)

    @app.route('/api/status')
    def get_status():
        """Get current processor status."""
        status = processor.status.get_status()

        # Add folder stats
        status['folders'] = {
            'input':  processor.get_folder_stats(processor.config['paths']['input_dir']),
            'output': processor.get_folder_stats(processor.config['paths']['output_dir'])
        }

        # Add system stats
        status['system'] = {
            'memory_percent': round(psutil.virtual_memory().percent, 1),
            'cpu_percent':    round(psutil.cpu_percent(interval=0.1), 1)
        }

        return jsonify(status)

    @app.route('/health')
    def health():
        """Health check endpoint. Returns unhealthy if no processing in 20+ minutes."""
        status = processor.status.get_status()
        last_time_str = status.get('last_processed_time')

        healthy = True
        reason = 'ok'

        if last_time_str:
            try:
                last_time = datetime.strptime(last_time_str.replace(' UTC', ''), '%Y-%m-%d %H:%M:%S')
                minutes_ago = (datetime.utcnow() - last_time).total_seconds() / 60
                if minutes_ago > 20:
                    healthy = False
                    reason = f'no processing for {int(minutes_ago)} minutes'
            except Exception:
                pass
        else:
            # No processing yet (just started) — give it grace period
            pass

        result = {
            'status': 'healthy' if healthy else 'unhealthy',
            'reason': reason,
            'last_processed': status.get('last_processed'),
            'last_processed_time': last_time_str,
            'total_processed': status.get('total_processed', 0),
            'total_errors': status.get('total_errors', 0)
        }

        return jsonify(result), 200 if healthy else 503

    # Run server
    app.run(host=host, port=port, debug=False, threaded=True)
