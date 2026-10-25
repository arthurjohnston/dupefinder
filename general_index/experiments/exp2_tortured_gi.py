"""Tortured-phrase lookup against real GI n-gram rows (two downloaded chunks, ~5.4k partial docs),
next to the legitimate phrases they replace, plus per-row CPU cost of stream-time additions."""
import hashlib, os, sqlite3, sys, time
from collections import Counter, defaultdict
from inflate import inflate_head

TORTURED = {  # lemmatized/lowercased as GI stores them; legit original in the value
    "counterfeit consciousness": "artificial intelligence", "profound learning": "deep learning",
    "irregular woodland": "random forest", "arbitrary woodland": "random forest", "irregular backwoods": "random forest",
    "colossal information": "big data", "neural organization": "neural network", "fake neural organization": "artificial neural network",
    "versatile organization": "mobile network", "remote organization": "wireless network", "mean square blunder": "mean square error",
    "bosom peril": "breast cancer", "bosom malignant growth": "breast cancer", "kidney disappointment": "kidney failure",
    "choice tree": "decision tree", "cloud figuring": "cloud computing", "picture handling": "image processing",
    "credulous bayes": "naive bayes", "flag handling": "signal processing", "man make consciousness": "artificial intelligence",
    "information mining": "data mining", "web of thing": "internet of thing", "huge information": "big data",
    "worldwide positioning framework": "global positioning system", "lung malignant growth": "lung cancer",
    "malignant growth": "cancer", "subterranean insect state": "ant colony", "molecule swarm": "particle swarm",
}
LEGIT = set(TORTURED.values())
hits, legit, docs = defaultdict(set), defaultdict(set), set()
cpu = {}
for chunk in ("ng0_head.zip", "ng3_head.zip"):
    text = inflate_head(chunk)
    lines = [l.encode() for l in text.split("\n")]
    for l in lines:
        p = l.split(b"\t")
        if len(p) != 8 or len(p[0]) != 40:
            continue
        docs.add(p[0])
        if p[3] in (b"2", b"3", b"4"):
            g = p[2].decode("utf-8", "replace")
            if g in TORTURED:
                hits[g].add(p[0])
            elif g in LEGIT:
                legit[g].add(p[0])
    # CPU cost per row of each stream-time addition (same inner loop as gi_sample_5grams.py)
    if chunk == "ng0_head.zip":
        ts = {t.encode() for t in TORTURED}
        canon = {}
        def run(mode):
            t0 = time.process_time(); n = 0
            for l in lines:
                p = l.split(b"\t")
                if len(p) != 8:
                    continue
                if mode >= 1 and p[3] in (b"2", b"3", b"4") and p[2] in ts:
                    n += 1
                if p[3] != b"5":
                    continue
                h = int.from_bytes(hashlib.blake2b(p[2], digest_size=8).digest(), "little")
                if h % 8 == 0:
                    n += 1
                if mode >= 2:
                    k = b" ".join(canon.setdefault(w, w) for w in p[2].split())
                    h2 = int.from_bytes(hashlib.blake2b(k, digest_size=8).digest(), "little")
                    n += h2 % 8 == 0
            return time.process_time() - t0
        base = run(0); cpu = {"baseline": base, "+tortured lookup": run(1), "+second normalized 5-gram hash": run(2)}
db = sqlite3.connect("file:../data/gi_meta.sqlite3?mode=ro", uri=True)
print(f"{len(docs):,} docs (partial) scanned")
for g, ds in sorted(hits.items(), key=lambda x: -len(x[1])):
    yrs = Counter()
    for d in ds:
        r = db.execute("select substr(pub_date,1,4), journal, title from meta where dkey=?", (d.decode(),)).fetchone()
        yrs[r[0] if r else "no-meta"] += 1
    print(f"  TORTURED {g!r:<32} {len(ds):>3} docs  (legit {TORTURED[g]!r}: {len(legit.get(TORTURED[g], ()))} docs)  years {dict(yrs)}")
print(f"tortured phrases with no hit: {sorted(set(TORTURED) - set(hits))}")
print("\nCPU seconds for the same 100MB chunk:", {k: round(v, 2) for k, v in cpu.items()},
      " -> relative:", {k: f"{v / cpu['baseline']:.2f}x" for k, v in cpu.items()})
