"""For the unobtainable pool's survivors (data/unobtainable_openalex_screen.tsv: not eliminated, both sides with
OpenAlex authors, model score >= MIN_P), count the works the two sides' authors have together: one free OpenAlex
filter call per pair, best score first, cached in D/unobtainable_coauthors.json (useu_coauthors.json answers are
reused). Rerun unobtainable_screen.py afterwards: 2+ joint works eliminates the pair. Resumable; stops at MAX_CALLS
or the first 429. A side with more than 100 author ids (big collaborations) is recorded as null, not checked.
Usage: unobtainable_coauthors.py D [MAX_CALLS] [MIN_P]   (D: data/affiliation_hunt; defaults 8000, 0.3)"""
import csv, json, os, sqlite3, sys, time, urllib.parse, urllib.request
D = sys.argv[1]
MAX_CALLS = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
MIN_P = float(sys.argv[3]) if len(sys.argv) > 3 else 0.3
G = "/home/arthur/Sites/dupefinder/general_index"
db = sqlite3.connect(f"file:{G}/data/gi.sqlite3?mode=ro", uri=True)
key = open(os.path.expanduser("~/.config/dupefinder/openalex_api_key")).read().strip()
cache_f = os.path.join(D, "unobtainable_coauthors.json")
cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {}
useu_f = os.path.join(D, "useu_coauthors.json")
useu = json.load(open(useu_f)) if os.path.exists(useu_f) else {}
ids = lambda doi: [a for (a,) in db.execute("select distinct author_id from oa_authorships where doi=? and author_id is not null",
                                            (doi.lower(),))]
with open(f"{G}/data/unobtainable_openalex_screen.tsv") as fh:
    todo = [r for r in csv.DictReader(fh, delimiter="\t")
            if r["eliminated"] == "0" and r["covered"] == "1" and float(r["p"]) >= MIN_P]
todo.sort(key=lambda r: -float(r["p"]))
calls = 0
for n, r in enumerate(todo, 1):
    k = r["pair_id"]
    if k in cache:
        continue
    if k in useu:
        cache[k] = useu[k]
        continue
    if calls >= MAX_CALLS:
        print(f"--max-calls {MAX_CALLS} reached; rerun to go on"); break
    A, B = ids(r["doi_a"]), ids(r["doi_b"])
    if not A or not B or len(A) > 100 or len(B) > 100:
        cache[k] = None
        continue
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"filter": f"authorships.author.id:{'|'.join(A)},authorships.author.id:{'|'.join(B)}",
         "select": "id", "per_page": 1, "api_key": key})
    for attempt in range(3):
        try:
            cache[k] = json.load(urllib.request.urlopen(url, timeout=60))["meta"]["count"]
            break
        except Exception as e:
            if "429" in str(e):
                json.dump(cache, open(cache_f, "w"))
                sys.exit(f"rate limited after {calls} calls; rerun later (cache kept)")
            time.sleep(5)
    calls += 1
    time.sleep(0.12)
    if calls % 500 == 0:
        json.dump(cache, open(cache_f, "w"))
        print(f"  {calls} calls, {n}/{len(todo)} pairs", flush=True)
json.dump(cache, open(cache_f, "w"))
done = [cache.get(r["pair_id"]) for r in todo if r["pair_id"] in cache]
print(f"{calls} OpenAlex calls; {len(done)}/{len(todo)} survivors checked: {sum(1 for c in done if c is not None and c >= 2)} "
      f"co-authored 2+ works, {sum(1 for c in done if c == 1)} exactly 1 (kept), "
      f"{sum(1 for c in done if c is None)} not checkable")
