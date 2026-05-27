#!/usr/bin/env python3
"""
Preprocess collected IEM RIDGE PNGs into canonical model tensors (Part B).

Per scan in manifest.csv:
  - decode the paletted PNG by INDEX (IEM RIDGE encodes intensity in the index)
      reflectivity N0B: index 0 = missing                 -> normalise idx/255
      velocity     N0S: index 0 = missing, 15 = range-fold -> normalise idx/15
  - georeference via the per-station .wld; crop a fixed ~120 km physical box
    centred on the event lat/lon, then resize to 128x128 (handles any km/px)
  - stack 2 channels (reflectivity, velocity), masked cells -> 0
  - save float32 (2,128,128) .npy + tensors_manifest.csv

NOTE (checkpoint simplification): values are normalised palette INDEX — consistent
across positives & negatives (same products) and fine for an offline classifier,
but NOT calibrated to true dBZ/knots. N0S velocity is a coarse 16-level
storm-relative product. Refine to physical units in Part C if the model passes.

Usage: python preprocess.py --data ../data/sample
"""
import argparse, csv, math, os
from pathlib import Path
import numpy as np
from PIL import Image

OUT = 128
BOX_KM = 120.0
REFL = dict(missing={0}, denom=255.0)
VEL  = dict(missing={0, 15}, denom=15.0)


def load_wld(path: Path):
    A, _, _, E, C, F = [float(x) for x in path.read_text().split()]
    return A, E, C, F  # lon=A*col+C ; lat=E*row+F


def decode_crop(png_path: Path, lat, lon, wld, spec):
    A, E, C, F = wld
    km_px = abs(A) * 111.0 * math.cos(math.radians(lat))      # ~km per pixel
    npix = max(8, int(round(BOX_KM / max(km_px, 1e-6))))
    row, col = (lat - F) / E, (lon - C) / A
    idx = np.array(Image.open(png_path))
    H, W = idx.shape
    r0, c0 = int(round(row)) - npix // 2, int(round(col)) - npix // 2
    sr0, sc0, sr1, sc1 = max(0, r0), max(0, c0), min(H, r0 + npix), min(W, c0 + npix)
    buf = np.zeros((npix, npix), dtype=np.float32)
    ib = 0.0
    if sr1 > sr0 and sc1 > sc0:
        sub = idx[sr0:sr1, sc0:sc1].astype(np.float32)
        sub[np.isin(idx[sr0:sr1, sc0:sc1], list(spec["missing"]))] = 0.0
        sub = np.clip(sub / spec["denom"], 0.0, 1.0)
        buf[sr0 - r0:sr0 - r0 + sub.shape[0], sc0 - c0:sc0 - c0 + sub.shape[1]] = sub
        ib = sub.shape[0] * sub.shape[1] / (npix * npix)
    out = np.array(Image.fromarray(buf, mode="F").resize((OUT, OUT), Image.BILINEAR), dtype=np.float32)
    return out, ib


TENSOR_FIELDS = ["event_id","label","class","subtype","station","mag",
                 "date","scan_time","dist_km","tensor","has_velocity"]


def load_existing_tensor_manifest(path: Path):
    """Read prior tensor manifest if present. Tolerate a truncated trailing row."""
    if not path.exists() or path.stat().st_size == 0:
        return {}
    done = {}
    with open(path, newline="") as f:
        try:
            for r in csv.DictReader(f):
                t = r.get("tensor")
                if t:
                    done[t] = r
        except csv.Error:
            pass
    return done


