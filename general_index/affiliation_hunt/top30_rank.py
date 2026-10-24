"""Train P(cross-author/data copying) on full-text-checked pairs from lead features; score the top-30 pairs."""
import sqlite3, pickle, sys, math, json, collections
import numpy as np
sys.path.insert(0, "/home/arthur/Sites/dupefinder/general_index")
import gi_preprints as gp
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_predict, StratifiedKFold
from sklearn.metrics import roc_auc_score

PKL, OUT = sys.argv[1], sys.argv[2]
db = sqlite3.connect("file:/home/arthur/Sites/dupefinder/general_index/data/gi.sqlite3?mode=ro", uri=True)
ABS = ["not checked", "shared text", "no shared text", "partner unidentified", "identical"]

def feats(r):
    (pid, da, dbb, lo, hi, tier, shape, authors, abstracts, abs_run, rw, shared, pa, pb, sa, sb,
     ta, ya, tb, yb) = r[:20]
    pa, pb = pa or 0, pb or 0
    years = (ya or "").isdigit() and (yb or "").isdigit()
    earlier = gp.earlier_by_year(ya, yb)
    prio = gp.pair_priority(da, dbb, earlier)[0]
    real = lambda d: (d or "").startswith("10.")
    ab = (abstracts or "").lower()
    return [tier == 2, tier == 4, tier == 5, shape == "two-sided", authors == "no shared author",
            *[ab.startswith(a) for a in ABS], math.log1p(abs_run or 0), rw is not None,
            math.log1p(shared or 0), pa, pb, max(pa, pb), min(pa, pb), math.log1p(sa or 0), math.log1p(sb or 0),
            years, abs(int(ya) - int(yb)) if years else 0, years and ya == yb, real(da) and real(dbb),
            prio == gp.PROMOTED, prio == gp.POSSIBLY_PROMOTED, prio == gp.POSSIBLY, prio == gp.LATER]

train = db.execute("""select l.pair_id, l.doi_a, l.doi_b, l.pair_lo, l.pair_hi, l.tier, l.shape, l.authors, l.abstracts,
        l.abs_run, l.retraction_watch, p.shared, p.pct_a, p.pct_b, p.sampled_a, p.sampled_b,
        da.title, case when da.pub_date like '1970-01-01%' then null else substr(da.pub_date,1,4) end,
        db.title, case when db.pub_date like '1970-01-01%' then null else substr(db.pub_date,1,4) end,
        f.ft_verdict, f.byline, coalesce(r.decision, a.decision), coalesce(r.type, a.type)
        from fulltext f join leads l on l.pair_lo = f.pair_lo and l.pair_hi = f.pair_hi and l.primary_pair = 1
        join pairs p using (pair_id) left join documents da on da.dkey = p.dkey_a left join documents db on db.dkey = p.dkey_b
        left join reviews r on r.pair_lo = f.pair_lo and r.pair_hi = f.pair_hi
        left join agent_reviews a on a.agent_review_id = (select agent_review_id from agent_reviews x
            where x.pair_lo = f.pair_lo and x.pair_hi = f.pair_hi and x.type is not null
            order by x.model like 'local:%', x.agent_review_id desc limit 1)
        where f.ft_verdict in ('real overlap', 'no real overlap') and l.tier not in (1, 3)""").fetchall()
X = np.array([feats(r) for r in train], float)
y = np.array([r[20] == "real overlap" and r[21] == "no byline overlap" and r[22] == "problem"
              and r[23] in ("cross-author copying", "data copying") for r in train], int)
print(f"training pairs {len(y)}, positive (cross-author/data copying) {y.sum()}")
cv = StratifiedKFold(5, shuffle=True, random_state=0)
for name, m in [("logistic", LogisticRegression(max_iter=5000, C=1.0)),
                ("boosted trees", GradientBoostingClassifier(random_state=0))]:
    p = cross_val_predict(m, X, y, cv=cv, method="predict_proba")[:, 1]
    top = np.argsort(-p)[:len(p) // 10]
    print(f"{name}: cross-validated AUC {roc_auc_score(y, p):.3f}; top-10% precision {y[top].mean():.2f} "
          f"(base rate {y.mean():.2f})")
model = GradientBoostingClassifier(random_state=0).fit(X, y)

d = pickle.load(open(PKL, "rb"))
years = pickle.load(open(sys.argv[3], "rb"))
def fill(r):
    r = list(r)
    for di, yi in ((1, 17), (2, 19)):
        if not (r[yi] or "").isdigit():
            r[yi] = years.get((r[di] or "").lower(), r[yi])
    return tuple(r)
d["rows"] = [fill(r) for r in d["rows"]]
hits, ft = d["doi_hits"], d["ft"]
out = []
for r in d["rows"]:
    pid, da, dbb, lo, hi, tier = r[:6]
    if tier in (1, 3):
        continue
    ya, yb = r[17], r[19]
    earlier = gp.earlier_by_year(ya, yb)
    if earlier is None:
        continue                         # can't tell which paper copied
    sides = ["b"] if earlier == "a" else ["a"]
    copy = [(s, hits.get((da if s == "a" else dbb or "").lower())) for s in sides]
    copy = [(s, h) for s, h in copy if h]
    if not copy:
        continue                         # the top-30 author is on the earlier (copied-from) paper only
    f = ft.get((lo, hi))
    out.append({"pair_id": pid, "p": float(model.predict_proba(np.array([feats(r)], float))[0, 1]),
                "tier": tier, "pct": (r[12], r[13]), "shared": r[11], "abstracts": r[8], "authors_flag": r[7],
                "a": (da, ya, r[16]), "b": (dbb, yb, r[18]), "order_known": earlier is not None,
                "copy_side": [s for s, _ in copy], "top30": [h for _, h in copy],
                "fulltext": f[1:] if f else None})
out.sort(key=lambda o: -o["p"])
pickle.dump(out, open(OUT, "wb"))
print("top-30 copying-side pairs:", len(out), "| order known:", sum(o["order_known"] for o in out),
      "| already full-text:", sum(o["fulltext"] is not None for o in out))
