"""Turn gi_sample_5grams.py output into ranked candidate document pairs.

A sampled 5-gram that occurs in 2..--max-group documents contributes one point to
every pair of those documents; more common ones (boilerplate, stock phrases) are
ignored. Pairs are ranked by containment: shared samples / the smaller document's
sampled 5-gram count. pct_a / pct_b give each side's share separately.

Several slice directories can be combined; each slice numbers its documents from 0,
so slice k's ids are offset by k << 24. Partitions are by hash, so part_XX of every
slice holds the same hash range and they are joined part by part.

--thin N keeps only hashes with h % N == 0 (N a multiple of the sampling rate used
when extracting), to measure what a sparser sample would have found.

Usage: gi_pairs.py OUT_TSV SAMPLE_DIR [SAMPLE_DIR ...] [--thin 16] [--max-group 20] [--min-shared 10]
                   [--db data/gi.sqlite3] [--no-tsv]
--db also writes the pairs into gi.sqlite3's `pairs` table (replacing it; pair_id = this rank order,
values rounded as in the TSV), which is what gi_report.py reads. --no-tsv skips the TSV (OUT_TSV is
then only the spill directory's name).
"""
import argparse
import glob
import os
from itertools import combinations

import numpy as np

REC = np.dtype([("h", "<u8"), ("d", "<u4")])
SLICE_SHIFT = 24


def load_part(dirs, name, thin):
    recs = []
    for k, sd in enumerate(dirs):
        r = np.fromfile(os.path.join(sd, name), dtype=REC)
        if thin > 1:
            r = r[r["h"] % np.uint64(thin) == 0]
        r["d"] += np.uint32(k << SLICE_SHIFT)
        recs.append(r)
    return np.unique(np.concatenate(recs))  # also drops (hash, doc) repeats


def pairs_for_part(rec, max_group):
    if len(rec) == 0:
        return np.empty(0, np.uint64)
    h, d = rec["h"], rec["d"].astype(np.uint64)
    starts = np.concatenate(([0], np.flatnonzero(np.diff(h)) + 1))
    sizes = np.diff(np.concatenate((starts, [len(h)])))
    out = []
    for g in range(2, max_group + 1):
        s = starts[sizes == g]
        if len(s) == 0:
            continue
        members = d[s[:, None] + np.arange(g)]  # (groups, g), sorted by doc id within group
        for i, j in combinations(range(g), 2):
            out.append((members[:, i] << np.uint64(32)) | members[:, j])
    return np.concatenate(out) if out else np.empty(0, np.uint64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_tsv")
    ap.add_argument("sample_dirs", nargs="+")
    ap.add_argument("--thin", type=int, default=1)
    ap.add_argument("--max-group", type=int, default=20)
    ap.add_argument("--min-shared", type=int, default=10)
    ap.add_argument("--db", help="also write the pairs table of this gi.sqlite3")
    ap.add_argument("--no-tsv", action="store_true")
    ap.add_argument("--spill-buckets", type=int, default=128)
    args = ap.parse_args()

    # A pair's count is only complete once every hash partition has been read, so pairs
    # are spilled to disk bucketed by pair key, and each bucket is counted on its own:
    # memory is one bucket's pairs rather than every pair seen so far.
    spill_dir = args.out_tsv + ".spill"
    os.makedirs(spill_dir, exist_ok=True)
    spills = [open(os.path.join(spill_dir, f"{i:03d}.bin"), "wb") for i in range(args.spill_buckets)]
    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(args.sample_dirs[0], "part_*.bin")))
    emitted = 0
    for name in names:
        k = pairs_for_part(load_part(args.sample_dirs, name, args.thin), args.max_group)
        emitted += len(k)
        bucket = (k * np.uint64(0x9E3779B97F4A7C15)) % np.uint64(args.spill_buckets)
        order = np.argsort(bucket, kind="stable")
        k, bucket = k[order], bucket[order]
        bounds = np.searchsorted(bucket, np.arange(args.spill_buckets + 1, dtype=np.uint64))
        for i in range(args.spill_buckets):
            k[bounds[i]:bounds[i + 1]].tofile(spills[i])
        print(f"{name}: {emitted:,} pair occurrences spilled", flush=True)
    for f in spills:
        f.close()

    keys, counts = [], []
    for i in range(args.spill_buckets):
        path = os.path.join(spill_dir, f"{i:03d}.bin")
        k, c = np.unique(np.fromfile(path, dtype=np.uint64), return_counts=True)
        os.remove(path)
        keep = c >= args.min_shared
        keys.append(k[keep])
        counts.append(c[keep])
    os.rmdir(spill_dir)
    keys, counts = np.concatenate(keys), np.concatenate(counts)
    print(f"counted {args.spill_buckets} spill buckets", flush=True)
    docs = {}
    for k, sd in enumerate(args.sample_dirs):
        with open(os.path.join(sd, "docs.tsv")) as f:
            for line in f:
                d, dkey, total, sampled = line.rstrip("\n").split("\t")
                if args.thin > 1:
                    sampled = None  # recomputed below from the thinned parts
                docs[int(d) + (k << SLICE_SHIFT)] = [dkey, int(total), sampled]
    if args.thin > 1:
        per_doc = {}
        for name in names:
            d = load_part(args.sample_dirs, name, args.thin)["d"]
            u, c = np.unique(d, return_counts=True)
            for di, ci in zip(u.tolist(), c.tolist()):
                per_doc[di] = per_doc.get(di, 0) + ci
        for di, v in docs.items():
            v[2] = per_doc.get(di, 0)
    rows = []
    for key, n in zip(keys.tolist(), counts.tolist()):
        a, b = docs[key >> 32], docs[key & 0xFFFFFFFF]
        rows.append((n / max(1, min(int(a[2]), int(b[2]))), n, a, b))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    if not args.no_tsv:
        with open(args.out_tsv, "w") as f:
            # pct_a: share of A's sampled 5-grams also in B; pct_b the reverse. containment is
            # the larger of the two (the smaller document's share).
            f.write("containment\tpct_a\tpct_b\tshared\tdkey_a\tsampled_a\tdkey_b\tsampled_b\n")
            for cont, n, a, b in rows:
                f.write(f"{cont:.3f}\t{100 * n / max(1, int(a[2])):.1f}\t{100 * n / max(1, int(b[2])):.1f}\t"
                        f"{n}\t{a[0]}\t{a[2]}\t{b[0]}\t{b[2]}\n")
    if args.db:
        import gi_db
        db = gi_db.connect(args.db)
        db.execute("delete from pairs")
        db.executemany("insert into pairs values (?,?,?,?,?,?,?,?,?)",
                       ((i, a[0], b[0], n, int(a[2]), int(b[2]), round(100 * n / max(1, int(a[2])), 1),
                         round(100 * n / max(1, int(b[2])), 1), round(cont, 3))
                        for i, (cont, n, a, b) in enumerate(rows, 1)))
        db.commit()
    print(f"{len(rows):,} pairs with >= {args.min_shared} shared samples"
          + ("" if args.no_tsv else f" -> {args.out_tsv}") + (f" and {args.db}:pairs" if args.db else ""))


if __name__ == "__main__":
    main()
