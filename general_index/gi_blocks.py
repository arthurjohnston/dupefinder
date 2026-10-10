"""Step 2 at full scale: pair General Index shards one shard-pair block at a time.

Replaces gi_pairs.py's single all-shards run, which needs RAM and spill in proportion to the square of
the corpus and can't fit 16 shards on this machine (NEXT_STEPS.md, "Step 2"). Subcommands:

  docs     per shard, numpy document tables (dkeys, sampled 5-gram counts) from docs.tsv
  thin     write a 1-in-N copy of a shard (keep h % N == 0; a subset of a 1-in-4 sample)
  freq     the global phrase-frequency pass: per hash partition, across every shard in the run,
           how many documents each sampled 5-gram is in (only counts >= 2 are kept: a phrase in one
           document pairs nothing). Every block reads these, so a group-size rule means the same
           thing in every block. Also writes the common-phrase store (below).
  block    pair shards I and J (I <= J): each partition's rows from the two shards, phrases in
           2..--cap documents corpus-wide, all pairs inside each phrase's group (for I < J only pairs
           with one document in each shard), spilled to disk by pair key and counted per bucket.
           A pair belongs to exactly one block, so the min-shared threshold applies exactly per block:
           10 shared samples at 1-in-4, 5 at 1-in-8 (the same ~40 shared 5-grams).
  run      every block of the run not yet done, N in parallel; resumable and incremental (a new
           shard adds only its own blocks). Blocks made under an older frequency pass (a different
           set of shards) are stale and redone.
  common   shared_common for every pair: how many common phrases (in cap+1..1000 documents) the
           two documents share.
  clusters template families too big for any pair threshold: common phrases whose document sets
           are nearly the same (MinHash-proposed, exactly checked Jaccard >= 0.8) merged into
           families -> phrase_clusters / phrase_cluster_summary.
  finalize rebuild gi.sqlite3's pairs table from the run's blocks, ranked as gi_pairs.py ranked them
           (containment, then shared), so gi_report.py and everything after it is unchanged.

Phrases shared by many documents are never thrown away. Up to --cap (default 100) documents they
are expanded into pairs. Above it they go to the common-phrase store (common/part_XX.npz): the
hash, the document count, and the document list for groups up to 1000 (a 100-document sample above
1000), with a phrase-level summary in the common_phrases table. Pairs get a shared_common count,
and families of documents sharing many common phrases get phrase_clusters.

Document ids are global: shard k's local id + (k << 24) (gi_pairs.py's scheme), so a pair's
(a, b) orientation and keys are identical to the old single-run output.

Usage (from general_index/data):
  gi_blocks.py run --rate 8 --cap 100 --shards 0-15 [--db gi.sqlite3] [--workers 3]
  gi_blocks.py common|clusters|finalize --rate 8 --cap 100 [--db gi.sqlite3]
  gi_blocks.py thin --shard 0 --rate 8          (slice0 -> slice0_s8)
"""
import argparse
import glob
import json
import os
import resource
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent / "data"
REC = np.dtype([("h", "<u8"), ("d", "<u4")])
SHIFT = 24
NPARTS = 64
MIN_SHARED = 10
COMMON_LIST_MAX = 1000   # common phrases up to this many documents keep their full document list
COMMON_SAMPLE = 100      # documents kept for phrases above COMMON_LIST_MAX
GOLDEN = np.uint64(0x9E3779B97F4A7C15)


# ------------------------------------------------------------------ shards and documents

def shard_dir(k, rate):
    """Shards 0-3 were streamed at 1-in-4 (sliceK); a 1-in-8 copy is sliceK_s8. Shards 4-15 were
    streamed at 1-in-8 (sliceK)."""
    if rate == 4:
        return DATA / f"slice{k}"
    if rate == 8:
        return DATA / (f"slice{k}_s8" if k <= 3 else f"slice{k}")
    raise SystemExit(f"no shard layout for rate {rate}")


