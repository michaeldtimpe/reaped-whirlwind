#!/usr/bin/env python3
"""
Batch Radar Image Processor
Processes tornado dataset images in weekly subdirectories.
Outputs JSON files alongside source PNGs, then deletes originals.
"""

import os
import sys
import time
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, '/app')
from radar_tools import RadarImageConverter


def process_single_image(image_path_str: str, scale_paths: dict, sample_rate: int) -> tuple:
    """Process one image. Returns (success, filename, rel_path, time, error)."""
    image_path = Path(image_path_str)
    filename = image_path.name

    try:
        converter = RadarImageConverter(
            scale_paths['reflectivity'],
            scale_paths['velocity']
        )

        if 'reflectivity' in filename.lower():
            radar_type = 'reflectivity'
        elif 'velocity' in filename.lower():
            radar_type = 'velocity'
        else:
            return (False, filename, str(image_path.parent), 0, "Unknown radar type")

        # Output JSON in same directory as input
        output_path = image_path.with_suffix('.json')

        start = time.time()
        converter.convert_and_save(
            str(image_path),
            radar_type,
            str(output_path),
            sample_rate=sample_rate,
            save_numpy=False
        )
        elapsed = time.time() - start

        # Verify output
        if not output_path.exists() or output_path.stat().st_size < 1000:
            return (False, filename, str(image_path.parent), elapsed, "Output missing or too small")

        with open(output_path, 'r') as f:
            data = json.load(f)
        if 'metadata' not in data or 'data' not in data:
            return (False, filename, str(image_path.parent), elapsed, "Invalid JSON structure")

        # Delete source
        image_path.unlink()

        return (True, filename, str(image_path.parent), elapsed, None)

    except Exception as e:
        return (False, filename, str(image_path.parent), 0, str(e))


class BatchStatus:
    """Thread-safe batch progress tracker with web API."""

    def __init__(self, total_files):
        self._lock = threading.Lock()
        self.total = total_files
        self.processed = 0
        self.errors = 0
        self.current_folder = ""
        self.last_file = ""
        self.start_time = time.time()
        self.logs = []
        self.done = False

    def record(self, success, filename, folder, error=None):
        with self._lock:
            if success:
                self.processed += 1
            else:
                self.errors += 1
            self.last_file = filename
            self.current_folder = folder
            entry = {
                'time': datetime.utcnow().strftime('%H:%M:%S'),
                'file': filename,
                'status': 'OK' if success else f'ERR: {error}'
            }
            self.logs.append(entry)
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]

    def get_status(self):
        with self._lock:
            elapsed = time.time() - self.start_time
            rate = self.processed / elapsed if elapsed > 0 else 0
            remaining = (self.total - self.processed - self.errors) / rate if rate > 0 else 0
            pct = ((self.processed + self.errors) / self.total * 100) if self.total > 0 else 0
            return {
                'total': self.total,
                'processed': self.processed,
                'errors': self.errors,
                'percent': round(pct, 1),
                'elapsed_min': round(elapsed / 60, 1),
                'rate_per_min': round(rate * 60, 1),
                'eta_min': round(remaining / 60, 1),
                'current_folder': self.current_folder,
                'last_file': self.last_file,
                'done': self.done,
                'logs': list(self.logs[-50:])
            }


