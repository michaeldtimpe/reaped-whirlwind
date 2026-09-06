#!/usr/bin/env python3
"""
Historical replay smoke test for the live inference path.

Answers "does the deployed mechanism actually score real tornado scans higher
than quiet ones?" — something neither the held-out eval (which scores cached
training tensors) nor `test_tensor_equiv.py` (which proves the transform is
bit-identical) can answer for the *service* code path end to end.

For each case it pulls the KFWS N0B/N0S pair nearest a UTC timestamp from the
IEM RIDGE archive, pairs them with the SAME tolerance the service uses, builds
the tensor with the service's own `build_tensor` (→ ml/preprocess.decode_crop),
and runs the canonical `models/v1` weights loaded through the service's own
fingerprint-checked loader. Nothing here is a re-implementation.

Built-in positives are SPC-confirmed EF1+ tornadoes within ~45 km of KFWS
(2020-2025 era, the N0B/N0S product era). Each is scored at several offsets
before/after the SPC touchdown time so a "pre-tornado" ramp is visible.
Built-in controls are quiet DFW afternoons/nights.

CAVEATS (read before trusting the numbers)
  * These events lie inside the model's training years and the (date, station)
    split lives only in the analysis machine's manifest, so some positives may
    have been TRAINED ON. This is a mechanism smoke test, not an unbiased eval.
    The unbiased numbers are in models/v1/eval.json / docs/MODEL_CARD.md.
  * SPC `time` is CST (tz=3) regardless of DST; UTC = CST + 6 h.
  * The live crop is centred on KFWS, not on the tornado; training crops were
    centred on the event. A tornado 40 km out sits well inside the ~120 km box
    but not at its centre.
  * The 0.8 threshold trades recall for specificity (~30 % recall on the test
    set), so most positives are NOT expected to cross it. The pass criterion is
    discrimination: positives at T0 must score above every quiet control, and
    at least one positive must cross the threshold.

Usage (from the repo root, needs network + the torch venv):
  .venv/bin/python services/inference/replay_smoke.py
  .venv/bin/python services/inference/replay_smoke.py --offsets -30,-15,-5,0,10
  .venv/bin/python services/inference/replay_smoke.py --at 2022-12-13T14:14Z --at 2023-08-15T21:00Z
  .venv/bin/python services/inference/replay_smoke.py --json out.json

Exit status: 0 if the discrimination criterion holds, 1 otherwise, 2 if the
archive could not be reached for a case (no verdict).
"""
import argparse, json, os, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]

# Point the service module at the repo checkout before importing it: its config
# is read from the environment at import time.
os.environ.setdefault("MODEL_PATH",    str(_REPO / "models" / "v1" / "model.pt"))
os.environ.setdefault("MANIFEST_PATH", str(_REPO / "models" / "v1" / "manifest.json"))
_tmp = Path(os.environ.get("REPLAY_STATE_DIR") or tempfile.mkdtemp(prefix="replay-smoke-"))
os.environ.setdefault("STATE_DIR",     str(_tmp))
os.environ.setdefault("STATUS_PATH",   str(_tmp / "inference_status.json"))
os.environ.setdefault("SCORE_LOG_PATH", str(_tmp / "scores.jsonl"))
sys.path.insert(0, str(_HERE))

import torch                                                   # noqa: E402
import inference_service as svc                                # noqa: E402
from iem import ARCH, REFL_PROD, VEL_PROD, list_times, nearest, fetch_png, fetch_text  # noqa: E402

STATION = svc.KFWS_STATION
S = STATION[1:]

# (label, UTC touchdown, note). Source: SPC 1950-2025_actual_tornadoes.csv,
# EF1+, start point within 45 km of KFWS (32.5728, -97.3031), 2020+.
POSITIVES = [
    ("EF2 Crowley/Burleson 15 km",  "2022-04-05T03:41Z", "2022-04-04 21:41 CST"),
    ("EF1 Cresson 18 km",           "2020-01-10T23:41Z", "2020-01-10 17:41 CST"),
    ("EF2 Arlington 20 km",         "2020-11-25T02:51Z", "2020-11-24 20:51 CST"),
    ("EF1 Fort Worth 27 km",        "2022-12-13T14:14Z", "2022-12-13 08:14 CST (outbreak)"),
    ("EF1 Irving 36 km",            "2023-03-16T21:47Z", "2023-03-16 15:47 CST"),
    ("EF1 Dallas 42 km",            "2025-03-04T11:24Z", "2025-03-04 05:24 CST"),
]
# Quiet controls: no severe weather in DFW. Mid-August heat, a clear winter
# night, and a benign spring afternoon.
CONTROLS = [
    ("quiet Aug afternoon",  "2023-08-15T21:00Z"),
    ("quiet Jan night",      "2024-01-20T06:00Z"),
    ("quiet Apr afternoon",  "2024-04-10T20:00Z"),
    ("quiet Oct morning",    "2022-10-05T15:00Z"),
]

_index_cache = {}


def _times(prod, day):
    key = (prod, day.date())
    if key not in _index_cache:
        _index_cache[key] = list_times(S, prod, day)
    return _index_cache[key]


def _index_around(prod, when):
    idx = {}
    for off in (-1, 0, 1):
        idx.update(_times(prod, when + timedelta(days=off)))
    return idx


def _wld_for(day, sample_fn):
    p = _tmp / f"{STATION}_{day:%Y%m%d}.wld"
    if p.exists():
        return p
    txt = fetch_text(ARCH.format(y=day.year, m=day.month, d=day.day, s=S, prod=REFL_PROD)
                     + sample_fn.replace(".png", ".wld"))
    if not txt:
        return None
    p.write_text(txt)
    return p


