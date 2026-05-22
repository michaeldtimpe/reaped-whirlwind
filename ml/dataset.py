"""Dataset + leakage-safe splits for the tornado-risk CNN (Part B).

Split key = (date, station): every scan of one storm-day-radar stays in ONE split,
so the same mesocyclone can't appear in both train and test. Stratified so positives
are spread across splits. Reports independent-event counts (not just scan counts).
"""
import csv, random
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import Dataset


def load_rows(data_dir):
    return list(csv.DictReader(open(Path(data_dir) / "tensors_manifest.csv")))


def split_by_group(rows, val_frac=0.15, test_frac=0.15, seed=42):
    groups = {}
    for r in rows:
        groups.setdefault((r["date"], r["station"]), []).append(r)
    pos = [k for k, g in groups.items() if any(x["label"] == "1" for x in g)]
    neg = [k for k in groups if k not in set(pos)]
    rng = random.Random(seed); rng.shuffle(pos); rng.shuffle(neg)

    def carve(ks):
        n = len(ks); nte = int(n * test_frac); nva = int(n * val_frac)
        return ks[nte + nva:], ks[nte:nte + nva], ks[:nte]   # train, val, test

    ptr, pva, pte = carve(pos); ntr, nva, nte = carve(neg)
    gather = lambda ks: [r for k in ks for r in groups[k]]
    return {"train": gather(ptr + ntr), "val": gather(pva + nva), "test": gather(pte + nte)}


def split_summary(splits):
    for name, rows in splits.items():
        ev = {r["event_id"] for r in rows}
        posev = {r["event_id"] for r in rows if r["label"] == "1"}
        npos = sum(1 for r in rows if r["label"] == "1")
        print(f"  {name:5s}: {len(rows):5d} scans ({npos} pos / {len(rows)-npos} neg) | "
              f"{len(ev)} events ({len(posev)} pos / {len(ev)-len(posev)} neg)")


class RadarDataset(Dataset):
    def __init__(self, rows, data_dir):
        self.rows = rows; self.dir = Path(data_dir)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        x = np.load(self.dir / r["tensor"]).astype("float32")   # (2,128,128)
        return torch.from_numpy(x), torch.tensor(float(r["label"]))