def shard_ready(k, rate):
    d = shard_dir(k, rate)
    if not (d / "docs.tsv").exists() or len(glob.glob(str(d / "part_*.bin"))) != NPARTS:
        return False
    if k > 3 or rate == 4:  # streamed shards: the stream log must say done (docs.tsv is written last)
        log = DATA / f"slice{k}.log"
        return log.exists() and "done:" in log.read_text()[-400:]
    return (d / "THINNED").exists()


def parse_shards(spec):
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return sorted(set(out))


MIN_SHARED_BY_RATE = {4: 10, 8: 5}  # the same ~40 shared 5-grams at either sampling rate


def run_dir(rate, cap, min_shared=None):
    ms = MIN_SHARED_BY_RATE.get(rate, MIN_SHARED) if min_shared is None else min_shared
    return DATA / (f"blocks_r{rate}_c{cap}" + ("" if ms == 10 else f"_m{ms}"))


def docs_tables(k, rate):
    """(dkeys S40 array, sampled counts uint32) indexed by local id; cached as .npy next to docs.tsv."""
    d = shard_dir(k, rate)
    kp, sp = d / "docs_dkey.npy", d / "docs_sampled.npy"
    if not (kp.exists() and sp.exists()) or kp.stat().st_mtime < (d / "docs.tsv").stat().st_mtime:
        ids, dkeys, sampled = [], [], []
        with open(d / "docs.tsv") as f:
            for line in f:
                i, dkey, _total, s = line.rstrip("\n").split("\t")
                ids.append(int(i)); dkeys.append(dkey); sampled.append(int(s))
        n = max(ids) + 1
        dk = np.zeros(n, dtype="S40"); sa = np.zeros(n, dtype=np.uint32)
        dk[ids] = dkeys; sa[ids] = sampled
        np.save(kp, dk); np.save(sp, sa)
    return np.load(kp, mmap_mode="r"), np.load(sp, mmap_mode="r")


def sort_dedupe(r):
    """Sorted by (hash, doc), repeats dropped. np.unique / sort(order=...) on this structured dtype take
    ~10x longer than a two-key lexsort (measured: 11.1s vs 2.8s for one partition), and the structured
    sorts were most of a block's time."""
    r = r[np.lexsort((r["d"], r["h"]))]
    keep = np.ones(len(r), bool)
    keep[1:] = (r["h"][1:] != r["h"][:-1]) | (r["d"][1:] != r["d"][:-1])
    return r[keep]


def merge_sorted(parts):
    """Concatenate (hash, doc)-sorted parts whose doc ids are in increasing, disjoint ranges (shards in
    order): a stable sort on the hash alone keeps (hash, doc) order (0.9s vs 31s for sort(order=...))."""
    rec = np.concatenate(parts)
    return rec[np.argsort(rec["h"], kind="stable")]


def load_part(k, rate, p):
    """One shard's partition p, (hash, doc) repeats dropped, doc ids made global."""
    r = sort_dedupe(np.fromfile(shard_dir(k, rate) / f"part_{p:02d}.bin", dtype=REC))
    r["d"] += np.uint32(k << SHIFT)
    return r


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


# ------------------------------------------------------------------ thin

def thin(k, rate):
    src, dst = shard_dir(k, 4), shard_dir(k, rate)
    assert rate % 4 == 0 and rate > 4
    dst.mkdir(exist_ok=True)
    counts = None
    for p in range(NPARTS):
        r = np.fromfile(src / f"part_{p:02d}.bin", dtype=REC)
        r = r[r["h"] % np.uint64(rate) == 0]
        r.tofile(dst / f"part_{p:02d}.bin")
        c = np.bincount(r["d"].astype(np.int64), minlength=0)
        counts = c if counts is None else (np.pad(counts, (0, max(0, len(c) - len(counts))))
                                          + np.pad(c, (0, max(0, len(counts) - len(c)))))
    # sampled = rows per document after thinning (gi_sample_5grams counts rows, repeats included)
    with open(src / "docs.tsv") as f, open(dst / "docs.tsv", "w") as out:
        for line in f:
            i, dkey, total, _s = line.rstrip("\n").split("\t")
            i = int(i)
            out.write(f"{i}\t{dkey}\t{total}\t{int(counts[i]) if i < len(counts) else 0}\n")
    (dst / "THINNED").write_text(f"from {src.name} at 1-in-{rate}\n")
    print(f"thinned shard {k} -> {dst.name}", flush=True)


