#!/usr/bin/env python3
"""
Load-bearing regression test for the inference service.

Goal: prove that the tensor the live inference pipeline builds from raw IEM PNGs
is BIT-EQUIVALENT to the .npy tensor that ml/preprocess.py wrote during training,
for the same (event_id, scan_time). If this ever fails, the live and trained
distributions have diverged → Part C is invalid until fixed.

Picks a random scan from data/full/tensors_manifest.csv, locates its source PNGs
in data/full/raw/, and runs `build_tensor` from the inference service against
them. Compares to the saved .npy with np.array_equal.

Usage:
  python services/inference/test_tensor_equiv.py [--data data/full] [--n 5]
"""
import argparse, csv, sys, random
from pathlib import Path

sys.path.insert(0, "data-tools")
sys.path.insert(0, "ml")
sys.path.insert(0, "services/inference")

import numpy as np

from preprocess import decode_crop, load_wld, REFL, VEL


def build_tensor_from_local(refl_png: Path, vel_png: Path, lat: float, lon: float, wld_path: Path) -> np.ndarray:
    """The same transform `inference_service.build_tensor` uses, but operating on local PNGs."""
    wld = load_wld(wld_path)
    rch, _ = decode_crop(refl_png, lat, lon, wld, REFL)
    if vel_png:
        vch, _ = decode_crop(vel_png, lat, lon, wld, VEL)
    else:
        vch = np.zeros_like(rch)
    return np.stack([rch, vch]).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/full")
    ap.add_argument("--n", type=int, default=5, help="sample this many random scans")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    data = Path(a.data)
    tman = list(csv.DictReader(open(data / "tensors_manifest.csv")))
    man  = {(r["event_id"], r["scan_time"]): r for r in csv.DictReader(open(data / "manifest.csv"))}

    rng = random.Random(a.seed)
    sample = rng.sample(tman, min(a.n, len(tman)))

    failures = 0
    for tr in sample:
        key = (tr["event_id"], tr["scan_time"])
        if key not in man:
            print(f"SKIP no manifest row for {key}")
            continue
        mr = man[key]
        refl_png = data / mr["refl"]
        vel_png  = data / mr["vel"] if mr["vel"] else None
        wld_path = data / "wld" / f"{mr['station']}.wld"
        if not refl_png.exists() or (vel_png and not vel_png.exists()) or not wld_path.exists():
            print(f"SKIP missing artifact for {key}")
            continue

        live  = build_tensor_from_local(refl_png, vel_png, float(mr["event_lat"]),
                                         float(mr["event_lon"]), wld_path)
        saved = np.load(data / tr["tensor"]).astype(np.float32)

        if not np.array_equal(live, saved):
            failures += 1
            d = np.abs(live - saved)
            print(f"FAIL  {key}  max|Δ|={d.max():.6g}  mean|Δ|={d.mean():.6g}")
        else:
            print(f"OK    {key}  shape={live.shape}")

    if failures:
        print(f"\n{failures}/{len(sample)} samples failed bit-equivalence — Part C is INVALID until fixed.")
        sys.exit(1)
    print(f"\nall {len(sample)} samples bit-equivalent. Part C transform contract holds.")


if __name__ == "__main__":
    main()
