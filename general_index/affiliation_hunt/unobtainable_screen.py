"""Screen the unobtainable pool (unobtainable_fetch.py) for pairs that are not cross-author, from OpenAlex alone.
Calibrates each signal on the full-text-checked real-overlap pairs first (cross-author/data copying vs self-reuse/
same paper), then scores every pool pair with top30_rank.py's model and writes data/unobtainable_openalex_screen.tsv.
Signals: shared author id; a name in any word order ('Jia Li Wei' = 'Liwei Jia': OpenAlex splits such people into
two ids); surname + first initial; same title (IEEE masthead stripped); shared institution; and, once
unobtainable_coauthors.py has run, the two sides' authors having 2+ works together. `eliminated` = any AUTHOR
signal or co-authorship (none removed a confirmed cross-author case). Same title alone is NOT eliminated: besides
JPT synopses of SPE papers (legitimate) it holds whole papers republished under other names. Shared institution
alone isn't either (it loses ~3% of real cases). Read-only on gi.sqlite3.
Usage: unobtainable_screen.py D   (D: data/affiliation_hunt)"""
import collections, csv, difflib, itertools, json, os, pickle, re, sqlite3, sys, unicodedata
import numpy as np
G = "/home/arthur/Sites/dupefinder/general_index"
sys.path.insert(0, G); os.chdir(G)
D = sys.argv[1]
POOL, OUT = os.path.join(D, "unobtainable_pool.pkl"), "data/unobtainable_openalex_screen.tsv"
CO_F = os.path.join(D, "unobtainable_coauthors.json")
CO = json.load(open(CO_F)) if os.path.exists(CO_F) else {}
src = open("affiliation_hunt/top30_rank.py").read()
exec(src[:src.index("d = pickle.load")].replace("PKL, OUT = sys.argv[1], sys.argv[2]", ""))
import gi_affiliations as ga
years = pickle.load(open("data/affiliation_hunt/top30_years.pkl", "rb"))

plain = lambda t: unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
_cache = {}
def auth(doi):
    d = (doi or "").lower()
    if d not in _cache:
        _cache[d] = [(aid, name, json.loads(inst or "[]")) for aid, name, inst in db.execute(
            "select author_id, name, institutions from oa_authorships where doi=?", (d,))]
    return _cache[d]
def perm_keys(A):
    """every concatenation of a name's parts in any order: 'Jia Li Wei' and 'Liwei Jia' share 'jialiwei'"""
    ks = set()
    for _, n, _ in A:
        w = re.findall(r"[a-z]+", plain(n))
        if 2 <= len(w) <= 4 and sum(map(len, w)) >= 5:
            ks |= {"".join(p) for p in itertools.permutations(w)}
    return ks
def init_keys(A):
    ks = set()
    for _, n, _ in A:
        w = re.findall(r"[a-z]+", plain(n))
        if len(w) >= 2 and len(w[-1]) >= 2:
            ks.add((w[-1], w[0][0]))
    return ks
def insts(A):
    return {i.get("id") or i.get("name") for _, _, ins in A for i in ins if i.get("id") or i.get("name")}
IEEE = re.compile(r"^\s*\[[^\]]*\]\s*.*? - ")
def title(t):
    return re.sub(r"\W+", " ", plain(ga.clean_title(IEEE.sub("", t or "")))).strip()
def tsim(a, b):
    a, b = title(a), title(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if len(a) > 15 and len(b) > 15 else 0
SIG = ("shared_id", "name_perm", "name_init", "same_title", "shared_inst", "jpt_synopsis")
def signals(da, dbb, ta, tb, pid=None):
    A, B = auth(da), auth(dbb)
    s = {"covered": bool(A) and bool(B)}
    s["shared_id"] = bool({x for x, _, _ in A if x} & {x for x, _, _ in B if x})
    s["name_perm"] = bool(perm_keys(A) & perm_keys(B))
    s["name_init"] = bool(init_keys(A) & init_keys(B))
    s["same_title"] = tsim(ta, tb) >= 0.82
    s["shared_inst"] = bool(insts(A) & insts(B))
    s["author"] = s["shared_id"] or s["name_perm"] or s["name_init"]
    co = CO.get(str(pid))
    s["coauthored"] = "" if co is None else int(co >= 2)
    s["jpt_synopsis"] = s["same_title"] and not s["author"] and "-jpt" in (da + dbb).lower()
    s["elim"] = bool(s["author"] or s["coauthored"] or s["jpt_synopsis"])
    return s

# calibration
NOT_CROSS = {"text recycling", "duplicate publication", "same paper", "legitimate reuse"}
cal = collections.defaultdict(collections.Counter)
fp = []
for r in train:
    if r[20] != "real overlap":
        continue
    cross = r[21] == "no byline overlap" and r[22] == "problem" and r[23] in ("cross-author copying", "data copying")
    notc = r[21] != "no byline overlap" or r[23] in NOT_CROSS
    if not (cross or notc):
        continue
    lab = "cross" if cross else "not_cross"
    s = signals(r[1] or '', r[2] or '', r[16], r[18])
    c = cal[lab]; c["n"] += 1
    for k in ("covered",) + SIG + ("elim",):
        c[k] += s[k]
    c["inst_only"] += s["shared_inst"] and not s["elim"]
    if cross and s["elim"]:
        fp.append((r[1], r[2], [k for k in SIG if s[k]]))
print("\nCALIBRATION on checked real-overlap pairs")
for lab in ("cross", "not_cross"):
    print(f"  {lab:10s}", dict(cal[lab]))
print("  cross-author cases the rule would drop:", fp)

# pool
pool = pickle.load(open(POOL, "rb"))
rows = []
for r in pool:
    r = list(r[:20]) + [r[20]]
    for di, yi in ((1, 17), (2, 19)):
        if not (r[yi] or "").isdigit():
            r[yi] = years.get((r[di] or "").lower(), r[yi])
    rows.append(r)
P = model.predict_proba(np.array([feats(r[:20]) for r in rows], float))[:, 1]
res = collections.defaultdict(collections.Counter)
with open(OUT, "w", newline="") as fh:
    w = csv.writer(fh, delimiter="\t")
    w.writerow(["pair_id", "p", "tier", "run", "doi_a", "doi_b", "year_a", "year_b", "covered", *SIG, "coauthored", "eliminated",
                "title_a", "title_b"])
    for r, p in sorted(zip(rows, P), key=lambda x: -x[1]):
        s = signals(r[1] or '', r[2] or '', r[16], r[18], r[0])
        band = "p>=0.5" if p >= 0.5 else "p 0.3-0.5" if p >= 0.3 else "p<0.3"
        for b in (band, "all"):
            c = res[b]; c["n"] += 1
            for k in ("covered",) + SIG + ("elim",):
                c[k] += s[k]
            c["inst_only"] += s["shared_inst"] and not s["elim"]
            c["coauthored"] += s["coauthored"] == 1
            c["title_other"] += s["same_title"] and not s["elim"]
        w.writerow([r[0], f"{p:.3f}", r[5], r[20], r[1], r[2], r[17], r[19], int(s["covered"]),
                    *[int(s[k]) for k in SIG], s["coauthored"], int(s["elim"]), (r[16] or "")[:200], (r[18] or "")[:200]])
print(f"\nPOOL ({len(rows)} unobtainable pairs)")
for b in ("p>=0.5", "p 0.3-0.5", "p<0.3", "all"):
    print(f"  {b:10s}", dict(res[b]))
print("wrote", OUT)
