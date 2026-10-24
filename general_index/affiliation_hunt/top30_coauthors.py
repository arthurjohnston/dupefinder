"""For each 'different authors' pair left on the manual-download list: have any author of the earlier paper and
any author of the later paper ever published together (OpenAlex, one filter call per pair)? Writes
coauthors.json next to the list; top30_manual.py reads it."""
import json, os, pickle, re, sqlite3, sys, time, urllib.parse, urllib.request
S = sys.argv[1]
short = pickle.load(open(f"{S}/top30_short.pkl", "rb"))
db = sqlite3.connect("file:data/gi.sqlite3?mode=ro", uri=True)
out = "data/verify_top30_s16/manual_downloads/coauthors.json"
txt = open("data/verify_top30_s16/manual_downloads/_pending.md").read()
ranks = [int(x) for x in re.findall(r"^## #(\d+) .*· (?:different authors|earlier paper's authors unknown)$", txt, re.M)]
key = open(os.path.expanduser("~/.config/dupefinder/openalex_api_key")).read().strip()
res = json.load(open(out)) if os.path.exists(out) else {}
ids = lambda doi: [a for (a,) in db.execute("select author_id from oa_authorships where doi=? and author_id is not null", (doi.lower(),))]
for rk in ranks:
    if str(rk) in res:
        continue
    r = short[rk - 1]
    E, L = ids(r["a"][0]), ids(r["b"][0])
    if not E or not L:
        res[str(rk)] = {"count": None, "note": "no OpenAlex author ids for a side"}
        continue
    if len(E) > 100 or len(L) > 100:
        res[str(rk)] = {"count": None, "note": f"large collaboration ({len(E)}/{len(L)} authors)"}
        continue
    f = f"authorships.author.id:{'|'.join(E)},authorships.author.id:{'|'.join(L)}"
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"filter": f, "select": "publication_year,title", "per_page": 3, "api_key": key})
    d = json.load(urllib.request.urlopen(url, timeout=60))
    res[str(rk)] = {"count": d["meta"]["count"],
                    "examples": [f"{w['publication_year']} {(w['title'] or '')[:80]}" for w in d["results"]]}
    time.sleep(0.15)
json.dump(res, open(out, "w"), indent=1)
for rk in ranks:
    v = res[str(rk)]
    print(f"#{rk:<4} {v['count'] if v['count'] is not None else '-':>4}  {v.get('note') or '; '.join(v['examples'])[:120]}")