# ------------------------------------------------------------------ freq + common store

def freq_partition(args):
    p, shards, rate, cap, out = args
    rec = merge_sorted([load_part(k, rate, p) for k in sorted(shards)])
    u, c = np.unique(rec["h"], return_counts=True)  # rec["h"] is sorted, so this is a run-length count
    keep = c >= 2
    np.savez(out / "freq" / f"part_{p:02d}.npz", h=u[keep], c=c[keep].astype(np.uint32))
    # common-phrase store: phrases in more than cap documents
    common_h, common_c = u[keep & (c > cap)], c[keep & (c > cap)].astype(np.uint32)
    sel = rec[np.isin(rec["h"], common_h, assume_unique=False)]  # still (h, d)-sorted
    starts = np.searchsorted(sel["h"], common_h)
    ends = np.searchsorted(sel["h"], common_h, side="right")
    lens = np.minimum(ends - starts, np.where(common_c <= COMMON_LIST_MAX, common_c, COMMON_SAMPLE))
    docs = np.concatenate([sel["d"][s:s + n] for s, n in zip(starts, lens)]) if len(common_h) else np.empty(0, np.uint32)
    offsets = np.concatenate(([0], np.cumsum(lens))).astype(np.int64)
    for name, arr in (("h", common_h), ("c", common_c), ("off", offsets), ("docs", docs.astype(np.uint32))):
        np.save(out / "common" / f"part_{p:02d}_{name}.npy", arr)
    hist = {"rows": int(len(rec)), "distinct": int(len(u)), "ge2": int(keep.sum()),
            "common": int(len(common_h)), "common_doc_rows": int(len(docs))}
    return p, hist, peak_rss_mb()


