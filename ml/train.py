"""Train the tornado-risk CNN (Part B). Resume-safe: per-epoch checkpoints + atomic run.json.

  python train.py --data ../data/full --epochs 30
  python train.py --resume runs/20260527_120000          # resume from last.pt in that run dir

Reproducibility: each run writes runs/<ts>/{model.pt, last.pt, run.json}. `model.pt` is
the best-PR-AUC weights so far; `last.pt` is the most recent epoch's full state
(model + optimizer + epoch counter + best + RNG state) for clean continuation.

NOTE on resume: RNG state is restored to bias toward equivalence, but DataLoader
worker scheduling and MPS nondeterminism mean a resumed run is a *continuation*,
not a bit-identical replay of an uninterrupted run.
"""
import argparse, hashlib, json, os, random, time
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import DataLoader
from sklearn.metrics import average_precision_score

from dataset import load_rows, split_by_group, split_summary, RadarDataset
from model import TornadoCNN


def pick_device():
    if torch.backends.mps.is_available(): return "mps"
    if torch.cuda.is_available(): return "cuda"
    return "cpu"


def dataset_fingerprint(rows):
    h = hashlib.sha256()
    for r in sorted(r["tensor"] for r in rows): h.update(r.encode())
    return h.hexdigest()[:16]


def write_json_atomic(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


@torch.no_grad()
def predict(model, loader, dev):
    model.eval(); ys, ps = [], []
    for x, y in loader:
        ps += list(torch.sigmoid(model(x.to(dev))).cpu().numpy()); ys += list(y.numpy())
    return np.array(ys), np.array(ps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data/full")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--resume", default=None, help="path to an existing run dir with last.pt")
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    dev = pick_device(); print(f"device: {dev}")

    rows = load_rows(a.data)
    splits = split_by_group(rows, seed=a.seed)
    print("splits (leakage-safe by date+station):"); split_summary(splits)

    tr = DataLoader(RadarDataset(splits["train"], a.data), batch_size=a.batch, shuffle=True)
    va = DataLoader(RadarDataset(splits["val"], a.data), batch_size=a.batch)

    npos = sum(1 for r in splits["train"] if r["label"] == "1")
    nneg = len(splits["train"]) - npos
    pos_weight = torch.tensor([nneg / max(1, npos)], device=dev)   # handle class imbalance
    model = TornadoCNN().to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)
    lossf = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    # --- resume vs fresh ---
    start_ep = 0
    best = -1.0
    if a.resume:
        run = Path(a.resume)
        ckpt_path = run / "last.pt"
        if not ckpt_path.exists():
            raise SystemExit(f"--resume {run}: no last.pt found")
        # weights_only=False: checkpoint contains numpy/python RNG state objects, not just tensors.
        # Safe because the checkpoint is produced by this same script locally.
        ckpt = torch.load(ckpt_path, map_location=dev, weights_only=False)
        model.load_state_dict(ckpt["model"])
        opt.load_state_dict(ckpt["opt"])
        start_ep = ckpt["epoch"]
        best = ckpt.get("best", -1.0)
        rng = ckpt.get("rng") or {}
        if "torch" in rng:  torch.set_rng_state(rng["torch"].cpu() if hasattr(rng["torch"], "cpu") else rng["torch"])
        if "numpy" in rng:  np.random.set_state(rng["numpy"])
        if "python" in rng: random.setstate(rng["python"])
        print(f"resumed from {run}: starting at epoch {start_ep+1}/{a.epochs}, best={best:.3f}")
    else:
        run = Path(a.out) / time.strftime("%Y%m%d_%H%M%S"); run.mkdir(parents=True, exist_ok=True)
        # Write LATEST immediately so a crash at epoch 1 still leaves the run discoverable.
        (Path(a.out) / "LATEST").write_text(str(run))

    for ep in range(start_ep, a.epochs):
        model.train(); tot = 0.0
        for x, y in tr:
            x, y = x.to(dev), y.to(dev)
            opt.zero_grad(); loss = lossf(model(x), y); loss.backward(); opt.step()
            tot += loss.item() * len(y)
        ys, ps = predict(model, va, dev)
        prauc = average_precision_score(ys, ps) if len(set(ys)) > 1 else float("nan")
        train_loss = tot / max(1, len(splits["train"]))
        print(f"  ep {ep+1:2d}  train_loss {train_loss:.4f}  val PR-AUC {prauc:.3f}")
        if prauc == prauc and prauc > best:
            best = prauc; torch.save(model.state_dict(), run / "model.pt")
        # --- per-epoch durable state ---
        torch.save({
            "model": model.state_dict(),
            "opt":   opt.state_dict(),
            "epoch": ep + 1,
            "best":  best,
            "rng": {
                "torch":  torch.get_rng_state(),
                "numpy":  np.random.get_state(),
                "python": random.getstate(),
            },
        }, run / "last.pt")
        write_json_atomic(run / "run.json", {
            "args": vars(a), "device": dev, "best_val_prauc": best,
            "split_scans": {k: len(v) for k, v in splits.items()},
            "train_pos": npos, "train_neg": nneg,
            "dataset_fingerprint": dataset_fingerprint(rows),
            "epochs_done": ep + 1,
            "last_train_loss": train_loss,
            "last_val_prauc": (None if prauc != prauc else float(prauc)),
        })
        # Keep LATEST current (no-op on the same run, but harmless and idempotent).
        (Path(a.out) / "LATEST").write_text(str(run))

    print(f"saved -> {run}/  (best val PR-AUC {best:.3f})")


if __name__ == "__main__":
    main()
