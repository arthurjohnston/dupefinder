"""How do candidate pairs scale with corpus size? All 64 partitions, docs subsampled by id % m.

For each m, pairs from hash groups of size 2..CAP, aggregated across partitions, histogram of shared
counts. Pairs among a uniform doc subsample scale ~ f^2 whether they are real duplicates or chance,
so counts at fraction f extrapolate to the full 107M corpus by (107M / docs)^2.
"""
import os
import sys
from itertools import combinations

import numpy as np

D = "/home/arthur/Sites/dupefinder/general_index/data/slice0"
REC = np.dtype([("h", "<u8"), ("d", "<u4")])
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 20
MS = [int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "32,16,8").split(",")]
FULL = 107_233_728


def load(i):
    path = os.path.join(D, f"part_{i:02d}.bin")
    return np.fromfile(path, dtype=REC, count=os.path.getsize(path) // REC.itemsize)


def pair_keys(r, cap):
    r = np.unique(r)
    h, d = r["h"], r["d"].astype(np.uint64)
    starts = np.concatenate(([0], np.flatnonzero(np.diff(h)) + 1))
    sizes = np.diff(np.concatenate((starts, [len(h)])))
    out = []
    for g in range(2, cap + 1):
        s = starts[sizes == g]
        if len(s):
            m = d[s[:, None] + np.arange(g)]
            for i, j in combinations(range(g), 2):
                out.append((m[:, i] << np.uint64(32)) | m[:, j])
    return np.concatenate(out) if out else np.empty(0, np.uint64)


parts = list(range(64))
for m in MS:
    keys, counts, ndocs, nrec = np.empty(0, np.uint64), np.empty(0, np.int64), 0, 0
    for i in parts:
        r = load(i)
        r = r[r["d"] % m == 0]
        nrec += len(r)
        if len(r):
            ndocs = max(ndocs, int(r["d"].max()) + 1)
        k, c = np.unique(pair_keys(r, CAP), return_counts=True)
        keys, inv = np.unique(np.concatenate((keys, k)), return_inverse=True)
        merged = np.zeros(len(keys), np.int64)
        np.add.at(merged, inv, np.concatenate((counts, c)))
        counts = merged
    n = ndocs // m
    scale = (FULL / n) ** 2
    inc = int(counts.sum())
    print(f"\n1/{m}: {n:,} docs, {nrec:,} records, cap {CAP}: {inc:,} pair-increments, "
          f"{len(keys):,} distinct pairs   (x{scale:,.0f} to full corpus)")
    for t in (1, 2, 3, 5, 10, 20, 50):
        k = int((counts >= t).sum())
        print(f"   shared >= {t:>2}: {k:>12,} pairs   -> full corpus est. {k * scale:,.0f}")
