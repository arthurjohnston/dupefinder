"""Take the best-scored US/European pairs (useu_ranked.pkl), drop those whose two sides' authors have ever
published together (one free OpenAlex filter call per pair, cached in useu_coauthors.json), and write the
survivors' pair_ids to general_index/data/useu_pairs.txt for gi_verify.py select --pairs.
Usage: useu_screen.py SCRATCH N   (N: how many survivors to keep)"""
import json, os, pickle, sqlite3, sys, time, urllib.parse, urllib.request
S, N = sys.argv[1], int(sys.argv[2])
G = "/home/arthur/Sites/dupefinder/general_index"
ranked = pickle.load(open(f"{S}/useu_ranked.pkl", "rb"))
db = sqlite3.connect(f"file:{G}/data/gi.sqlite3?mode=ro", uri=True)
key = open(os.path.expanduser("~/.config/dupefinder/openalex_api_key")).read().strip()
cache_f = f"{S}/useu_coauthors.json"
cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {}
ids = lambda doi: [a for (a,) in db.execute("select author_id from oa_authorships where doi=? and author_id is not null",
                                            (doi.lower(),))]
# university-printed pairs first (an "institut"/"univ"/... name on the copying paper's byline), then the rest
order = sorted(ranked, key=lambda o: (not o["academic"], -o["p"]))
keep, calls, dropped = [], 0, 0
for o in order:
    if len(keep) >= N:
        break
    k = str(o["pair_id"])
    if k not in cache:
        E, L = ids(o["a"][0]), ids(o["b"][0])
        if not E or not L or len(E) > 100 or len(L) > 100:
            cache[k] = None
        else:
            f = f"authorships.author.id:{'|'.join(E)},authorships.author.id:{'|'.join(L)}"
            url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
                {"filter": f, "select": "id", "per_page": 1, "api_key": key})
            for attempt in range(3):
                try:
                    cache[k] = json.load(urllib.request.urlopen(url, timeout=60))["meta"]["count"]
                    break
                except Exception as e:
                    if "429" in str(e):
                        sys.exit(f"rate limited after {calls} calls; rerun later (cache kept)")
                    time.sleep(5)
            calls += 1
            time.sleep(0.12)
            if calls % 200 == 0:
                json.dump(cache, open(cache_f, "w"))
                print(f"  {calls} calls, {len(keep)} kept, {dropped} collaborators", flush=True)
    if cache.get(k) is None or cache[k] >= 2:     # unknown authors (large collaborations) or collaborators: skip
        dropped += 1
        continue
    keep.append(o)
json.dump(cache, open(cache_f, "w"))
with open(f"{G}/data/useu_pairs.txt", "w") as fh:
    for o in keep:
        later = o["b"] if o["later"] == "b" else o["a"]
        fh.write(f"{o['pair_id']}  # {o['p']:.2f} {'univ' if o['academic'] else 'other'} {later[1]} "
                 f"{'; '.join(sorted({c for _, _, c in o['printed']}))}\n")
print(f"{calls} OpenAlex calls; kept {len(keep)} (university-printed {sum(o['academic'] for o in keep)}), "
      f"skipped {dropped} (collaborators or authors unknown) -> data/useu_pairs.txt")
