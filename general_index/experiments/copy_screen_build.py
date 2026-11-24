"""Build the blind copy-screen test sets: can an agent tell, from one paper's text alone (no source,
no web), that it contains text copied from another work?

Positives: the LATER article of cases settled as real cross-author copying by a human
(data/case_status.csv: verdict=real, verdict_by=you, triage "problem / cross-author copying"
or "problem / data copying"). Controls: papers on either side of a GI pair whose full-text check
found "no real overlap", that appear in no real-overlap / wrong-pdf pair and no review, journal-matched
to the positives where possible. Neither class may be in Retraction Watch or carry a retraction /
concern stamp in its text (that would leak the label).

Writes data/copy_screen/{dev,test}/Pnnn.txt (pdftotext of the PDF, cut at MAX_WORDS) and
data/copy_screen/key.json (kept outside the folders the screening agents are given).

    python3 experiments/copy_screen_build.py            # dev 20/80, test 5/45, seed 1123
"""
import csv, json, random, re, sqlite3, subprocess, sys
from collections import defaultdict
from pathlib import Path

GI = Path(__file__).resolve().parent.parent
OUT = GI / "data" / "copy_screen"
SEED = 1123
SPLITS = {"dev": (20, 80), "test": (5, 45)}
MIN_WORDS, MAX_WORDS = 1500, 20000
LEAK = re.compile(r"retract|expression of concern|plagiar|withdrawn", re.I)

db = sqlite3.connect(f"file:{GI}/data/gi.sqlite3?mode=ro", uri=True)


def journal(doi):
    r = db.execute("select journal from documents where lower(doi)=lower(?) and journal is not null limit 1",
                   (doi,)).fetchone()
    return (r[0] or "").strip().lower() if r else ""


def text_of(library, paper_id):
    lib = sqlite3.connect(f"file:{library}?mode=ro", uri=True)
    row = lib.execute("select file_path, title, doi from papers where id=?", (paper_id,)).fetchone()
    if not row or not Path(row[0]).exists():
        return None
    try:
        t = subprocess.run(["pdftotext", row[0], "-"], capture_output=True, text=True, timeout=60).stdout
    except subprocess.TimeoutExpired:
        return None
    words = t.split()
    if not (MIN_WORDS <= len(words) <= MAX_WORDS) or LEAK.search(t):
        return None
    return t, row[0], row[1]


retracted = {r[0] for r in db.execute("select original_doi_lower from retraction_watch where original_doi_lower is not null")}
flagged = set()   # any doi in a pair that is not a clean "no real overlap", or reviewed at all
for a, b in db.execute("select doi_a, doi_b from fulltext where ft_verdict in ('real overlap','wrong pdf')"
                       " union select doi_a, doi_b from reviews"):
    flagged |= {(a or "").lower(), (b or "").lower()}

# ---- positives
pos = []
for row in csv.DictReader(open(GI / "data" / "case_status.csv")):
    if row["verdict"] != "real" or row["verdict_by"] != "you":
        continue
    if row["triage"] not in ("problem / cross-author copying", "problem / data copying"):
        continue
    f = db.execute("select doi_a, doi_b, year_a, year_b, paper_a, paper_b, library, ft_pct_a, ft_pct_b"
                   " from fulltext where page_number=?", (int(row["case"][3:]),)).fetchone()
    if not f or not (f[2] and f[3]) or f[2] == f[3]:
        continue
    later = 0 if f[2] > f[3] else 1
    doi, pid, pct = f[later], f[4 + later], f[7 + later]
    if doi.lower() in retracted:
        continue
    pos.append(dict(case=row["case"], doi=doi, paper_id=pid, library=f[6], pct_of_later=pct,
                    earlier_doi=f[1 - later], triage=row["triage"]))

rng = random.Random(SEED)
rng.shuffle(pos)
need_pos = sum(p for p, _ in SPLITS.values())
chosen_pos, used = [], set()
for p in pos:
    if p["doi"].lower() in used or p.get("title_key") in used:          # one later article can be the later side of several cases
        continue
    got = text_of(p["library"], p["paper_id"])
    if got:
        p["text"], p["pdf"], p["title"] = got
        p["journal"] = journal(p["doi"])
        tk = re.sub(r"\W+", " ", p["title"].lower()).strip()
        if tk in used:                    # the same article under two DOIs (GI-59/GI-60)
            continue
        p["title_key"] = tk
        chosen_pos.append(p)
        used |= {p["doi"].lower(), tk}
    if len(chosen_pos) == need_pos:
        break
