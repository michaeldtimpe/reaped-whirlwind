#!/usr/bin/env python3
"""
Live tornado-risk inference service (Part C).

Every POLL_INTERVAL seconds:
  1. List the latest KFWS N0B (reflectivity) and N0S (storm-relative velocity)
     scans in the IEM RIDGE archive.
  2. Pick the most recent N0B; pair it with the closest N0S within
     MAX_PAIR_DELTA_SECONDS. Skip the cycle if no N0S is within the window —
     never silently substitute a stale velocity scan.
  3. Decode + crop + resize via `ml/preprocess.decode_crop` — the *same*
     code path as training. This is the no-train/serve-skew guarantee.
  4. Forward through TornadoCNN; sigmoid to [0,1].
  5. Atomically rewrite /status/inference_status.json.

CLI:
  python inference_service.py                # service loop (Flask /health + bg poll)
  python inference_service.py --once         # one synchronous cycle, JSON to stdout, exit
"""
import argparse, gc, hashlib, json, os, sys, threading, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Shared modules live under bind-mounted dirs; see docker-compose volumes.
sys.path.insert(0, "/data-tools")
sys.path.insert(0, "/ml")

import numpy as np
import torch
from flask import Flask, jsonify

from iem import (ARCH, REFL_PROD, VEL_PROD,
                 list_times, nearest, fetch_png, fetch_text, validate_png_on_disk)
from preprocess import decode_crop, load_wld, REFL, VEL, PREPROCESS_VERSION
from model import TornadoCNN

# ---- config from env (set in docker-compose.yml) -----------------------------
PORT             = int(os.environ.get("PORT", "9008"))
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "300"))         # 5 min
MAX_PAIR_DELTA   = int(os.environ.get("MAX_PAIR_DELTA_SECONDS", "300"))
MAX_SCORE_AGE    = int(os.environ.get("MAX_SCORE_AGE_SECONDS", "1800"))
KFWS_LAT         = float(os.environ.get("KFWS_LAT",  "32.5728"))
KFWS_LON         = float(os.environ.get("KFWS_LON", "-97.3031"))
THRESHOLD        = float(os.environ.get("MODEL_RISK_THRESHOLD", "0.8"))
MODEL_PATH       = Path(os.environ.get("MODEL_PATH", "/model/model.pt"))
MANIFEST_PATH    = Path(os.environ.get("MANIFEST_PATH", "/model/manifest.json"))
EXPECTED_FP_ENV  = os.environ.get("MODEL_FINGERPRINT", "").strip()     # env wins over manifest
STATUS_PATH      = Path(os.environ.get("STATUS_PATH", "/status/inference_status.json"))
STATE_DIR        = Path(os.environ.get("STATE_DIR", "/state"))

KFWS_STATION       = "KFWS"
CYCLE_DEADLINE_SEC = 60.0