def npy_loadable(path: Path) -> bool:
    try:
        np.load(path)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="../data/sample")
    data = Path(ap.parse_args().data)
    rows = list(csv.DictReader(open(data / "manifest.csv")))
    tdir = data / "tensors"; tdir.mkdir(exist_ok=True)
    tman = data / "tensors_manifest.csv"
    done = load_existing_tensor_manifest(tman)
    fresh = (not tman.exists()) or tman.stat().st_size == 0

    wld_cache = {}
    refl_means, vel_means, inb = [], [], []
    n_new, n_reused, n_healed_orphan, n_healed_corrupt, nvel, skipped = 0, 0, 0, 0, 0, 0
    rows_committed_since_fsync = 0

    fh = open(tman, "a", newline="", buffering=1)
    try:
        w = csv.DictWriter(fh, fieldnames=TENSOR_FIELDS)
        if fresh:
            w.writeheader(); fh.flush()
        if done:
            print(f"resumed: {len(done)} tensors already in manifest")

        for r in rows:
            wp = data / "wld" / f'{r["station"]}.wld'
            if not wp.exists():
                skipped += 1; continue
            name = Path(r["refl"]).stem.replace("_N0B", "") + ".npy"
            tensor_rel = f"tensors/{name}"
            npy_path = tdir / name

            # ---- skip / heal logic: .npy on disk is the source of truth ----
            if npy_path.exists():
                if npy_loadable(npy_path):
                    if tensor_rel in done:
                        n_reused += 1
                        continue
                    # orphan tensor (manifest row was lost in prior crash): append a row, no recompute.
                    row_out = {k: r.get(k, "") for k in (
                        "event_id","label","class","subtype","station","mag","date","scan_time","dist_km"
                    )} | {"tensor": tensor_rel, "has_velocity": int(bool(r["vel"]))}
                    w.writerow(row_out); fh.flush(); done[tensor_rel] = row_out
                    n_healed_orphan += 1
                    if r["vel"]: nvel += 1
                    continue
                else:
                    npy_path.unlink(missing_ok=True)
                    n_healed_corrupt += 1
                    # fall through and recompute

            # ---- compute tensor ----
            wld = wld_cache.setdefault(r["station"], load_wld(wp))
            lat, lon = float(r["event_lat"]), float(r["event_lon"])
            rch, ib = decode_crop(data / r["refl"], lat, lon, wld, REFL)
            if r["vel"]:
                vch, _ = decode_crop(data / r["vel"], lat, lon, wld, VEL); nvel += 1
            else:
                vch = np.zeros((OUT, OUT), dtype=np.float32)
            arr = np.stack([rch, vch]).astype(np.float32)
            np.save(npy_path, arr)
            row_out = {k: r.get(k, "") for k in (
                "event_id","label","class","subtype","station","mag","date","scan_time","dist_km"
            )} | {"tensor": tensor_rel, "has_velocity": int(bool(r["vel"]))}
            w.writerow(row_out); fh.flush(); done[tensor_rel] = row_out
            n_new += 1
            rows_committed_since_fsync += 1
            if rows_committed_since_fsync >= 100:
                os.fsync(fh.fileno()); rows_committed_since_fsync = 0

            if (rch > 0).any(): refl_means.append(float(rch[rch > 0].mean()))
            if r["vel"] and (vch > 0).any(): vel_means.append(float(vch[vch > 0].mean()))
            inb.append(ib)
        if rows_committed_since_fsync:
            os.fsync(fh.fileno())
    finally:
        fh.close()

    # Final report counts everything in the manifest after this run.
    out_rows = list(done.values())
    ev = {}
    for r in out_rows: ev.setdefault(r["event_id"], r["label"])
    npos = sum(1 for r in out_rows if str(r["label"]) == "1")
    pos_ev = sum(1 for v in ev.values() if str(v) == "1")
    print("=" * 60)
    print("PREPROCESS REPORT")
    print("=" * 60)
    print(f"this run: new={n_new}  reused={n_reused}  "
          f"healed_orphan={n_healed_orphan}  healed_corrupt={n_healed_corrupt}  "
          f"skipped_no_wld={skipped}")
    print(f"manifest total:        {len(out_rows)}  (shape 2x{OUT}x{OUT} float32, ~{BOX_KM:.0f} km box)")
    print(f"  positive / negative: {npos} / {len(out_rows)-npos}")
    print(f"  independent events:  {len(ev)}  (pos {pos_ev} / neg {len(ev)-pos_ev})")
    if inb:
        print(f"crop in-bounds frac:  mean {np.mean(inb):.2f}  min {np.min(inb):.2f}")
    if refl_means:
        print(f"refl norm mean-of-echo: {np.mean(refl_means):.3f}  (n={len(refl_means)})")
    if vel_means:
        print(f"vel  norm mean-of-echo: {np.mean(vel_means):.3f}  (n={len(vel_means)})")
    print(f"-> {tman}")


if __name__ == "__main__":
    main()
