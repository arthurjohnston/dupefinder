"""Shape of heavy pairs (>=50 shared samples) in a 1/8 doc subsample of slice 0: containment and size ratio."""
import numpy as np, os
exec(open("exp_pair_scaling.py").read().split("parts = list(range(64))")[0])
m = 8
keys, counts = np.empty(0, np.uint64), np.empty(0, np.int64)
size = None
for i in range(64):
    r = load(i); r = np.unique(r[r["d"] % m == 0])
    b = np.bincount(r["d"], minlength=4_000_000)
    size = b if size is None else size + b[:len(size)]
    k, c = np.unique(pair_keys(r, 20), return_counts=True)
    keys, inv = np.unique(np.concatenate((keys, k)), return_inverse=True)
    merged = np.zeros(len(keys), np.int64); np.add.at(merged, inv, np.concatenate((counts, c))); counts = merged
for t in (10, 50):
    sel = counts >= t
    a = (keys[sel] >> np.uint64(32)).astype(np.int64); b = (keys[sel] & np.uint64(0xFFFFFFFF)).astype(np.int64)
    sa, sb = size[a], size[b]
    cont = counts[sel] / np.minimum(sa, sb)
    ratio = np.maximum(sa, sb) / np.maximum(1, np.minimum(sa, sb))
    print(f"\npairs with >= {t} shared: {sel.sum():,}")
    print("  containment (of smaller) quantiles p10/p25/p50/p75/p90:", np.round(np.percentile(cont, [10,25,50,75,90]), 2))
    print("  size ratio larger/smaller p10/p50/p90:", np.round(np.percentile(ratio, [10,50,90]), 2))
    twin = (cont >= 0.8) & (ratio <= 1.25)
    print(f"  twin-shaped (cont>=0.8, sizes within 25%): {twin.mean():.0%};  contained (cont>=0.8, ratio>2): {((cont>=0.8)&(ratio>2)).mean():.0%};"
          f"  partial (cont<0.3): {(cont<0.3).mean():.0%}")
    # docs in many heavy pairs = clusters
    deg = np.bincount(np.concatenate((a, b)))
    print(f"  docs involved: {(deg>0).sum():,}; docs in >=5 such pairs: {(deg>=5).sum():,}; max degree {deg.max()}")
