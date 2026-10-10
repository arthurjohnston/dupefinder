"""High-overlap pairs in a real chunk: how many are one work twice (same DOI / same title)?"""
import hashlib, html, re, sqlite3
from collections import defaultdict, Counter
from itertools import combinations
from inflate import rows

docs = defaultdict(set)
for p in rows("ng0_head.zip"):
    if p[3] == "5":
        h = int.from_bytes(hashlib.blake2b(p[2].encode(), digest_size=8).digest(), "little")
        if h % 4 == 0:
            docs[p[0]].add(h)
inv = defaultdict(list)
for d, hs in docs.items():
    for h in hs:
        inv[h].append(d)
shared = Counter()
for m in inv.values():
    if 2 <= len(m) <= 20:
        for a, b in combinations(sorted(m), 2):
            shared[(a, b)] += 1
db = sqlite3.connect("file:../data/gi_meta.sqlite3?mode=ro", uri=True)
meta = lambda k: db.execute("select doi, title, journal, pub_date from meta where dkey=?", (k,)).fetchone()
norm = lambda t: " ".join(re.sub(r"[^a-z0-9 ]+", " ", html.unescape(t or "").lower()).split())
cats = Counter()
print(f"{len(docs)} docs; pairs with >=10 shared samples:")
for (a, b), n in sorted(shared.items(), key=lambda x: -x[1]):
    if n < 10:
        break
    c = n / min(len(docs[a]), len(docs[b]))
    ma, mb = meta(a), meta(b)
    if not (ma and mb):
        cat = "no metadata"
    elif ma[0] and mb[0] and ma[0].lower() == mb[0].lower():
        cat = "same DOI"
    elif norm(ma[1]) and norm(ma[1]) == norm(mb[1]):
        cat = "same title"
    else:
        cat = "different work"
    cats[cat] += 1
    if cat == "different work" or cats[cat] <= 2:
        print(f"  [{cat}] cont={c:.2f} shared={n}\n     A {ma}\n     B {mb}")
print(dict(cats))
