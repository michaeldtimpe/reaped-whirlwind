#!/usr/bin/env python3
"""Pipeline Dashboard — monitoring + control for weather pipeline services.

Uses the Docker Engine REST API directly over the Unix socket instead of
shelling out to the docker CLI. This avoids API version mismatches between
a newer CLI and an older daemon (common on Synology NAS).
"""

import json, os, subprocess, socket, http.client
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import quote
from flask import Flask, jsonify, request, send_file, send_from_directory

# ── Config ───────────────────────────────────────────────────────────────────

STATUS_DIR      = Path(os.getenv("STATUS_DIR", "/status"))
SCREENSHOTS_DIR = Path(os.getenv("SCREENSHOTS_DIR", "/screenshots"))
PROCESSED_DIR   = Path(os.getenv("PROCESSED_DIR", "/processed"))
DOCKER_SOCKET   = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
DOCKER_API_VER  = os.getenv("DOCKER_API_VERSION", "1.43")

# Unified reaped-whirlwind compose project. The whole repo is mounted at COMPOSE_DIR
# so the dashboard can run `docker-compose -p PROJECT_NAME up -d --build <service>`.
COMPOSE_DIR     = os.getenv("COMPOSE_DIR", "/project")
PROJECT_NAME    = os.getenv("COMPOSE_PROJECT_NAME", "reaped-whirlwind")

# "service" = compose service name (rebuild target); "containers" = container_name(s)
# matched for state/logs/stop/start via the Docker API.
SERVICES = {
    "screenshot-service": {
        "label": "Screenshot Service",
        "service": "screenshot",
        "status_file": "screenshot_status.json",
        "containers": ["screenshot-service", "weather-screenshot-service"],
    },
    "radar-processor": {
        "label": "Radar Processor",
        "service": "processor",
        "status_file": "processor_status.json",
        "containers": ["radar-image-processor", "radar-processor"],
    },
    "weather-reporter": {
        "label": "Weather Reporter",
        "service": "weather",
        "status_file": "weather_status.json",
        "containers": ["weather-reporter", "weather-report-generator"],
    },
}

_cmd_lock = Lock()

# ── Docker Engine API (over Unix socket) ─────────────────────────────────────

class _DockerSocketConnection(http.client.HTTPConnection):
    """HTTPConnection that talks to a Unix domain socket."""
    def __init__(self, socket_path, timeout=5):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


