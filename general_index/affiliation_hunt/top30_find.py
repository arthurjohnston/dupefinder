"""Leads/fulltext pairs whose copying (later) paper has an author now at a top-30 university."""
import sqlite3, json, pickle, sys, collections
sys.path.insert(0, "/home/arthur/Sites/dupefinder/general_index")
OUT = sys.argv[1]
TOP30 = {  # canonical -> OpenAlex display names counted as that university
 "Oxford": ["University of Oxford"], "MIT": ["Massachusetts Institute of Technology"],
 "Harvard": ["Harvard University"], "Princeton": ["Princeton University"], "Cambridge": ["University of Cambridge"],
 "Stanford": ["Stanford University", "Stanford Medicine"], "Caltech": ["California Institute of Technology"],
 "UC Berkeley": ["University of California, Berkeley"], "Imperial": ["Imperial College London"],
 "Yale": ["Yale University"], "ETH Zurich": ["ETH Zurich"], "Tsinghua": ["Tsinghua University"],
 "Peking": ["Peking University"], "Penn": ["University of Pennsylvania"],
 "Johns Hopkins": ["Johns Hopkins University", "Johns Hopkins Medicine"], "Chicago": ["University of Chicago"],
 "UCLA": ["University of California, Los Angeles"], "Cornell": ["Cornell University", "Weill Cornell Medicine"],
 "Columbia": ["Columbia University", "Columbia University Irving Medical Center"],
 "Michigan": ["University of Michigan", "University of Michigan–Ann Arbor"], "Toronto": ["University of Toronto"],
 "UCL": ["University College London"], "Edinburgh": ["University of Edinburgh"],
 "Carnegie Mellon": ["Carnegie Mellon University"], "U Washington": ["University of Washington"],
 "NYU": ["New York University"], "TU Munich": ["Technical University of Munich"],
 "NUS": ["National University of Singapore"], "Duke": ["Duke University"], "Northwestern": ["Northwestern University"],
}
NAME = {n.lower(): k for k, ns in TOP30.items() for n in ns}
db = sqlite3.connect("file:/home/arthur/Sites/dupefinder/general_index/data/gi.sqlite3?mode=ro", uri=True)
orcid_now = {}
for o, cur in db.execute("select orcid, current from orcid_employments"):
    hits = [NAME[j["name"].lower()] for j in json.loads(cur or "[]") if (j.get("name") or "").lower() in NAME]
    if hits: orcid_now[o] = hits[0]
authors = {}   # author_id -> (uni, how)
for aid, orcid, last_year, insts in db.execute("select author_id, orcid, last_year, institutions from oa_authors"):
    uni = next((NAME[i["name"].lower()] for i in json.loads(insts or "[]") if (i.get("name") or "").lower() in NAME), None)
    if orcid in orcid_now:
        authors[aid] = (orcid_now[orcid], "ORCID current job")
    elif uni and (last_year or 0) >= 2024:
        authors[aid] = (uni, f"OpenAlex last known, published {last_year}")
print("active top-30 authors:", len(authors))
doi_hits = collections.defaultdict(list)
for doi, aid, name in db.execute("select doi, author_id, name from oa_authorships where author_id is not null"):
    if aid in authors:
        doi_hits[doi].append((name, *authors[aid]))
print("DOIs with one:", len(doi_hits))
import gi_preprints
cands = []
fields = "pair_id, doi_a, doi_b, pair_lo, pair_hi, tier, shape, authors, abstracts, abs_run, retraction_watch"
q = f"""select {', '.join('l.' + f for f in fields.split(', '))}, p.shared, p.pct_a, p.pct_b, p.sampled_a, p.sampled_b,
        da.title, case when da.pub_date like '1970-01-01%' then null else substr(da.pub_date,1,4) end,
        db.title, case when db.pub_date like '1970-01-01%' then null else substr(db.pub_date,1,4) end
        from leads l join pairs p using (pair_id) left join documents da on da.dkey = p.dkey_a
        left join documents db on db.dkey = p.dkey_b where l.pair_id = ?"""
ids = [r[0] for r in db.execute("""select a.pair_id from pair_affiliation a""")]
# every scored lead (tier 2 + band 4) plus every full-text pair
ft = {(r[0], r[1]): r for r in db.execute("select pair_lo, pair_hi, run, ft_verdict, byline, page_number, retrieved from fulltext")}
ft_ids = [r[0] for r in db.execute("select l.pair_id from fulltext f join leads l on l.pair_lo=f.pair_lo and l.pair_hi=f.pair_hi and l.primary_pair=1")]
lead_doi = {}
for pid, da, dbb in db.execute("select pair_id, lower(doi_a), lower(doi_b) from leads where primary_pair = 1"):
    if da in doi_hits or dbb in doi_hits:
        lead_doi[pid] = (da, dbb)
want = set(ids) | set(ft_ids)
keep = [pid for pid in lead_doi if pid in want]
print("pairs with a top-30 author on either side:", len(keep))
rows = []
for pid in keep:
    r = db.execute(q, (pid,)).fetchone()
    rows.append(r)
pickle.dump({"rows": rows, "doi_hits": dict(doi_hits), "ft": ft}, open(OUT, "wb"))
print("saved", len(rows))
