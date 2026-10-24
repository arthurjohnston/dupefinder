"""Unchecked leads (tiers 2/4/5, no shared author, primary pair) whose LATER paper prints a US/European institution
on its own byline (OpenAlex authorships of that paper, already cached: no name-merge guesswork), with no
author id or surname in common between the two sides. Scores them with the same model as top30_rank.py and
writes useu_ranked.pkl. Read-only on gi.sqlite3; no network."""
import json, math, os, pickle, re, sqlite3, sys, unicodedata
import numpy as np
sys.path.insert(0, "/home/arthur/Sites/dupefinder/general_index")
import gi_preprints as gp
import gi_affiliations as ga
S = sys.argv[1]
db = sqlite3.connect("file:/home/arthur/Sites/dupefinder/general_index/data/gi.sqlite3?mode=ro", uri=True)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "top30_rank.py")).read()   # its imports, feats(), training set and fitted model
exec(src[:src.index("d = pickle.load")].replace("PKL, OUT = sys.argv[1], sys.argv[2]", ""))

TARGET = ga.TARGET_COUNTRIES
plain = lambda t: unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()

def authorships(dois):
    out = {}
    dois = list(dois)
    for i in range(0, len(dois), 500):
        ch = dois[i:i + 500]
        for doi, aid, name, inst in db.execute(
                f"select doi, author_id, name, institutions from oa_authorships where doi in ({','.join('?' * len(ch))})", ch):
            out.setdefault(doi, []).append((aid, name, json.loads(inst or "[]")))
    return out

# candidate leads
leads = db.execute("""select l.pair_id, l.doi_a, l.doi_b, l.pair_lo, l.pair_hi from leads l
    where l.primary_pair = 1 and l.tier in (2, 4, 5) and l.authors = 'no shared author'
    and coalesce(l.abstracts, '') not like 'partner unidentified%' and coalesce(l.abstracts, '') != 'identical (artifact)'
    and not exists (select 1 from fulltext f where f.pair_lo = l.pair_lo and f.pair_hi = l.pair_hi)""").fetchall()
print("unchecked leads:", len(leads), flush=True)
years = pickle.load(open(f"{S}/top30_years.pkl", "rb"))
q = """select l.pair_id, l.doi_a, l.doi_b, l.pair_lo, l.pair_hi, l.tier, l.shape, l.authors, l.abstracts,
        l.abs_run, l.retraction_watch, p.shared, p.pct_a, p.pct_b, p.sampled_a, p.sampled_b,
        da.title, case when da.pub_date like '1970-01-01%' then null else substr(da.pub_date,1,4) end,
        db.title, case when db.pub_date like '1970-01-01%' then null else substr(db.pub_date,1,4) end
        from leads l join pairs p using (pair_id) left join documents da on da.dkey = p.dkey_a
        left join documents db on db.dkey = p.dkey_b where l.pair_id in ({})"""
out, seen = [], 0
for i in range(0, len(leads), 2000):
    ch = leads[i:i + 2000]
    auth = authorships({d.lower() for r in ch for d in (r[1], r[2]) if d})
    keep = [r[0] for r in ch if auth.get((r[1] or "").lower()) and auth.get((r[2] or "").lower())]
    if not keep:
        continue
    for r in db.execute(q.format(",".join("?" * len(keep))), keep):
        r = list(r)
        for di, yi in ((1, 17), (2, 19)):
            if not (r[yi] or "").isdigit():
                r[yi] = years.get((r[di] or "").lower(), r[yi])
        earlier = gp.earlier_by_year(r[17], r[19])
        if earlier is None:
            continue
        later = "b" if earlier == "a" else "a"
        L, E = auth[(r[1 if later == "a" else 2]).lower()], auth[(r[1 if earlier == "a" else 2]).lower()]
        if {a for a, _, _ in L if a} & {a for a, _, _ in E if a}:
            continue
        sur = lambda A: {w for _, n, _ in A for w in re.findall(r"[a-z]{3,}", plain(n))}
        if sur(L) & sur(E):
            continue
        printed = [(n, x.get("name"), x.get("country")) for _, n, ins in L for x in ins
                   if x.get("country") in TARGET]
        if not printed:
            continue
        acad = [p for p in printed if ga.ACADEMIC_NAME.search(p[1] or "")]
        out.append({"pair_id": r[0], "p": None, "row": tuple(r), "later": later, "printed": printed,
                    "academic": bool(acad), "a": (r[1], r[17], r[16]), "b": (r[2], r[19], r[18]), "tier": r[5],
                    "pct": (r[12], r[13])})
    seen += len(ch)
    if seen % 100000 < 2000:
        print(f"  {seen:,} leads read, {len(out):,} kept", flush=True)
X = np.array([feats(o["row"]) for o in out], float)
for o, p in zip(out, model.predict_proba(X)[:, 1]):
    o["p"] = float(p)
    del o["row"]
out.sort(key=lambda o: -o["p"])
pickle.dump(out, open(f"{S}/useu_ranked.pkl.tmp", "wb"))
os.replace(f"{S}/useu_ranked.pkl.tmp", f"{S}/useu_ranked.pkl")   # run_useu_loop.sh may be reading it
print("kept:", len(out), "| academic institution printed:", sum(o["academic"] for o in out),
      "| p>=0.3:", sum(o["p"] >= 0.3 for o in out), "| p>=0.5:", sum(o["p"] >= 0.5 for o in out))