assert len(chosen_pos) == need_pos, len(chosen_pos)

# ---- controls
pool, seen = defaultdict(list), set()
for a, b, pa, pb, lib in db.execute("select doi_a, doi_b, paper_a, paper_b, library from fulltext"
                                    " where ft_verdict='no real overlap'"):
    for doi, pid in ((a, pa), (b, pb)):
        k = (doi or "").lower()
        if not k or k in seen or k in flagged or k in retracted:
            continue
        seen.add(k)
        pool["all"].append(dict(doi=doi, paper_id=pid, library=lib))
# second control source: the EARLIER article of other real-overlap pairs (original work, same fields as
# the positives). Excluded: the sources of the chosen positives, and any doi that is ever the later side.
later_side = set()
for a, b, ya, yb in db.execute("select doi_a, doi_b, year_a, year_b from fulltext where ft_verdict='real overlap'"):
    if ya and yb and ya != yb:
        later_side.add((a if ya > yb else b or "").lower())
    else:
        later_side |= {(a or "").lower(), (b or "").lower()}
pos_sources = {p["earlier_doi"].lower() for p in chosen_pos}
for a, b, ya, yb, pa, pb, lib in db.execute("select doi_a, doi_b, year_a, year_b, paper_a, paper_b, library"
                                            " from fulltext where ft_verdict='real overlap'"):
    if not (ya and yb) or ya == yb:
        continue
    doi, pid = (b, pb) if ya > yb else (a, pa)
    k = (doi or "").lower()
    if not k or k in seen or k in later_side or k in retracted or k in pos_sources:
        continue
    seen.add(k)
    pool["source"].append(dict(doi=doi, paper_id=pid, library=lib, control_type="earlier side of a real pair"))
for c in pool["all"]:
    c["control_type"] = "no-real-overlap lead"
rng.shuffle(pool["all"])
rng.shuffle(pool["source"])
print(f"positives {len(chosen_pos)} of {len(pos)} eligible; control pool {len(pool['all'])}", file=sys.stderr)

# journal-match: first pass prefers controls in the positives' journals, the rest are random
pos_journals = {p["journal"] for p in chosen_pos if p["journal"]}
need_ctl = sum(c for _, c in SPLITS.values())
chosen_ctl, matched = [], 0
scan = pool["all"] + pool["source"][:3000]   # clean leads first, sources fill the rest
for c in scan:
    c["journal"] = journal(c["doi"])
first = sorted(scan, key=lambda c: (c["control_type"] != "no-real-overlap lead", c["journal"] not in pos_journals))
for c in first:
    got = text_of(c["library"], c["paper_id"])
    if got:
        c["text"], c["pdf"], c["title"] = got
        chosen_ctl.append(c)
        matched += c["journal"] in pos_journals
    if len(chosen_ctl) == need_ctl:
        break
assert len(chosen_ctl) == need_ctl, len(chosen_ctl)
print(f"controls {need_ctl}, {matched} journal-matched", file=sys.stderr)

# ---- split, shuffle, write
rng.shuffle(chosen_ctl)
key, pi, ci = {}, 0, 0
for split, (np_, nc) in SPLITS.items():
    items = [dict(p, label="copied") for p in chosen_pos[pi:pi + np_]] + \
            [dict(c, label="control") for c in chosen_ctl[ci:ci + nc]]
    pi, ci = pi + np_, ci + nc
    rng.shuffle(items)
    d = OUT / split
    d.mkdir(parents=True, exist_ok=True)
    for n, it in enumerate(items, 1):
        name = f"P{n:03d}"
        (d / f"{name}.txt").write_text(it.pop("text"))
        key[f"{split}/{name}"] = it
(OUT / "key.json").write_text(json.dumps(key, indent=1))
print(f"wrote {len(key)} papers to {OUT}", file=sys.stderr)
