"""Background false positives of mod-4 sampling: what the shared 5-grams are, and evidence-only recall."""
from collections import Counter
src = open("exp_synthetic.py").read().split("# ---- mod-p sampling ----")[0]
exec(compile(src, "exp_synthetic_setup", "exec"))

text = {}
for p in rows(SRC):
    if p[3] == "5":
        text[h64(p[2])] = p[2]

keys = {n: a[a % np.uint64(4) == 0].tolist() for n, a in arrs.items()}
cnt = {n: len(k) for n, k in keys.items()}
sh = index_pairs(keys)
GRID2 = [(5, 0.0), (10, 0.0), (15, 0.0), (20, 0.0)]
evaluate("mod-4, evidence-only thresholds", sh, lambda a, b, n: 1.0, sum(cnt.values()), GRID2)

bg = sorted(((n / min(cnt[a], cnt[b]), n, a, b) for (a, b), n in sh.items()
             if (a, b) not in plant_pairs and (a, b) not in artifact and n >= 5), reverse=True)
print(f"\nbackground pairs with >=5 shared samples: {len(bg)}")
for c, n, a, b in bg[:12]:
    common = sorted(set(keys[a]) & set(keys[b]))
    print(f"\ncont={c:.2f} shared={n} sizes={cnt[a]}/{cnt[b]}")
    for x in common[:6]:
        print("   ", text.get(x, "?"))