def start_web(status, port=8080):
    """Minimal status web server."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json as jsonlib

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/status':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(jsonlib.dumps(status.get_status(), indent=2).encode())
            elif self.path == '/health':
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'ok')
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(DASHBOARD_HTML.encode())

        def log_message(self, format, *args):
            pass  # Suppress request logs

    server = HTTPServer(('0.0.0.0', port), Handler)
    server.serve_forever()


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Tornado Batch Processor</title>
<meta http-equiv="refresh" content="5">
<style>
body { background: #1e1e1e; color: #d4d4d4; font-family: 'Consolas', monospace; margin: 20px; }
h1 { color: #569cd6; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
.stat { background: #2d2d2d; padding: 15px; border-radius: 8px; border-left: 4px solid #569cd6; }
.stat .label { color: #808080; font-size: 12px; text-transform: uppercase; }
.stat .value { font-size: 28px; font-weight: bold; color: #4ec9b0; }
.progress-bar { background: #333; border-radius: 10px; height: 30px; margin: 20px 0; overflow: hidden; }
.progress-fill { background: linear-gradient(90deg, #569cd6, #4ec9b0); height: 100%; transition: width 0.5s; display: flex; align-items: center; justify-content: center; font-weight: bold; }
.logs { background: #1a1a1a; padding: 15px; border-radius: 8px; max-height: 400px; overflow-y: auto; font-size: 13px; }
.log-ok { color: #4ec9b0; }
.log-err { color: #f44747; }
.done { color: #4ec9b0; font-size: 24px; font-weight: bold; }
</style>
</head>
<body>
<h1>Tornado Dataset Batch Processor</h1>
<div id="content">Loading...</div>
<script>
async function update() {
    try {
        const r = await fetch('/api/status');
        const s = await r.json();
        let html = '';
        if (s.done) {
            html += '<p class="done">BATCH COMPLETE</p>';
        }
        html += '<div class="stats">';
        html += `<div class="stat"><div class="label">Processed</div><div class="value">${s.processed} / ${s.total}</div></div>`;
        html += `<div class="stat"><div class="label">Errors</div><div class="value" style="color:${s.errors > 0 ? '#f44747' : '#4ec9b0'}">${s.errors}</div></div>`;
        html += `<div class="stat"><div class="label">Rate</div><div class="value">${s.rate_per_min}/min</div></div>`;
        html += `<div class="stat"><div class="label">ETA</div><div class="value">${s.eta_min} min</div></div>`;
        html += `<div class="stat"><div class="label">Elapsed</div><div class="value">${s.elapsed_min} min</div></div>`;
        html += `<div class="stat"><div class="label">Current Folder</div><div class="value" style="font-size:16px">${s.current_folder}</div></div>`;
        html += '</div>';
        html += `<div class="progress-bar"><div class="progress-fill" style="width:${s.percent}%">${s.percent}%</div></div>`;
        html += '<div class="logs">';
        for (const log of s.logs.reverse()) {
            const cls = log.status === 'OK' ? 'log-ok' : 'log-err';
            html += `<div class="${cls}">${log.time} ${log.file} — ${log.status}</div>`;
        }
        html += '</div>';
        document.getElementById('content').innerHTML = html;
    } catch(e) {}
}
update();
setInterval(update, 5000);
</script>
</body>
</html>"""


def main():
    input_dir = Path(os.getenv('INPUT_DIR', '/input'))
    sample_rate = int(os.getenv('SAMPLE_RATE', '4'))
    scale_paths = {
        'reflectivity': '/app/base_reflectivity_intensity_scale.png',
        'velocity': '/app/base_velocity_intensity_scale.png',
    }

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    logger = logging.getLogger('BatchProcessor')

    # Find all PNG files recursively
    logger.info(f"Scanning {input_dir} for PNG files...")
    all_images = sorted(input_dir.rglob('*.png'))
    # Filter out scale images and scripts
    all_images = [p for p in all_images if 'intensity_scale' not in p.name]
    logger.info(f"Found {len(all_images)} images to process")

    if not all_images:
        logger.info("Nothing to process. Exiting.")
        return

    status = BatchStatus(len(all_images))

    # Start web server
    web_thread = threading.Thread(target=start_web, args=(status, 8080), daemon=True)
    web_thread.start()
    logger.info("Dashboard available on mapped port")

    # Process in parallel — use 3 cores (0,1,2), leave core 3 for live service
    num_workers = int(os.getenv('WORKERS', '3'))
    logger.info(f"Processing with {num_workers} parallel workers")

    completed = 0
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(process_single_image, str(img), scale_paths, sample_rate): img
            for img in all_images
        }

        for future in as_completed(futures):
            completed += 1
            success, filename, folder, elapsed, error = future.result()
            status.record(success, filename, folder.replace(str(input_dir), ''), error)

            if success:
                logger.info(f"[{completed}/{len(all_images)}] OK {filename} ({elapsed:.1f}s)")
            else:
                logger.error(f"[{completed}/{len(all_images)}] FAIL {filename}: {error}")

    status.done = True
    logger.info(f"Batch complete: {status.processed} processed, {status.errors} errors")
    logger.info("Container will stay alive for dashboard access. Ctrl+C or docker stop to exit.")

    # Keep alive for dashboard
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
