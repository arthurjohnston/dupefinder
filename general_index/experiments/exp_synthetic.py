"""Synthetic recall/precision test of bucketing schemes on real General Index 5-gram sets.

Docs are the (partial) 5-gram sets found in the first 150MB of ngram slice 3. Planted pairs:
  T1 republication   : copy of a donor with 10% of its 5-grams swapped for another doc's
  T2 partial reuse   : S donor 5-grams (S = 25/50/100, "a few paragraphs") added to a host
  T3 short-into-long : a short doc (60-200 5-grams) embedded whole in a long doc (>= 2000)
Each host is replaced by its planted version, so background pairs are all real.

Methods (each: an inverted index on per-doc keys, then a pair score):
  mod-p      : keep hashes with h % p == 0 (current pipeline); score containment over samples
  capped-crs : mod-4 but at most M kept per doc (smallest hashes) -- conditional random sampling
  minhash r=1: k independent minima, any shared minimum = candidate (upper bound for MinHash banding)
  minhash b/r: classic banding
Detection = candidate AND evidence >= e_min AND containment estimate >= c_min.
"""
import hashlib
import random
import sys
from collections import defaultdict
from itertools import combinations

import numpy as np

from inflate import rows

rng = random.Random(7)
SRC = sys.argv[1] if len(sys.argv) > 1 else "ng3_head.zip"


def h64(s):
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little")


docs = defaultdict(set)
for p in rows(SRC):
    if p[3] == "5":
        docs[p[0]].add(h64(p[2]))
docs = {k: v for k, v in docs.items() if len(v) >= 50}
names = list(docs)
rng.shuffle(names)
sizes = {k: len(v) for k, v in docs.items()}
print(f"{len(docs)} docs with >=50 5-grams; median size {sorted(sizes.values())[len(sizes)//2]}, "
      f"max {max(sizes.values())}")

# ---- plant ----
used, planted = set(), []  # (label, doc_a, doc_b)
artifact = set()  # (other, T1 copy): the swapped-in 10% makes these overlap too
pool = [n for n in names if 200 <= sizes[n] <= 3000]


def take(cands):
    for n in cands:
        if n not in used:
            used.add(n)
            return n
    raise RuntimeError("pool exhausted")


corpus = dict(docs)
for i in range(40):  # T1
    d, other = take(pool), take(pool)
    src = list(docs[d])
    k = len(src) // 10
    new = set(rng.sample(src, len(src) - k)) | set(rng.sample(list(docs[other]), min(k, len(docs[other]))))
    corpus[f"T1copy{i}"] = new
    planted.append(("T1 republication", d, f"T1copy{i}"))
    artifact.add(tuple(sorted((other, f"T1copy{i}"))))
for S in (25, 50, 100):  # T2
    for i in range(40):
        d, host = take(pool), take(pool)
        if sizes[d] < S:
            continue
        corpus[host] = docs[host] | set(rng.sample(list(docs[d]), S))
        planted.append((f"T2 partial S={S}", d, host))
shorts = [n for n in names if 60 <= sizes[n] <= 200]
longs = sorted((n for n in names if sizes[n] >= 2000), key=lambda n: -sizes[n])
for i in range(min(40, len(longs))):  # T3
    s, l = take(shorts), take(longs)
    corpus[l] = docs[l] | docs[s]
    planted.append(("T3 short-into-long", s, l))
plant_pairs = {tuple(sorted((a, b))) for _, a, b in planted}
print(f"planted {len(planted)} pairs; corpus {len(corpus)} docs")

ids = {n: i for i, n in enumerate(corpus)}
arrs = {n: np.fromiter(v, dtype=np.uint64) for n, v in corpus.items()}
true_size = {n: len(v) for n, v in corpus.items()}


def index_pairs(keys_of, cap=20):
    """keys_of: doc -> iterable of keys. Returns {(a,b): shared key count} for groups <= cap."""
    inv = defaultdict(list)
    for n, ks in keys_of.items():
        for k in ks:
            inv[k].append(n)
    shared = defaultdict(int)
    for members in inv.values():
        if 2 <= len(members) <= cap:
            for a, b in combinations(sorted(members), 2):
                shared[(a, b)] += 1
    return shared