def score_at(when, tol_min=10):
    """Score the archive pair nearest `when`. Returns a result dict."""
    when = when.replace(tzinfo=None)
    refl = _index_around(REFL_PROD, when)
    vel  = _index_around(VEL_PROD, when)
    if not refl:
        return {"error": "no N0B listing"}
    n0b_t = nearest(refl, when, tol_min)
    if n0b_t is None:
        return {"error": f"no N0B within {tol_min} min"}
    n0s_t = nearest(vel, n0b_t, svc.MAX_PAIR_DELTA / 60.0)
    if n0s_t is None:
        return {"error": f"no N0S within {svc.MAX_PAIR_DELTA}s of N0B", "n0b_time": n0b_t.isoformat()}
    cache = _tmp / "current"
    cache.mkdir(parents=True, exist_ok=True)
    paths = {}
    for prod, t, fn in ((REFL_PROD, n0b_t, refl[n0b_t]), (VEL_PROD, n0s_t, vel[n0s_t])):
        local = cache / fn
        if not local.exists():
            data = fetch_png(ARCH.format(y=t.year, m=t.month, d=t.day, s=S, prod=prod) + fn)
            if data is None:
                return {"error": f"fetch failed: {fn}"}
            local.write_bytes(data)
        paths[prod] = local
    wld = _wld_for(n0b_t, refl[n0b_t])
    if wld is None:
        return {"error": "wld fetch failed"}
    x = svc.build_tensor(paths[REFL_PROD], paths[VEL_PROD], wld)
    with torch.no_grad():
        score = float(torch.sigmoid(_MODEL(x)).item())
    return {
        "n0b_time": n0b_t.isoformat() + "Z",
        "n0s_time": n0s_t.isoformat() + "Z",
        "scan_delta_s": abs((n0s_t - n0b_t).total_seconds()),
        "score": score,
    }


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def main():
    global _MODEL
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offsets", default="-30,-15,-5,0,10",
                    help="minutes relative to touchdown to score positives at")
    ap.add_argument("--at", action="append", default=[],
                    help="ad-hoc UTC time YYYY-MM-DDTHH:MMZ (repeatable); scored once, no verdict")
    ap.add_argument("--json", help="write full results here")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override MODEL_RISK_THRESHOLD (default: manifest/env)")
    args = ap.parse_args()

    _MODEL, sha, manifest = svc.startup_check_and_load_model()
    thr = args.threshold if args.threshold is not None else svc.THRESHOLD
    offsets = [int(x) for x in args.offsets.split(",") if x.strip()]
    print(f"model {sha[:12]}…  preprocess {manifest['preprocess_version']}  threshold {thr}")
    print(f"pair tolerance {svc.MAX_PAIR_DELTA}s  cache {_tmp}\n")

    results = {"positives": [], "controls": [], "adhoc": [], "threshold": thr, "model_sha256": sha}
    unreachable = 0

    if not args.at:
        print("POSITIVES (SPC-confirmed tornado; offsets are minutes from touchdown)")
        for label, ts, note in POSITIVES:
            t0 = _parse(ts)
            row = {"label": label, "touchdown_utc": ts, "note": note, "series": []}
            cells = []
            for off in offsets:
                r = score_at(t0 + timedelta(minutes=off))
                r["offset_min"] = off
                row["series"].append(r)
                if "score" in r:
                    mark = "*" if r["score"] >= thr else " "
                    cells.append(f"{off:+4d}m {r['score']:.3f}{mark}")
                else:
                    cells.append(f"{off:+4d}m  ---  ")
                    unreachable += 1
            results["positives"].append(row)
            print(f"  {label:<30} " + "  ".join(cells))
        print("\nCONTROLS (quiet)")
        for label, ts in CONTROLS:
            r = score_at(_parse(ts))
            r.update({"label": label, "at_utc": ts})
            results["controls"].append(r)
            if "score" in r:
                mark = "*" if r["score"] >= thr else " "
                print(f"  {label:<30}       {r['score']:.3f}{mark}   ({r['n0b_time']})")
            else:
                print(f"  {label:<30}       ---   {r['error']}")
                unreachable += 1

    for ts in args.at:
        r = score_at(_parse(ts))
        r["at_utc"] = ts
        results["adhoc"].append(r)
        if "score" in r:
            print(f"  {ts}  score {r['score']:.3f}  (N0B {r['n0b_time']}  N0S {r['n0s_time']})")
        else:
            print(f"  {ts}  ---  {r['error']}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))

    if args.at:
        return 0

    # Verdict: discrimination, not calibration.
    t0_scores = [c["score"] for p in results["positives"] for c in p["series"]
                 if c.get("offset_min") == 0 and "score" in c]
    ctl_scores = [c["score"] for c in results["controls"] if "score" in c]
    if not t0_scores or not ctl_scores:
        print("\nNO VERDICT: archive unreachable for every case")
        return 2
    max_ctl = max(ctl_scores)
    above = sum(1 for s in t0_scores if s > max_ctl)
    crossed = sum(1 for s in t0_scores if s >= thr)
    ctl_crossed = sum(1 for s in ctl_scores if s >= thr)
    print(f"\nT0 positives above every control: {above}/{len(t0_scores)}   "
          f"positives >= {thr}: {crossed}/{len(t0_scores)}   "
          f"controls >= {thr}: {ctl_crossed}/{len(ctl_scores)}   "
          f"(max control {max_ctl:.3f})")
    if unreachable:
        print(f"note: {unreachable} cell(s) unreachable in the archive")
    ok = (above == len(t0_scores)) and crossed >= 1 and ctl_crossed == 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
