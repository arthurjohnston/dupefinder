"""Evaluate exact / fuzzy (3-gram) / normalized (WordNet, word-class, SimHash) keys, plus chaining.

For each key scheme: every doc -> set of keys. Background = every pair among the 1,200 real docs
(719,400 pairs), counted through an inverted index that ignores keys present in > CAP docs (the
analog of the production group cap). Recall = plants whose (donor, planted doc) shared-key count
exceeds the background maximum, i.e. zero false positives among 719k real pairs.
"""
import hashlib
import os
import pickle
import sys
from collections import Counter, defaultdict
from itertools import combinations

os.environ.setdefault("NLTK_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nltk_data"))
import numpy as np  # noqa: E402
from nltk.corpus import wordnet as wn  # noqa: E402

import gi_emulate  # noqa: E402

CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 10
E = pickle.load(open("emulated.pkl", "rb"))
orig, plants = E["orig"], E["plants"]
KINDS = ["verbatim", "edit", "spin30_wn", "spin60_wn", "spin50_vec", "doc_spin50"]
nlp = gi_emulate.nlp()
STOP = nlp.Defaults.stop_words
V = nlp.vocab.vectors


def h64(s):
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little")


# ---------- word normalizers ----------
_wn1, _wnmin = {}, {}


def wn_first(w):
    if w not in _wn1:
        ss = wn.synsets(w)
        _wn1[w] = ss[0].lemmas()[0].name().lower() if ss else w
    return _wn1[w]


def wn_min(w):
    if w not in _wnmin:
        names = [l.name().lower() for s in wn.synsets(w) for l in s.lemmas()]
        _wnmin[w] = min(names) if names else w
    return _wnmin[w]


_row = {}


def vrow(w):
    if w not in _row:
        r = V.find(key=nlp.vocab.strings[w]) if w in nlp.vocab.strings else -1
        _row[w] = int(r) if r is not None and r >= 0 else -1
    return _row[w]


def kmeans(X, k, iters=12, seed=0):
    r = np.random.default_rng(seed)
    C = X[r.choice(len(X), k, replace=False)].copy()
    for _ in range(iters):
        sim = X @ C.T
        a = sim.argmax(1)
        for j in range(k):
            m = a == j
            if m.any():
                C[j] = X[m].mean(0)
        C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-9
    return (X @ C.T).argmax(1)


Xv = V.data.astype(np.float32)
Xv /= np.linalg.norm(Xv, axis=1, keepdims=True) + 1e-9
CLS = {k: kmeans(Xv, k) for k in (500, 2000, 8000)}
print(f"vector table: {len(Xv)} rows; clustered into 500/2000/8000 classes", flush=True)
PLANES = np.random.default_rng(5).standard_normal((32, Xv.shape[1])).astype(np.float32)


def wordclass(k):
    def f(w):
        if w in STOP:
            return w
        r = vrow(w)
        return f"#{CLS[k][r]}" if r >= 0 else w
    return f


def simhash_key(gram, bits):
    vs = [Xv[vrow(w)] for w in gram.split() if w not in STOP and vrow(w) >= 0]
    if not vs:
        return gram
    s = (PLANES[:bits] @ np.mean(vs, axis=0)) > 0
    return "s" + "".join("1" if b else "0" for b in s)


def norm_keys(fn):
    return lambda doc: {h64(" ".join(fn(w) for w in g.split())) for _, g in doc["grams"][5]}


SCHEMES = {
    "exact 5-gram": lambda d: {h64(g) for _, g in d["grams"][5]},
    "exact 5-gram, mod-8 sample": lambda d: {x for x in (h64(g) for _, g in d["grams"][5]) if x % 8 == 0},
    "exact 3-gram rows": lambda d: {h64(g) for _, g in d["grams"][3]},
    "exact 4-gram rows": lambda d: {h64(g) for _, g in d["grams"][4]},
    "WordNet first-synset 5-gram": norm_keys(lambda w: w if w in STOP else wn_first(w)),
    "WordNet min-lemma 5-gram": norm_keys(lambda w: w if w in STOP else wn_min(w)),
    "word-class k=8000 5-gram": norm_keys(wordclass(8000)),
    "word-class k=2000 5-gram": norm_keys(wordclass(2000)),
    "word-class k=500 5-gram": norm_keys(wordclass(500)),
    "SimHash-24 of 5-gram vector": lambda d: {h64(simhash_key(g, 24)) for _, g in d["grams"][5]},
    "SimHash-16 of 5-gram vector": lambda d: {h64(simhash_key(g, 16)) for _, g in d["grams"][5]},
}


RELATED = None  # real overlap pairs already in the corpus (>= 15 shared exact 5-grams): not background


def evaluate(name, fn):
    global RELATED
    K = [fn(d) for d in orig]
    P = [(kind, dnr, fn(e)) for kind, dnr, e in plants]
    df = Counter()
    for s in K:
        df.update(s)
    inv = defaultdict(list)
    for i, s in enumerate(K):
        for x in s:
            if df[x] <= CAP:
                inv[x].append(i)
    shared = Counter()
    for m in inv.values():
        if len(m) >= 2:
            for a, b in combinations(m, 2):
                shared[(a, b)] += 1
    if RELATED is None:
        RELATED = {p for p, n in shared.items() if n >= 15}
        print(f"excluding {len(RELATED)} real-overlap corpus pairs (>=15 shared exact 5-grams) from background")
    bg = np.array(sorted(n for p, n in shared.items() if p not in RELATED), dtype=np.int64)
    bmax = int(bg.max()) if len(bg) else 0
    p9999 = int(np.percentile(np.concatenate((bg, np.zeros(719400 - len(bg), np.int64))), 99.99))
    keys_per_doc = np.mean([len(s) for s in K])
    over_cap = sum(1 for x, c in df.items() if c > CAP) / max(1, len(df))
    res = defaultdict(lambda: [0, 0, []])
    for kind, dnr, s in P:
        n = sum(1 for x in (K[dnr] & s) if df[x] + 1 <= CAP + 1)
        res[kind][1] += 1
        res[kind][2].append(n)
        res[kind][0] += n > bmax
    line = (f"{name:<30} keys/doc {keys_per_doc:6.0f}  keys>cap {over_cap:5.1%}  bg pairs>0 {len(bg):>7,}  "
            f"bg p99.99 {p9999:>3}  bg max {bmax:>4} |")
    for k in KINDS:
        hit, tot, ns = res[k]
        line += f" {k}:{hit}/{tot}(med {int(np.median(ns))})"
    print(line, flush=True)
    return K, P, df


