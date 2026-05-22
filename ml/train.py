"""Train the tornado-risk CNN (Part B). Saves best model + immutable run manifest.

  python train.py --data ../data/full --epochs 30

Reproducibility: each run writes runs/<ts>/{model.pt, run.json} with hyperparams,
split counts, dataset fingerprint, and best val PR-AUC.
"""
import argparse, hashlib, json, time
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

    run = Path(a.out) / time.strftime("%Y%m%d_%H%M%S"); run.mkdir(parents=True, exist_ok=True)
    best = -1.0
    for ep in range(a.epochs):
        model.train(); tot = 0.0
        for x, y in tr:
            x, y = x.to(dev), y.to(dev)
            opt.zero_grad(); loss = lossf(model(x), y); loss.backward(); opt.step()
            tot += loss.item() * len(y)
        ys, ps = predict(model, va, dev)
        prauc = average_precision_score(ys, ps) if len(set(ys)) > 1 else float("nan")
        print(f"  ep {ep+1:2d}  train_loss {tot/len(splits['train']):.4f}  val PR-AUC {prauc:.3f}")
        if prauc == prauc and prauc > best:
            best = prauc; torch.save(model.state_dict(), run / "model.pt")

    json.dump({
        "args": vars(a), "device": dev, "best_val_prauc": best,
        "split_scans": {k: len(v) for k, v in splits.items()},
        "train_pos": npos, "train_neg": nneg,
        "dataset_fingerprint": dataset_fingerprint(rows),
    }, open(run / "run.json", "w"), indent=2)
    (Path(a.out) / "LATEST").write_text(str(run))   # robust run-path pointer for runners
    print(f"saved -> {run}/  (best val PR-AUC {best:.3f})")


if __name__ == "__main__":
    main()