# ---- helpers -----------------------------------------------------------------
def utc_iso(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def fail(msg: str, code: int = 1):
    sys.stderr.write(f"FATAL: {msg}\n")
    sys.exit(code)


def atomic_write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---- startup correctness chain ----------------------------------------------
def startup_check_and_load_model():
    """Returns (loaded_model, model_sha256, manifest_dict). Exits non-zero on any mismatch."""
    if not MODEL_PATH.exists():
        fail(f"model not found at {MODEL_PATH}")
    if not MANIFEST_PATH.exists():
        fail(f"manifest not found at {MANIFEST_PATH}")
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
    except Exception as e:
        fail(f"manifest unreadable: {e}")
    for key in ("model_sha256", "preprocess_version", "threshold_recommended"):
        if key not in manifest:
            fail(f"manifest missing required key '{key}'")
    if manifest["preprocess_version"] != PREPROCESS_VERSION:
        fail(f"preprocess_version mismatch: manifest={manifest['preprocess_version']!r} "
             f"runtime={PREPROCESS_VERSION!r} — model was trained against an older transform")
    actual_sha = sha256_file(MODEL_PATH)
    # Priority: MODEL_FINGERPRINT env var > manifest.json. Env is for emergency overrides;
    # absent env, manifest is authoritative.
    expected_sha = EXPECTED_FP_ENV or manifest["model_sha256"]
    if actual_sha != expected_sha:
        src = "MODEL_FINGERPRINT env" if EXPECTED_FP_ENV else "manifest.json"
        fail(f"model fingerprint mismatch ({src}): got {actual_sha} expected {expected_sha}")
    # weights_only=True: model.pt is a pure state_dict (not the resume/last.pt). Defense in depth.
    model = TornadoCNN()
    model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu", weights_only=True))
    model.eval()
    return model, actual_sha, manifest


# ---- IEM fetch + pair --------------------------------------------------------
def get_wld_path() -> Path:
    """Cache KFWS.wld once in STATE_DIR. Returns path; may not exist on first-IEM-down boot."""
    p = STATE_DIR / f"{KFWS_STATION}.wld"
    if p.exists() and p.stat().st_size > 10:
        return p
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    s = KFWS_STATION[1:]
    today = datetime.now(timezone.utc)
    times = list_times(s, REFL_PROD, today)
    if not times:
        return p   # caller handles non-existence
    sample_fn = next(iter(times.values()))
    wld_url = (ARCH.format(y=today.year, m=today.month, d=today.day, s=s, prod=REFL_PROD)
               + sample_fn.replace(".png", ".wld"))
    txt = fetch_text(wld_url)
    if txt:
        p.write_text(txt)
    return p


def fetch_pair():
    """Return (n0b_path, n0s_path, n0b_time, n0s_time, scan_delta_seconds) or
    (None, None, n0b_time_or_None, n0s_time_or_None, None) on partial failure.
    Looks at today + yesterday UTC so we don't miss scans right around midnight."""
    s = KFWS_STATION[1:]
    refl_idx, vel_idx = {}, {}
    now = datetime.now(timezone.utc)
    for day_off in (0, 1):
        d = (now - timedelta(days=day_off)).replace(hour=0, minute=0, second=0, microsecond=0)
        refl_idx.update(list_times(s, REFL_PROD, d))
        vel_idx.update(list_times(s, VEL_PROD, d))
    if not refl_idx:
        return None, None, None, None, None
    n0b_time = max(refl_idx.keys())
    n0s_time = nearest(vel_idx, n0b_time, MAX_PAIR_DELTA / 60.0)
    if n0s_time is None:
        return None, None, n0b_time, None, None
    scan_delta = abs((n0s_time - n0b_time).total_seconds())

    cache = STATE_DIR / "current"
    cache.mkdir(parents=True, exist_ok=True)

    def _grab(prod, t, fn):
        url = ARCH.format(y=t.year, m=t.month, d=t.day, s=s, prod=prod) + fn
        for _ in range(2):
            data = fetch_png(url)
            if data is None:
                continue
            local = cache / f"{KFWS_STATION}_{prod}_{t:%Y%m%d%H%M}.png"
            local.write_bytes(data)
            if validate_png_on_disk(local):
                return local
            local.unlink(missing_ok=True)
        return None

    n0b_path = _grab(REFL_PROD, n0b_time, refl_idx[n0b_time])
    n0s_path = _grab(VEL_PROD,  n0s_time, vel_idx[n0s_time])
    if n0b_path is None or n0s_path is None:
        return None, None, n0b_time, n0s_time, scan_delta
    return n0b_path, n0s_path, n0b_time, n0s_time, scan_delta


def build_tensor(n0b_path: Path, n0s_path: Path, wld_path: Path) -> torch.Tensor:
    """SAME transform as ml/preprocess.py: palette index → mask → 120 km crop → 128² resize → stack."""
    wld = load_wld(wld_path)
    rch, _ = decode_crop(n0b_path, KFWS_LAT, KFWS_LON, wld, REFL)
    vch, _ = decode_crop(n0s_path, KFWS_LAT, KFWS_LON, wld, VEL)
    arr = np.stack([rch, vch]).astype(np.float32)
    return torch.from_numpy(arr).unsqueeze(0)


# ---- service state -----------------------------------------------------------
class State:
    def __init__(self, model, model_sha256, manifest):
        self.model = model
        self.model_sha256 = model_sha256
        self.manifest = manifest
        self.lock = threading.Lock()
        self.last_status = {
            "status": "uninitialized",
            "last_score": None,
            "last_score_time": None,
            "last_scan_time": None,
            "scan_delta_seconds": None,
            "pair_n0b_file": None,
            "pair_n0s_file": None,
            "model_sha256": model_sha256,
            "model_path": str(MODEL_PATH),
            "preprocess_version": PREPROCESS_VERSION,
            "threshold": THRESHOLD,
            "cycle_ms": {},
            "errors": [],
        }

    def record_error(self, kind, msg):
        with self.lock:
            errs = list(self.last_status.get("errors", []))
            errs.append({"time": utc_iso(), "kind": kind, "msg": str(msg)[:200]})
            self.last_status["errors"] = errs[-5:]

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.last_status))   # deep copy via JSON

    def commit(self, status):
        """Replace last_status and atomically write the JSON file."""
        with self.lock:
            self.last_status = status
            atomic_write_json(STATUS_PATH, status)


