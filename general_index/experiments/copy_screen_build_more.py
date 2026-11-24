"""Add another blind copy-screen set, disjoint from every set already in data/copy_screen/key.json.

Same selection rules as copy_screen_build.py (read that docstring), but it never touches the existing
splits: it skips any DOI or title already in key.json, plus the earlier articles of every positive used
so far, and appends the new split's entries to key.json.

    python3 experiments/copy_screen_build_more.py test2 5 95 [--seed 2411]
"""
import argparse, json, random, re, sqlite3, subprocess, sys
import csv
from pathlib import Path

GI = Path(__file__).resolve().parent.parent
OUT = GI / "data" / "copy_screen"
MIN_WORDS, MAX_WORDS = 1500, 20000
LEAK = re.compile(r"retract|expression of concern|plagiar|withdrawn", re.I)

ap = argparse.ArgumentParser()
ap.add_argument("split"); ap.add_argument("n_pos", type=int); ap.add_argument("n_ctl", type=int)
ap.add_argument("--seed", type=int, default=2411)
args = ap.parse_args()

key = json.loads((OUT / "key.json").read_text())
if any(k.startswith(args.split + "/") for k in key):
    sys.exit(f"split {args.split} already exists in key.json")
title_key = lambda t: re.sub(r"\W+", " ", (t or "").lower()).strip()
used = {v["doi"].lower() for v in key.values()} | {title_key(v["title"]) for v in key.values()}
used |= {v["earlier_doi"].lower() for v in key.values() if v.get("earlier_doi")}

db = sqlite3.connect(f"file:{GI}/data/gi.sqlite3?mode=ro", uri=True)


def text_of(library, paper_id):
    lib = sqlite3.connect(f"file:{library}?mode=ro", uri=True)
    row = lib.execute("select file_path, title, doi from papers where id=?", (paper_id,)).fetchone()
    if not row:
        return None
    pdf = Path(row[0]) if Path(row[0]).is_absolute() else Path(library).parent / row[0]
    if not pdf.exists() or title_key(row[1]) in used:
        return None
    try:
        t = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, timeout=60).stdout
    except subprocess.TimeoutExpired:
        return None
    if not (MIN_WORDS <= len(t.split()) <= MAX_WORDS) or LEAK.search(t):
        return None
    return t, str(pdf), row[1]


retracted = {r[0] for r in db.execute("select original_doi_lower from retraction_watch where original_doi_lower is not null")}
flagged = set()
for a, b in db.execute("select doi_a, doi_b from fulltext where ft_verdict in ('real overlap','wrong pdf')"
                       " union select doi_a, doi_b from reviews"):
    flagged |= {(a or "").lower(), (b or "").lower()}
rng = random.Random(args.seed)

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
    doi = f[later]
    if doi.lower() in retracted or doi.lower() in used:
        continue
    pos.append(dict(case=row["case"], doi=doi, paper_id=f[4 + later], library=f[6], pct_of_later=f[7 + later],
                    earlier_doi=f[1 - later], triage=row["triage"]))
rng.shuffle(pos)
print(f"{len(pos)} eligible positives not used before", file=sys.stderr)
chosen_pos = []
for p in pos:
    if p["doi"].lower() in used:
        continue
    got = text_of(p["library"], p["paper_id"])
    if got:
        p["text"], p["pdf"], p["title"] = got
        chosen_pos.append(p)
        used |= {p["doi"].lower(), title_key(p["title"]), p["earlier_doi"].lower()}
    if len(chosen_pos) == args.n_pos:
        break
assert len(chosen_pos) == args.n_pos, f"only {len(chosen_pos)} positives available"

# ---- controls: clean no-overlap leads first, then earlier sides of real pairs
later_side = set()
for a, b, ya, yb in db.execute("select doi_a, doi_b, year_a, year_b from fulltext where ft_verdict='real overlap'"):
    if ya and yb and ya != yb:
        later_side.add((a if ya > yb else b or "").lower())
    else:
        later_side |= {(a or "").lower(), (b or "").lower()}
leads, sources, seen = [], [], set()
for a, b, pa, pb, lib in db.execute("select doi_a, doi_b, paper_a, paper_b, library from fulltext"
                                    " where ft_verdict='no real overlap'"):
    for doi, pid in ((a, pa), (b, pb)):
        k = (doi or "").lower()
        if k and k not in seen and k not in flagged and k not in retracted and k not in used:
            seen.add(k)
            leads.append(dict(doi=doi, paper_id=pid, library=lib, control_type="no-real-overlap lead"))
for a, b, ya, yb, pa, pb, lib in db.execute("select doi_a, doi_b, year_a, year_b, paper_a, paper_b, library"
                                            " from fulltext where ft_verdict='real overlap'"):
    if not (ya and yb) or ya == yb:
        continue
    doi, pid = (b, pb) if ya > yb else (a, pa)
    k = (doi or "").lower()
    if k and k not in seen and k not in later_side and k not in retracted and k not in used:
        seen.add(k)
        sources.append(dict(doi=doi, paper_id=pid, library=lib, control_type="earlier side of a real pair"))
rng.shuffle(leads); rng.shuffle(sources)
chosen_ctl = []
for c in leads + sources:
    got = text_of(c["library"], c["paper_id"])
    if got:
        c["text"], c["pdf"], c["title"] = got
        tk = title_key(c["title"])
        if tk in used:
            continue
        chosen_ctl.append(c)
        used |= {c["doi"].lower(), tk}
    if len(chosen_ctl) == args.n_ctl:
        break
assert len(chosen_ctl) == args.n_ctl, f"only {len(chosen_ctl)} controls available"
n_leads = sum(c["control_type"] == "no-real-overlap lead" for c in chosen_ctl)
print(f"controls: {n_leads} no-real-overlap leads, {args.n_ctl - n_leads} earlier sides", file=sys.stderr)

items = [dict(p, label="copied") for p in chosen_pos] + [dict(c, label="control") for c in chosen_ctl]
rng.shuffle(items)
d = OUT / args.split
d.mkdir(parents=True)
for n, it in enumerate(items, 1):
    name = f"P{n:03d}"
    (d / f"{name}.txt").write_text(it.pop("text"))
    key[f"{args.split}/{name}"] = it
(OUT / "key.json").write_text(json.dumps(key, indent=1))
print(f"wrote {len(items)} papers to {d}", file=sys.stderr)
