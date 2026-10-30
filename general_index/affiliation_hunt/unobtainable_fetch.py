"""The unobtainable pool: full-text-attempted pairs (tiers 2/4/5) that didn't get both PDFs and where a missing side has
no reachable copy (no_oa, 404/410, DOI not found/unknown to Unpaywall -- not 403s or landing pages, which a browser
usually gets). Pickles it to D/unobtainable_pool.pkl (the join takes ~10 min; delete the file to rebuild) and
fetches OpenAlex authorships for every DOI in it into gi.sqlite3's oa_works/oa_authorships (gi_affiliations.Fetcher:
50 DOIs per call, cached, nothing refetched). 2026-10-27: 62,237 pairs, 96,152 DOIs, 616 calls.
Usage: unobtainable_fetch.py D [MAX_CALLS]   (D: data/affiliation_hunt)"""
import logging, os, pickle, re, sqlite3, sys
G = "/home/arthur/Sites/dupefinder/general_index"
sys.path.insert(0, G); os.chdir(G)
import gi_affiliations as ga
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
OUT = os.path.join(sys.argv[1], "unobtainable_pool.pkl")
MAX_CALLS = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
db = sqlite3.connect("data/gi.sqlite3", timeout=120)
DEAD = {"no_oa", "doi_not_found", "doi_unknown_to_unpaywall"}
def dead(p):
    return p is None or p["status"] in DEAD or (p["status"] == "error" and re.search(r"HTTP (404|410)", p["error"] or ""))
if not os.path.exists(OUT):
    q = """select l.pair_id, l.doi_a, l.doi_b, l.pair_lo, l.pair_hi, l.tier, l.shape, l.authors, l.abstracts,
            l.abs_run, l.retraction_watch, p.shared, p.pct_a, p.pct_b, p.sampled_a, p.sampled_b,
            da.title, case when da.pub_date like '1970-01-01%' then null else substr(da.pub_date,1,4) end,
            db.title, case when db.pub_date like '1970-01-01%' then null else substr(db.pub_date,1,4) end, f.run
            from fulltext f join leads l on l.pair_lo = f.pair_lo and l.pair_hi = f.pair_hi and l.primary_pair = 1
            join pairs p using (pair_id) left join documents da on da.dkey = p.dkey_a left join documents db on db.dkey = p.dkey_b
            where coalesce(f.ft_verdict, '') in ('', 'not both retrieved') and l.tier not in (1, 3)"""
    states, pool = {}, []
    for r in db.execute(q).fetchall():
        run = r[20]
        if run not in states:
            path = f"data/verify_{run}/state.sqlite3"
            states[run] = {}
            if os.path.exists(path):
                st = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                states[run] = {(d or "").lower(): {"status": s, "error": e}
                               for d, s, e in st.execute("select doi, status, error from papers")}
        sa, sb = states[run].get((r[1] or "").lower()), states[run].get((r[2] or "").lower())
        miss = [p for p in (sa, sb) if not (p and p["status"] == "downloaded")]
        if miss and any(dead(p) for p in miss):
            pool.append(r)
    pickle.dump(pool, open(OUT, "wb"))
pool = pickle.load(open(OUT, "rb"))
dois = sorted({(d or "").lower() for r in pool for d in (r[1], r[2]) if ga.queryable(d)})
print(f"pool {len(pool)} pairs, {len(dois)} DOIs", flush=True)
f = ga.Fetcher(db, max_calls=MAX_CALLS, orcid=False)
fetched = 0
for i in range(0, len(dois), ga.CHUNK):
    fetched += f.works(dois[i:i + ga.CHUNK])
    print(f"  {i + ga.CHUNK} DOIs looked at, {fetched} needed fetching, {f.calls} calls", flush=True)
    if f.stopped:
        print("stopped:", f.stopped); break
print(f"done: {f.calls} OpenAlex calls")