# ---- cycle -------------------------------------------------------------------
def run_cycle(state: State, wld_path: Path) -> dict:
    t0 = time.time()
    timings = {}
    status = state.snapshot()
    status["cycle_ms"] = {}

    try:
        t_f = time.time()
        n0b, n0s, n0b_time, n0s_time, scan_delta = fetch_pair()
        timings["fetch"] = int((time.time() - t_f) * 1000)

        if n0b is None or n0s is None:
            if n0b_time is None:
                reason = "no_recent_n0b_in_iem"
            elif n0s_time is None:
                reason = f"no_n0s_within_{MAX_PAIR_DELTA}s_of_n0b_at_{utc_iso(n0b_time)}"
            else:
                reason = "png_fetch_failed"
            state.record_error("fetch", reason)
            # Keep last_score visible but mark stale if we ever had one.
            status = state.snapshot()
            status["status"] = "stale" if status.get("last_score") is not None else "uninitialized"
            status["cycle_ms"] = timings
            state.commit(status)
            return status

        if not wld_path.exists():
            wld_path_local = get_wld_path()
            if not wld_path_local.exists():
                state.record_error("wld", "wld unavailable after retry")
                status = state.snapshot()
                status["status"] = "error"
                status["cycle_ms"] = timings
                state.commit(status)
                return status
            wld_path = wld_path_local

        t_p = time.time()
        x = build_tensor(n0b, n0s, wld_path)
        timings["preprocess"] = int((time.time() - t_p) * 1000)

        t_i = time.time()
        with torch.no_grad():
            score = float(torch.sigmoid(state.model(x)).item())
        timings["infer"] = int((time.time() - t_i) * 1000)

        t_w = time.time()
        now = datetime.now(timezone.utc)
        status = state.snapshot()
        status.update({
            "status": "running",
            "last_score": round(score, 4),
            "last_score_time": utc_iso(now),
            "last_scan_time": utc_iso(n0b_time),
            "scan_delta_seconds": int(scan_delta),
            "pair_n0b_file": n0b.name,
            "pair_n0s_file": n0s.name,
            "model_sha256": state.model_sha256,
            "model_path": str(MODEL_PATH),
            "preprocess_version": PREPROCESS_VERSION,
            "threshold": THRESHOLD,
        })
        state.commit(status)
        timings["write"] = int((time.time() - t_w) * 1000)
        status["cycle_ms"] = timings
        state.commit(status)
        gc.collect()
        return status
    except Exception as e:
        state.record_error("cycle", f"{type(e).__name__}: {e}")
        status = state.snapshot()
        status["status"] = "error"
        status["cycle_ms"] = timings
        state.commit(status)
        return status
    finally:
        elapsed = time.time() - t0
        if elapsed > CYCLE_DEADLINE_SEC:
            state.record_error("deadline", f"cycle {elapsed:.1f}s > {CYCLE_DEADLINE_SEC}s")


# ---- service loop + Flask /health -------------------------------------------
def poll_loop(state, wld_path):
    while True:
        try:
            run_cycle(state, wld_path)
        except Exception as e:
            state.record_error("loop", f"{type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)


def make_app(state: State) -> Flask:
    app = Flask(__name__)

    @app.route("/health")
    def health():
        s = state.snapshot()
        age = None
        if s.get("last_score_time"):
            t = datetime.strptime(s["last_score_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age = int((datetime.now(timezone.utc) - t).total_seconds())
        s["score_age_seconds"] = age
        if s.get("status") == "running" and age is not None and age > MAX_SCORE_AGE:
            s["status"] = "stale"
        return jsonify(s)

    return app


# ---- entrypoint --------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="run one cycle synchronously, print status JSON, exit")
    args = ap.parse_args()

    model, sha, manifest = startup_check_and_load_model()
    state = State(model, sha, manifest)
    state.commit(state.last_status)   # writes the initial "uninitialized" status

    wld_path = get_wld_path()

    if args.once:
        status = run_cycle(state, wld_path)
        print(json.dumps(status, indent=2))
        sys.exit(0 if status.get("status") == "running" else 1)

    app = make_app(state)
    t = threading.Thread(target=poll_loop, args=(state, wld_path), daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