def freq(shards, rate, cap, workers, db):
    out = run_dir(rate, cap)
    (out / "freq").mkdir(parents=True, exist_ok=True)
    (out / "common").mkdir(parents=True, exist_ok=True)
    t0, tot = time.time(), {}
    # memory per worker grows with the rows merged: measured 2.6 GB for 4 shards at 1-in-8 (5.1 GB at
    # 1-in-4), so ~10.5 GB for 16 shards at 1-in-8. Keep the pass under ~12 GB in all.
    est_gb = 2.6 * len(shards) / 4 * 8 / rate
    fw = max(1, min(workers, int(12 // est_gb)))
    with ProcessPoolExecutor(fw) as ex:
        futs = [ex.submit(freq_partition, (p, shards, rate, cap, out)) for p in range(NPARTS)]
        rss = 0
        for f in as_completed(futs):
            p, hist, r = f.result()
            rss = max(rss, r)
            for k, v in hist.items():
                tot[k] = tot.get(k, 0) + v
    epoch = {"shards": shards, "rate": rate, "cap": cap, "made": time.strftime("%Y-%m-%d %H:%M:%S"),
             "seconds": round(time.time() - t0), "peak_rss_mb_per_worker": rss, **tot}
    (out / "EPOCH.json").write_text(json.dumps(epoch, indent=1))
    # phrase-level summary of the common store
    db.execute("create table if not exists common_phrases (run text, part integer, hash integer, doc_count integer, "
               "docs_stored integer, primary key (run, part, hash))")
    run = run_dir(rate, cap).name.removeprefix("blocks_")
    db.execute("delete from common_phrases where run = ?", (run,))
    for p in range(NPARTS):
        h, c, off, _ = load_common(out, p)
        stored = np.diff(off)
        db.executemany("insert into common_phrases values (?,?,?,?,?)",
                       ((run, p, int(hh), int(cc), int(ss)) for hh, cc, ss in
                        zip(np.asarray(h).view(np.int64).tolist(), np.asarray(c).tolist(), stored.tolist())))
    db.commit()
    print(f"freq: shards {shards}, {epoch['seconds']}s, {tot['ge2']:,} phrases in 2+ documents, "
          f"{tot['common']:,} in more than {cap}, peak {rss} MB/worker", flush=True)
    return epoch


def load_common(out, p):
    """Common-phrase store for partition p: (hashes, corpus-wide doc counts, offsets, docs), docs
    memory-mapped. Reads the older single .npz layout too."""
    base = out / "common" / f"part_{p:02d}"
    if Path(f"{base}_h.npy").exists():
        return (np.load(f"{base}_h.npy"), np.load(f"{base}_c.npy"), np.load(f"{base}_off.npy"),
                np.load(f"{base}_docs.npy", mmap_mode="r"))
    z = np.load(f"{base}.npz")
    return z["h"], z["c"], z["offsets"], z["docs"]


def load_freq(out, p):
    z = np.load(out / "freq" / f"part_{p:02d}.npz")
    return z["h"], z["c"]


# ------------------------------------------------------------------ block

def block_pairs_for_partition(rec, fh, fc, cap, cross):
    """All pairs inside each phrase group whose corpus-wide size is 2..cap; rec sorted by (h, d)."""
    idx = np.searchsorted(fh, rec["h"])
    idx_c = np.minimum(idx, len(fh) - 1)
    ok = (len(fh) > 0) & (fh[idx_c] == rec["h"])
    ok &= fc[idx_c] <= cap
    rec = rec[ok]
    if len(rec) < 2:
        return np.empty(0, np.uint64)
    h, d = rec["h"], rec["d"].astype(np.uint64)
    starts = np.concatenate(([0], np.flatnonzero(np.diff(h)) + 1))
    sizes = np.diff(np.concatenate((starts, [len(h)])))
    if cross:  # only groups with members in both shards (d is sorted, so first < last shard)
        first, last = d[starts] >> np.uint64(SHIFT), d[starts + sizes - 1] >> np.uint64(SHIFT)
        keepg = (sizes >= 2) & (first != last)
    else:
        keepg = sizes >= 2
    starts, sizes = starts[keepg], sizes[keepg]
    out = []
    for g in np.unique(sizes):
        s = starts[sizes == g]
        members = d[s[:, None] + np.arange(g)]
        iu, ju = np.triu_indices(g, 1)
        a, b = members[:, iu].ravel(), members[:, ju].ravel()
        if cross:
            m = (a >> np.uint64(SHIFT)) != (b >> np.uint64(SHIFT))
            a, b = a[m], b[m]
        out.append((a << np.uint64(32)) | b)
    return np.concatenate(out) if out else np.empty(0, np.uint64)


def run_block(args):
    i, j, rate, cap, buckets = args
    out = run_dir(rate, cap)
    t0 = time.time()
    spill = out / "spill" / f"{i}_{j}"
    spill.mkdir(parents=True, exist_ok=True)
    files = [open(spill / f"{b:03d}.bin", "wb") for b in range(buckets)]
    emitted = 0
    for p in range(NPARTS):
        fh, fc = load_freq(out, p)
        rec = load_part(i, rate, p) if i == j else merge_sorted([load_part(i, rate, p), load_part(j, rate, p)])
        k = block_pairs_for_partition(rec, fh, fc, cap, cross=i != j)
        emitted += len(k)
        bucket = (k * GOLDEN) % np.uint64(buckets)
        order = np.argsort(bucket, kind="stable")
        k, bucket = k[order], bucket[order]
        bounds = np.searchsorted(bucket, np.arange(buckets + 1, dtype=np.uint64))
        for b in range(buckets):
            k[bounds[b]:bounds[b + 1]].tofile(files[b])
        del rec, k, bucket, order
    for f in files:
        f.close()
    spill_bytes = sum(os.path.getsize(spill / f"{b:03d}.bin") for b in range(buckets))
    keys, counts = [], []
    for b in range(buckets):
        path = spill / f"{b:03d}.bin"
        u, c = np.unique(np.fromfile(path, dtype=np.uint64), return_counts=True)
        os.remove(path)
        keep = c >= MIN_SHARED_BY_RATE.get(rate, MIN_SHARED)
        keys.append(u[keep]); counts.append(c[keep])
    spill.rmdir()
    keys, counts = np.concatenate(keys), np.concatenate(counts).astype(np.uint32)
    np.savez(out / "pairs" / f"block_{i}_{j}.npz", keys=keys, counts=counts)
    return {"i": i, "j": j, "pairs": int(len(keys)), "emitted": int(emitted), "seconds": round(time.time() - t0),
            "peak_rss_mb": peak_rss_mb(), "spill_gb": round(spill_bytes / 1e9, 2)}


def ensure_tables(db):
    db.executescript("""
    create table if not exists pair_blocks (
        run text, shard_i integer, shard_j integer, rate integer, cap integer, epoch_shards text,
        status text, pairs integer, emitted integer, seconds integer, peak_rss_mb integer, spill_gb real,
        finished_at text, primary key (run, shard_i, shard_j));
    create table if not exists phrase_clusters (
        run text, cluster_id integer, dkey text, groups_in_cluster integer, groups_shared integer,
        primary key (run, cluster_id, dkey));
    create table if not exists phrase_cluster_summary (
        run text, cluster_id integer, n_groups integer, n_docs integer, mean_group_size real,
        primary key (run, cluster_id));
    """)


def run(shards, rate, cap, workers, db, buckets, only=None):
    ensure_tables(db)
    out = run_dir(rate, cap)
    (out / "pairs").mkdir(parents=True, exist_ok=True)
    ready = [k for k in shards if shard_ready(k, rate)]
    missing = sorted(set(shards) - set(ready))
    if missing:
        print(f"not ready (still streaming or not thinned): shards {missing}; running the rest", flush=True)
    epoch_file = out / "EPOCH.json"
    epoch = json.loads(epoch_file.read_text()) if epoch_file.exists() else None
    if not epoch or epoch["shards"] != ready:
        print(f"frequency pass over shards {ready}", flush=True)
        epoch = freq(ready, rate, cap, workers, db)
    tag = ",".join(map(str, ready))
    runname = run_dir(rate, cap).name.removeprefix("blocks_")
    done = {(r["shard_i"], r["shard_j"]) for r in db.execute(
        "select shard_i, shard_j from pair_blocks where run = ? and status = 'done' and epoch_shards = ?", (runname, tag))}
    todo = [(i, j) for a, i in enumerate(ready) for j in ready[a:] if (i, j) not in done]
    if only:
        todo = [t for t in todo if t in only]
    print(f"{len(todo)} block(s) to run, {len(done)} already done under this frequency pass", flush=True)
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(run_block, (i, j, rate, cap, buckets)): (i, j) for i, j in todo}
        for f in as_completed(futs):
            r = f.result()
            db.execute("insert or replace into pair_blocks values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (runname, r["i"], r["j"], rate, cap, tag, "done", r["pairs"], r["emitted"], r["seconds"],
                        r["peak_rss_mb"], r["spill_gb"], time.strftime("%Y-%m-%d %H:%M:%S")))
            db.commit()
            print(f"block {r['i']}x{r['j']}: {r['pairs']:,} pairs, {r['emitted']:,} occurrences, {r['seconds']}s, "
                  f"{r['peak_rss_mb']} MB, spill {r['spill_gb']} GB", flush=True)


# ------------------------------------------------------------------ common: shared_common, clusters

def common_slices(out, p, max_size=COMMON_LIST_MAX):
    """Partition p's common phrases with full document lists (cap+1..max_size documents):
    (docs memmap, offsets, sizes) restricted to those phrases (offsets index into docs)."""
    h, c, off, docs = load_common(out, p)
    m = np.flatnonzero(c <= max_size)
    return docs, off, c, m


def all_pair_keys(out):
    paths = sorted(glob.glob(str(out / "pairs" / "block_*.npz")))
    keys = [np.load(pth)["keys"] for pth in paths]
    lens = [len(k) for k in keys]
    return paths, (np.concatenate(keys) if keys else np.empty(0, np.uint64)), np.cumsum([0] + lens)


def shared_common(rate, cap, db):
    """shared_common per pair = common phrases (cap+1..1000 documents) both documents are in. Summed one
    hash partition at a time (a phrase lives in exactly one partition), so memory is one partition's
    common store plus the pair keys, at any corpus size."""
    from scipy import sparse
    out = run_dir(rate, cap)
    paths, keys, bounds = all_pair_keys(out)
    a, b = (keys >> np.uint64(32)).astype(np.uint32), (keys & np.uint64(0xFFFFFFFF)).astype(np.uint32)
    sc = np.zeros(len(keys), np.uint32)
    t0 = time.time()
    for p in range(NPARTS):
        docs, off, c, m = common_slices(out, p)
        if not len(m):
            continue
        lens = off[m + 1] - off[m]
        idx = np.concatenate([np.arange(off[g], off[g + 1]) for g in m])
        d = np.asarray(docs[idx])
        doc_ids, dense = np.unique(d, return_inverse=True)
        col = np.repeat(np.arange(len(m), dtype=np.int32), lens)
        X = sparse.csr_matrix((np.ones(len(d), np.uint8), (dense.astype(np.int32), col)),
                              shape=(len(doc_ids), len(m)))
        ia, ib = np.searchsorted(doc_ids, a), np.searchsorted(doc_ids, b)
        ia_c, ib_c = np.minimum(ia, len(doc_ids) - 1), np.minimum(ib, len(doc_ids) - 1)
        hit = np.flatnonzero((doc_ids[ia_c] == a) & (doc_ids[ib_c] == b))
        for s in range(0, len(hit), 500_000):
            ch = hit[s:s + 500_000]
            sc[ch] += np.asarray(X[ia_c[ch]].multiply(X[ib_c[ch]]).sum(axis=1)).ravel().astype(np.uint32)
    for i, pth in enumerate(paths):
        z = dict(np.load(pth))
        z["shared_common"] = sc[bounds[i]:bounds[i + 1]]
        np.savez(pth, **z)
    print(f"shared_common: {len(keys):,} pairs, {int((sc > 0).sum()):,} share a common phrase, "
          f"{round(time.time() - t0)}s, peak {peak_rss_mb()} MB", flush=True)


def clusters(rate, cap, db, min_jaccard=0.8, min_groups=5, n_hash=8, band=2):
    """Template families too big for any pair threshold. Common phrases (cap+1..1000 documents) whose
    document sets are nearly the same (Jaccard >= min_jaccard) are linked; MinHash banding proposes the
    links (n_hash hashes in bands of `band`) and each is then checked exactly. Connected components
    with >= min_groups phrases are families; a family's members are the documents in at least half of
    its phrases. Signatures are built one partition at a time and document lists are memory-mapped,
    so memory is the signatures (n_hash x 8 bytes per phrase), not the documents."""
    from scipy import sparse
    from scipy.sparse.csgraph import connected_components
    ensure_tables(db)
    out = run_dir(rate, cap)
    runname = run_dir(rate, cap).name.removeprefix("blocks_")
    db.execute("delete from phrase_clusters where run = ?", (runname,))
    db.execute("delete from phrase_cluster_summary where run = ?", (runname,))
    t0 = time.time()
    rng = np.random.default_rng(7)
    coef = [(np.uint64(rng.integers(1, 2**61, dtype=np.uint64)) | np.uint64(1),
             np.uint64(rng.integers(0, 2**61, dtype=np.uint64))) for _ in range(n_hash)]
    parts, sigs, sizes_all, src = {}, [], [], []
    for p in range(NPARTS):
        docs, off, c, m = common_slices(out, p)
        if not len(m):
            continue
        parts[p] = (docs, off)
        starts = off[m]
        idx = np.concatenate([np.arange(off[g], off[g + 1]) for g in m])
        d = np.asarray(docs[idx]).astype(np.uint64)
        seg_starts = np.concatenate(([0], np.cumsum(off[m + 1] - off[m])[:-1]))
        sig = np.empty((n_hash, len(m)), np.uint64)
        for i, (mult, add) in enumerate(coef):
            sig[i] = np.minimum.reduceat(d * mult + add, seg_starts)
        sigs.append(sig); sizes_all.append(c[m])
        src.append(np.stack([np.full(len(m), p, np.int64), m.astype(np.int64)], axis=1))
        del d
    if not sigs:
        db.commit()
        return
    sig, sizes, src = np.concatenate(sigs, axis=1), np.concatenate(sizes_all), np.concatenate(src)
    n = len(sizes)

    def doc_list(g):
        p, gi = src[g]
        docs, off = parts[int(p)]
        return np.asarray(docs[off[gi]:off[gi + 1]])

    cand = set()
    for bi in range(0, n_hash, band):
        key = sig[bi].copy()
        for r in range(bi + 1, min(bi + band, n_hash)):
            key = key * GOLDEN ^ sig[r]
        order = np.argsort(key, kind="stable")
        ks = key[order]
        starts = np.concatenate(([0], np.flatnonzero(np.diff(ks)) + 1, [len(ks)]))
        for s0, e0 in zip(starts[:-1], starts[1:]):
            if e0 - s0 > 1:
                # star, not clique: linking every bucket member to its first is enough for connected
                # components, and stays linear in the bucket size (a big family is one big bucket)
                head = int(order[s0])
                for y in order[s0 + 1:e0].tolist():
                    cand.add((head, y) if head < y else (y, head))
    edges = []
    for x, y in cand:
        dx, dy = doc_list(x), doc_list(y)
        inter = len(np.intersect1d(dx, dy, assume_unique=True))
        if inter >= min_jaccard * (len(dx) + len(dy) - inter):
            edges.append((x, y))
    if edges:
        e = np.array(edges)
        link = sparse.coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n))
        _, labels = connected_components(link, directed=False)
    else:
        labels = np.arange(n)
    comp_sizes = np.bincount(labels)
    rows_c, rows_s, dk_cache = [], [], {}
    for cid, comp in enumerate(np.flatnonzero(comp_sizes >= min_groups), 1):
        groups = np.flatnonzero(labels == comp)
        u, cnt = np.unique(np.concatenate([doc_list(g) for g in groups]), return_counts=True)
        keep = cnt >= max(2, len(groups) // 2)
        rows_s.append((runname, cid, len(groups), int(keep.sum()), float(sizes[groups].mean())))
        for gid, cc in zip(u[keep].tolist(), cnt[keep].tolist()):
            k = gid >> SHIFT
            if k not in dk_cache:
                dk_cache[k] = docs_tables(k, rate)[0]
            rows_c.append((runname, cid, dk_cache[k][gid & 0xFFFFFF].decode(), len(groups), cc))
    db.executemany("insert into phrase_cluster_summary values (?,?,?,?,?)", rows_s)
    db.executemany("insert into phrase_clusters values (?,?,?,?,?)", rows_c)
    db.commit()
    print(f"clusters: {n:,} common phrases, {len(cand):,} candidate links, {len(edges):,} confirmed, "
          f"{len(rows_s):,} families of >= {min_groups} phrases, {len(rows_c):,} document rows, "
          f"{round(time.time() - t0)}s, peak {peak_rss_mb()} MB", flush=True)


# ------------------------------------------------------------------ finalize

def finalize(rate, cap, db):
    """pairs <- every block of the run, ranked by containment then shared (gi_pairs.py's order)."""
    out = run_dir(rate, cap)
    db.execute("drop table if exists pairs")
    db.execute("""create table pairs (
        pair_id integer primary key, dkey_a text, dkey_b text, shared integer, sampled_a integer,
        sampled_b integer, pct_a real, pct_b real, containment real, shared_common integer)""")
    db.execute("drop table if exists pairs_stage")
    db.execute("create temp table pairs_stage (dkey_a text, dkey_b text, shared integer, sampled_a integer, "
               "sampled_b integer, pct_a real, pct_b real, containment real, shared_common integer)")
    tables = {}
    n = 0
    for path in sorted(glob.glob(str(out / "pairs" / "block_*.npz"))):
        z = np.load(path)
        keys, counts = z["keys"], z["counts"].astype(np.int64)
        sc = z["shared_common"] if "shared_common" in z.files else np.zeros(len(keys), np.uint32)
        a, b = (keys >> np.uint64(32)).astype(np.int64), (keys & np.uint64(0xFFFFFFFF)).astype(np.int64)
        cols = []
        for side in (a, b):
            ks = side >> SHIFT
            dk = np.empty(len(side), dtype="S40"); sa = np.empty(len(side), np.int64)
            for k in np.unique(ks):
                if k not in tables:
                    tables[k] = docs_tables(int(k), rate)
                m = ks == k
                dk[m] = tables[k][0][side[m] & 0xFFFFFF]; sa[m] = tables[k][1][side[m] & 0xFFFFFF]
            cols.append((dk, sa))
        (dka, sa_), (dkb, sb_) = cols
        cont = counts / np.maximum(1, np.minimum(sa_, sb_))
        db.executemany("insert into pairs_stage values (?,?,?,?,?,?,?,?,?)",
                       zip((x.decode() for x in dka), (x.decode() for x in dkb), counts.tolist(), sa_.tolist(),
                           sb_.tolist(), np.round(100 * counts / np.maximum(1, sa_), 1).tolist(),
                           np.round(100 * counts / np.maximum(1, sb_), 1).tolist(),
                           np.round(cont, 3).tolist(), sc.astype(np.int64).tolist()))
        n += len(keys)
    db.execute("""insert into pairs (dkey_a, dkey_b, shared, sampled_a, sampled_b, pct_a, pct_b, containment,
                  shared_common)
                  select * from pairs_stage order by containment desc, shared desc""")
    db.execute("drop table pairs_stage")
    db.commit()
    print(f"pairs <- {n:,} rows from {out.name}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["docs", "thin", "freq", "run", "common", "clusters", "finalize"])
    ap.add_argument("--rate", type=int, default=8)
    ap.add_argument("--cap", type=int, default=100)
    ap.add_argument("--shards", default="0-15")
    ap.add_argument("--shard", type=int)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--buckets", type=int, default=64)
    ap.add_argument("--only", help="run only these blocks, e.g. 0x0,1x2")
    ap.add_argument("--db", default=str(DATA / "gi.sqlite3"))
    args = ap.parse_args()
    db = sqlite3.connect(args.db, timeout=300)
    db.execute("pragma journal_mode = wal")
    if args.command == "thin":
        thin(args.shard, args.rate)
    elif args.command == "docs":
        for k in parse_shards(args.shards):
            if shard_ready(k, args.rate):
                docs_tables(k, args.rate)
    elif args.command == "freq":
        freq([k for k in parse_shards(args.shards) if shard_ready(k, args.rate)], args.rate, args.cap, args.workers, db)
    elif args.command == "run":
        only = {tuple(int(x) for x in t.split("x")) for t in args.only.split(",")} if args.only else None
        run(parse_shards(args.shards), args.rate, args.cap, args.workers, db, args.buckets, only)
    elif args.command == "common":
        shared_common(args.rate, args.cap, db)
    elif args.command == "clusters":
        clusters(args.rate, args.cap, db)
    elif args.command == "finalize":
        finalize(args.rate, args.cap, db)


if __name__ == "__main__":
    main()