for name, fn in SCHEMES.items():
    out = evaluate(name, fn)
    if name == "exact 5-gram":
        K5 = out

# ---------- (a) reassembly / chaining ----------
print("\n=== chaining: within-document overlap graph on the bag of 5-grams (no positions) ===")
stats = defaultdict(list)
for d in orig[:300]:
    pos = sorted(d["grams"][5])
    grams = sorted({g for _, g in pos})
    toks = [g.split() for g in grams]
    for k in (4, 3, 2):
        pre = defaultdict(set)
        for g, t in zip(grams, toks):
            pre[" ".join(t[:k])].add(g)
        outdeg = [len(pre.get(" ".join(t[-k:]), set()) - {g}) for g, t in zip(grams, toks)]
        stats[f"k={k} outdeg0"].append(np.mean([o == 0 for o in outdeg]))
        stats[f"k={k} outdeg1"].append(np.mean([o == 1 for o in outdeg]))
        stats[f"k={k} outdeg2+"].append(np.mean([o >= 2 for o in outdeg]))
    # true successor gaps: start-offset between consecutive surviving 5-gram windows
    starts = [s for s, _ in pos]
    gaps = np.diff(starts)
    for g in (1, 2, 3, 4):
        stats[f"gap={g}"].append(np.mean(gaps == g) if len(gaps) else 0)
    stats["gap>=5"].append(np.mean(gaps >= 5) if len(gaps) else 0)
    ntok = sum(1 for t in d["tokens"] if not t[2])
    stats["5-grams per 100 tokens"].append(100 * len(pos) / max(1, ntok))
    # runs recoverable by chaining with overlap >= k  <=> successive gaps <= 5-k
    for k in (4, 3, 2):
        maxgap = 5 - k
        runs, cur = [], 1
        for gp in gaps:
            if gp <= maxgap:
                cur += 1
            else:
                runs.append(cur)
                cur = 1
        runs.append(cur)
        stats[f"true run len (5-grams) chaining k>={k} median"].append(np.median(runs))
        stats[f"true run len (5-grams) chaining k>={k} p90"].append(np.percentile(runs, 90))
for k, v in stats.items():
    print(f"  {k:<48} {np.mean(v):.3f}")


def chain_component(grams):
    """Largest weakly connected component of the overlap graph (k>=2) over a set of 5-grams."""
    grams = list(grams)
    if not grams:
        return 0
    parent = list(range(len(grams)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    toks = [g.split() for g in grams]
    for k in (4, 3, 2):
        pre = defaultdict(list)
        for i, t in enumerate(toks):
            pre[" ".join(t[:k])].append(i)
        for i, t in enumerate(toks):
            for j in pre.get(" ".join(t[-k:]), []):
                if j != i:
                    parent[find(i)] = find(j)
    return Counter(find(i) for i in range(len(grams))).most_common(1)[0][1]


print("\n=== chaining across documents: largest chained cluster of SHARED 5-grams ===")
gram_sets = [{g for _, g in d["grams"][5]} for d in orig]
df5 = Counter()
for s in gram_sets:
    df5.update(s)
bg_pairs = [p for p, n in Counter(
    pr for m in [[i for i in ids] for ids in
                 (lambda inv: inv.values())(
                     (lambda: (lambda inv: [inv[g].append(i) or None for i, s in enumerate(gram_sets) for g in s if df5[g] <= CAP] and inv)(defaultdict(list)))())]
    if len(m) >= 2 for pr in combinations(m, 2)).items() if n >= 2 and p not in RELATED]
bg_chain = [chain_component({g for g in gram_sets[a] & gram_sets[b] if df5[g] <= CAP}) for a, b in bg_pairs]
bg_shared = [len({g for g in gram_sets[a] & gram_sets[b] if df5[g] <= CAP}) for a, b in bg_pairs]
print(f"  background pairs with >=2 shared 5-grams: {len(bg_pairs)}; largest chained cluster max "
      f"{max(bg_chain) if bg_chain else 0}, p99 {np.percentile(bg_chain, 99) if bg_chain else 0:.0f}; "
      f"shared-count max {max(bg_shared) if bg_shared else 0}")
for kind in KINDS:
    cs, ns = [], []
    for k2, dnr, e in plants:
        if k2 != kind:
            continue
        sh = gram_sets[dnr] & {g for _, g in e["grams"][5]}
        ns.append(len(sh))
        cs.append(chain_component(sh))
    bmc = max(bg_chain) if bg_chain else 0
    print(f"  {kind:<11} shared med {np.median(ns):5.0f}  chained-cluster med {np.median(cs):5.0f}  "
          f"chain>{bmc}: {sum(c > bmc for c in cs)}/{len(cs)}   shared>{max(bg_shared) if bg_shared else 0}: "
          f"{sum(n > (max(bg_shared) if bg_shared else 0) for n in ns)}/{len(ns)}")