def _docker_api(method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    """Call Docker Engine API. Returns (status_code, parsed_json_or_text)."""
    try:
        conn = _DockerSocketConnection(DOCKER_SOCKET)
        url = f"/v{DOCKER_API_VER}{path}"
        headers = {"Content-Type": "application/json"} if body else {}
        payload = json.dumps(body).encode() if body else None
        conn.request(method, url, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode()
        conn.close()
        try:
            return resp.status, json.loads(data)
        except json.JSONDecodeError:
            return resp.status, data
    except Exception as e:
        return 0, {"error": str(e)}


def _docker_ps_all() -> dict[str, dict]:
    """Return {name: {state, status}} for all containers."""
    code, data = _docker_api("GET", "/containers/json?all=true")
    if code != 200 or not isinstance(data, list):
        return {}
    result = {}
    for c in data:
        # Names come as ["/container-name"]
        names = [n.lstrip("/") for n in c.get("Names", [])]
        state = c.get("State", "unknown")  # running, exited, etc.
        for name in names:
            result[name] = {"state": state, "status": c.get("Status", "")}
    return result


def _find_container(containers: list[str], ps_map: dict) -> tuple[str, str | None]:
    """Return (state, matched_name) for first matching container."""
    for name in containers:
        if name in ps_map:
            return ps_map[name]["state"], name
    return "not_found", None


def _container_logs_api(containers: list[str], lines: int = 80) -> str:
    """Fetch logs via Docker API."""
    for name in containers:
        code, data = _docker_api("GET", f"/containers/{quote(name, safe='')}/logs?stdout=true&stderr=true&tail={lines}")
        if code == 200:
            # Docker log stream can have header bytes; clean them up
            if isinstance(data, str):
                # Strip Docker stream framing (8-byte headers per chunk)
                clean = []
                for line in data.split("\n"):
                    # Remove non-printable leading bytes
                    cleaned = line.lstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08")
                    if cleaned:
                        clean.append(cleaned)
                return "\n".join(clean)
            return str(data)
    return "No logs available"


def _docker_container_action(name: str, action: str) -> tuple[int, str]:
    """Start or stop a container. Returns (status_code, message)."""
    code, data = _docker_api("POST", f"/containers/{quote(name, safe='')}/{action}")
    if code in (204, 304):  # 204=ok, 304=already in that state
        return code, "ok"
    return code, str(data)


def _run_compose(compose_dir: str, *args, timeout: int = 300) -> dict:
    """Run docker compose via CLI — still needed for 'up -d --build'."""
    env = os.environ.copy()
    env["DOCKER_HOST"] = f"unix://{DOCKER_SOCKET}"
    # Try docker-compose (v1) first on Synology, then docker compose (v2)
    for cmd_base in (["docker-compose"], ["docker", "compose"]):
        try:
            result = subprocess.run(
                [*cmd_base, *args],
                cwd=compose_dir,
                capture_output=True, text=True, timeout=timeout, env=env,
            )
            return {
                "ok": result.returncode == 0,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
                "code": result.returncode,
            }
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": "Timed out", "code": -1}
    return {"ok": False, "stdout": "", "stderr": "docker compose not found", "code": -1}


# ── File helpers ─────────────────────────────────────────────────────────────

def _read_status(filename: str) -> dict | None:
    p = STATUS_DIR / filename
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

def _latest_file(directory: Path, glob: str) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob(glob), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None

def _folder_stats(directory: Path) -> dict:
    if not directory.exists():
        return {"count": 0, "size_mb": 0}
    files = [f for f in directory.iterdir() if f.is_file()]
    return {
        "count": len(files),
        "size_mb": round(sum(f.stat().st_size for f in files) / 1_048_576, 2),
    }


# ── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)

@app.route("/")
def index():
    return send_from_directory("/app/static", "index.html")

@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory("/app/static", p)


# ── Debug ────────────────────────────────────────────────────────────────────

@app.route("/api/debug")
def api_debug():
    info = {
        "docker_socket": DOCKER_SOCKET,
        "docker_socket_exists": os.path.exists(DOCKER_SOCKET),
        "docker_api_version": DOCKER_API_VER,
    }

    # Test Docker API directly
    code, data = _docker_api("GET", "/version")
    info["docker_engine_version"] = data if code == 200 else f"ERR {code}: {data}"

    # All containers
    ps = _docker_ps_all()
    info["all_containers"] = {k: v["state"] for k, v in ps.items()}

    # Service matches
    info["service_matches"] = {}
    for key, svc in SERVICES.items():
        state, matched = _find_container(svc["containers"], ps)
        info["service_matches"][key] = {"state": state, "matched": matched}

    # Status files
    info["status_dir_files"] = [f.name for f in STATUS_DIR.iterdir()] if STATUS_DIR.exists() else []
    info["status_data"] = {k: _read_status(v["status_file"]) for k, v in SERVICES.items()}
    info["screenshots_png_count"] = len(list(SCREENSHOTS_DIR.glob("radar_*.png"))) if SCREENSHOTS_DIR.exists() else 0
    info["processed_json_count"] = len(list(PROCESSED_DIR.glob("radar_*.json"))) if PROCESSED_DIR.exists() else 0

    return jsonify(info)


# ── Status API ───────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    ps = _docker_ps_all()

    services = {}
    for key, svc in SERVICES.items():
        status_data = _read_status(svc["status_file"])
        docker_state, _ = _find_container(svc["containers"], ps)

        # If docker can't find it but status file says running, trust that
        if docker_state in ("not_found", "") and status_data:
            inner = (status_data.get("status") or status_data.get("current_status") or "").lower()
            if inner in ("running", "idle", "processing", "starting"):
                docker_state = "running"

        services[key] = {
            "label": svc["label"],
            "docker_state": docker_state,
            "status_data": status_data,
        }

    # ── Screenshot service: synthesize when no status JSON ────────────────
    ss = services.get("screenshot-service", {})
    if ss.get("status_data") is None:
        latest_png = _latest_file(SCREENSHOTS_DIR, "radar_*.png")
        png_count = len(list(SCREENSHOTS_DIR.glob("radar_*.png"))) if SCREENSHOTS_DIR.exists() else 0
        proc_data = services.get("radar-processor", {}).get("status_data")
        proc_count = proc_data.get("total_processed", 0) if proc_data else 0
        proc_last = proc_data.get("last_processed_time") if proc_data else None
        proc_file = proc_data.get("last_processed") if proc_data else None

        is_producing = ss.get("docker_state") == "running" or (proc_data and proc_count > 0)

        ss["status_data"] = {
            "status": "running" if is_producing else "unknown",
            "total_captured": png_count if png_count > 0 else proc_count,
            "total_errors": 0,
            "last_capture_file": latest_png.name if latest_png else proc_file,
            "last_capture_time": (
                datetime.fromtimestamp(latest_png.stat().st_mtime, tz=timezone.utc).isoformat()
                if latest_png else proc_last
            ),
            "_synthetic": True,
        }
        if is_producing:
            ss["docker_state"] = "running"

    # Latest files
    latest_ss = _latest_file(SCREENSHOTS_DIR, "radar_*.png")
    ss_info = {"filename": latest_ss.name, "modified": latest_ss.stat().st_mtime,
               "size_bytes": latest_ss.stat().st_size} if latest_ss else None

    latest_pr = _latest_file(PROCESSED_DIR, "radar_*.json")
    pr_info = {"filename": latest_pr.name, "modified": latest_pr.stat().st_mtime,
               "size_bytes": latest_pr.stat().st_size} if latest_pr else None

    return jsonify({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": services,
        "latest_screenshot": ss_info,
        "latest_processed": pr_info,
        "folders": {
            "screenshots": _folder_stats(SCREENSHOTS_DIR),
            "processed": _folder_stats(PROCESSED_DIR),
        },
    })


# ── Image / data endpoints ───────────────────────────────────────────────────

@app.route("/api/screenshots/latest")
def latest_screenshot():
    f = _latest_file(SCREENSHOTS_DIR, "radar_*.png")
    if not f:
        return jsonify({"error": "No screenshots"}), 404
    return send_file(f, mimetype="image/png")

@app.route("/api/processed/latest")
def latest_processed():
    f = _latest_file(PROCESSED_DIR, "radar_*.json")
    if not f:
        return jsonify({"error": "No processed files"}), 404
    try:
        data = json.loads(f.read_text())
        meta = data.get("metadata", {})
        meta["_filename"] = f.name
        meta["_size_bytes"] = f.stat().st_size
        return jsonify(meta)
    except Exception:
        return jsonify({"error": "Read failed"}), 500


# ── Service control ──────────────────────────────────────────────────────────

def _svc_container_action(name: str, action: str):
    """Stop/start the service's container via the Docker API (by container_name)."""
    ps = _docker_ps_all()
    _, matched = _find_container(SERVICES[name]["containers"], ps)
    if not matched:
        return jsonify({"ok": False, "error": "container not found"}), 404
    code, msg = _docker_container_action(matched, action)
    return jsonify({"ok": code in (204, 304), "code": code, "message": msg, "container": matched})

@app.route("/api/services/<name>/stop", methods=["POST"])
def svc_stop(name):
    if name not in SERVICES:
        return jsonify({"error": "Unknown"}), 404
    with _cmd_lock:
        return _svc_container_action(name, "stop")

@app.route("/api/services/<name>/start", methods=["POST"])
def svc_start(name):
    if name not in SERVICES:
        return jsonify({"error": "Unknown"}), 404
    with _cmd_lock:
        return _svc_container_action(name, "start")

@app.route("/api/services/<name>/update", methods=["POST"])
def svc_update(name):
    if name not in SERVICES:
        return jsonify({"error": "Unknown"}), 404
    with _cmd_lock:
        return jsonify(_run_compose(
            COMPOSE_DIR, "-p", PROJECT_NAME, "up", "-d", "--build",
            SERVICES[name]["service"], timeout=600))

@app.route("/api/services/<name>/logs")
def svc_logs(name):
    if name not in SERVICES:
        return jsonify({"error": "Unknown"}), 404
    lines = request.args.get("lines", 80, type=int)
    return jsonify({"logs": _container_logs_api(SERVICES[name]["containers"], lines)})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9007"))
    print(f"Dashboard :{port} | API v{DOCKER_API_VER} | socket={DOCKER_SOCKET}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
