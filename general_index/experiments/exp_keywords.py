"""Do YAKE keyword sets separate real text-overlap pairs from unrelated pairs? Uses slice-3 head chunks of
both tables; only docs present in both are usable."""
import hashlib, random
from collections import defaultdict, Counter
from itertools import combinations
from inflate import rows
ng = defaultdict(set)
for p in rows("ng3_head.zip"):
    if p[3] == "5":
        h = int.from_bytes(hashlib.blake2b(p[2].encode(), digest_size=8).digest(), "little")
        if h % 4 == 0:
            ng[p[0]].add(h)
kw = defaultdict(set)
for p in rows("kw3_head.zip"):
    if p[3] in ("2", "3"):  # multi-word keywords only
        kw[p[0]].add(p[2])
both = [d for d in ng if d in kw and len(ng[d]) >= 20]
print(f"ngram docs {len(ng)}, keyword docs {len(kw)}, in both: {len(both)}")
inv = defaultdict(list)
for d in both:
    for h in ng[d]:
        inv[h].append(d)
sh = Counter()
for m in inv.values():
    if 2 <= len(m) <= 20:
        for a, b in combinations(sorted(m), 2):
            sh[(a, b)] += 1
J = lambda a, b: len(kw[a] & kw[b]) / max(1, len(kw[a] | kw[b]))
over = [(a, b) for (a, b), n in sh.items() if n >= 10]
rng = random.Random(1)
rand = [tuple(rng.sample(both, 2)) for _ in range(5000)]
import statistics as st
print(f"text-overlap pairs (>=10 shared samples): {len(over)}")
if over:
    js = sorted(J(a, b) for a, b in over)
    print("  keyword Jaccard of overlap pairs:", [round(x, 3) for x in js])
jr = sorted(J(a, b) for a, b in rand)
print("  random pairs keyword Jaccard p50/p99/p99.9/max:", round(jr[2500], 3), round(jr[4950], 3), round(jr[4995], 3), round(jr[-1], 3))
