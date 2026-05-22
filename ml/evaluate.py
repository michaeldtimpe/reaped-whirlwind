"""Honest evaluation = the Part-B go/no-go (run on the held-out test split).

  python evaluate.py --data ../data/full --model runs/<ts>/model.pt

Reports, on the SAME leakage-safe test split:
  - CNN PR-AUC / ROC-AUC vs trivial baselines (must beat them):
      * majority (base rate)        * reflectivity intensity   * velocity shear
  - operational metrics: precision at fixed recall (a model is only useful at a
    tolerable false-alarm rate, which aggregate AUC can hide)
  - per-negative-subtype false-positive rate (the real worry is failing on
    warning-no-tornado, i.e. severe-but-non-tornadic storms)

GO if the CNN clearly beats the baselines AND reaches usable precision at a sane
recall; otherwise report NO-GO (archive / rescope).
"""
import argparse, json
from pathlib import Path
from collections import Counter
import numpy as np
import torch
from torch.utils.data import DataLoader
from sklearn.metrics import average_precision_score, roc_auc_score, precision_recall_curve

from dataset import load_rows, split_by_group, RadarDataset
from model import TornadoCNN


def prauc(y, s):
    return average_precision_score(y, s) if len(set(y)) > 1 else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data/full")
    ap.add_argument("--model", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--thresh", type=float, default=0.5)
    a = ap.parse_args()
    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")

    test = split_by_group(load_rows(a.data), seed=a.seed)["test"]
    dl = DataLoader(RadarDataset(test, a.data), batch_size=64)
    model = TornadoCNN().to(dev)
    model.load_state_dict(torch.load(a.model, map_location=dev)); model.eval()

    ys, ps = [], []
    with torch.no_grad():
        for x, y in dl:
            ps += list(torch.sigmoid(model(x.to(dev))).cpu().numpy()); ys += list(y.numpy())
    ys, ps = np.array(ys), np.array(ps)

    # baselines straight from the tensors
    refl, vshear = [], []
    for r in test:
        t = np.load(Path(a.data) / r["tensor"])
        refl.append(float(t[0].mean()))
        vshear.append(float(t[1].max() - t[1].min()))
    refl, vshear = np.array(refl), np.array(vshear)

    print("=" * 64)
    print("TORNADO-RISK EVALUATION (held-out, leakage-safe test split)")
    print("=" * 64)
    print(f"test scans: {len(ys)}  | base rate (positives): {ys.mean():.3f}")
    print(f"{'model':22s} {'PR-AUC':>8s} {'ROC-AUC':>8s}")
    rocs = (lambda s: roc_auc_score(ys, s) if len(set(ys)) > 1 else float('nan'))
    print(f"{'CNN':22s} {prauc(ys,ps):8.3f} {rocs(ps):8.3f}   <-- must beat baselines")
    print(f"{'baseline: refl mean':22s} {prauc(ys,refl):8.3f} {rocs(refl):8.3f}")
    print(f"{'baseline: vel shear':22s} {prauc(ys,vshear):8.3f} {rocs(vshear):8.3f}")
    print(f"{'baseline: majority':22s} {ys.mean():8.3f} {'0.500':>8s}")

    print("\noperational — precision at fixed recall (CNN):")
    prec, rec, thr = precision_recall_curve(ys, ps)
    for tgt in (0.3, 0.5, 0.7, 0.9):
        i = int(np.argmin(np.abs(rec - tgt)))
        t = thr[min(i, len(thr) - 1)]
        print(f"  recall~{tgt:.1f}: precision {prec[i]:.3f}  (threshold {t:.2f})")

    print(f"\nfalse-positive rate by negative subtype @ thresh {a.thresh}:")
    pred = ps >= a.thresh
    fp, tot = Counter(), Counter()
    for r, p in zip(test, pred):
        if r["label"] == "0":
            tot[r["subtype"]] += 1; fp[r["subtype"]] += int(p)
    for st in sorted(tot):
        print(f"  {st:18s}: {fp[st]:4d}/{tot[st]:4d} = {fp[st]/max(1,tot[st]):.2f}")
    print("  (warning_no_torn FP is the key number — distinguishing tornadic from"
          " severe-but-non-tornadic storms is the actual hard problem)")

    out = Path(a.model).parent / "eval.json"
    json.dump({"n_test": int(len(ys)), "base_rate": float(ys.mean()),
               "cnn_prauc": float(prauc(ys, ps)), "refl_prauc": float(prauc(ys, refl)),
               "vshear_prauc": float(prauc(ys, vshear)),
               "fp_by_subtype": {st: [int(fp[st]), int(tot[st])] for st in tot}},
              open(out, "w"), indent=2)
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