def evaluate(name, shared, cont_fn, storage, grid):
    out = []
    for e_min, c_min in grid:
        hits = defaultdict(lambda: [0, 0])
        fp = 0
        for (a, b), n in shared.items():
            if n < e_min:
                continue
            ok = cont_fn(a, b, n) >= c_min
            if (a, b) in plant_pairs or (a, b) in artifact:
                continue
            fp += ok
        for label, a, b in planted:
            key = tuple(sorted((a, b)))
            n = shared.get(key, 0)
            hits[label][1] += 1
            if n >= e_min and cont_fn(*key, n) >= c_min:
                hits[label][0] += 1
        out.append((e_min, c_min, fp, {k: f"{v[0]}/{v[1]}" for k, v in sorted(hits.items())}))
    print(f"\n== {name}  (records stored: {storage:,}, {storage / len(corpus):.0f}/doc)")
    for e_min, c_min, fp, h in out:
        print(f"  evidence>={e_min:<3} cont>={c_min:<4}  background FP={fp:<5} " +
              "  ".join(f"{k.split()[0]}{k.split()[-1] if 'S=' in k else ''}:{v}" for k, v in h.items()))


GRID = [(3, 0.1), (3, 0.2), (5, 0.2), (10, 0.2), (10, 0.5)]

# ---- mod-p sampling ----
for p in (4, 16):
    keys = {n: a[a % np.uint64(p) == 0].tolist() for n, a in arrs.items()}
    cnt = {n: len(k) for n, k in keys.items()}
    sh = index_pairs(keys)
    evaluate(f"mod-{p} sampling (current design)", sh,
             lambda a, b, n, cnt=cnt: n / max(1, min(cnt[a], cnt[b])), sum(cnt.values()), GRID)

# ---- capped conditional random sampling (mod-4, at most M per doc) ----
for M in (128, 256):
    keys, tau = {}, {}
    for n, a in arrs.items():
        s = np.sort(a[a % np.uint64(4) == 0])
        if len(s) > M:
            tau[n] = int(s[M - 1])
            s = s[:M]
        else:
            tau[n] = 2 ** 64 - 1
        keys[n] = s.tolist()
    sh_raw = index_pairs(keys)
    sh = {}
    for (a, b), _ in sh_raw.items():  # recount below the common threshold
        t = min(tau[a], tau[b])
        ka = set(x for x in keys[a] if x <= t)
        kb = set(x for x in keys[b] if x <= t)
        sh[(a, b)] = (len(ka & kb), len(ka), len(kb))
    cont = lambda a, b, n, sh=sh: sh[(a, b)][0] / max(1, min(sh[(a, b)][1], sh[(a, b)][2])) if (a, b) in sh else 0
    evaluate(f"capped CRS mod-4, M={M}", {k: v[0] for k, v in sh.items()}, cont,
             sum(len(k) for k in keys.values()), GRID)

# ---- MinHash ----
MASK = np.uint64(2 ** 64 - 1)
for k in (128, 256):
    r_ = np.random.default_rng(1)
    A = r_.integers(1, 2 ** 63, size=k, dtype=np.uint64) | np.uint64(1)
    B = r_.integers(0, 2 ** 63, size=k, dtype=np.uint64)
    sig = {}
    with np.errstate(over="ignore"):
        for n, a in arrs.items():
            x = a[None, :] * A[:, None] + B[:, None]
            x ^= x >> np.uint64(29)
            sig[n] = x.min(axis=1)
    for b_, r in ((k, 1), (k // 2, 2), (k // 4, 4)):
        keys = {n: [(i, s[i * r:(i + 1) * r].tobytes()) for i in range(b_)] for n, s in sig.items()}
        cand = index_pairs(keys, cap=20)
        sh = {pr: int((sig[pr[0]] == sig[pr[1]]).sum()) for pr in cand}

        def cont(a, b, n, k=k):
            j = n / k
            sa, sb = true_size[a], true_size[b]
            return j * (sa + sb) / ((1 + j) * min(sa, sb))

        evaluate(f"MinHash k={k} bands={b_} r={r}", sh, cont, k * len(corpus),
                 [(e, c) for e, c in GRID if e <= 10])
