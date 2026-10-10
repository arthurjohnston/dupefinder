"""Compare a local model's agent-review verdicts with Opus's (prompt v4) on the same pairs.

Usage: compare_local_vs_opus.py PROMPT_VERSION [--out data/local_vs_opus.tsv]
Prints agreement on decision and type, a confusion table, and how each model did on the checks REVIEWING.md
makes non-optional: bylines read (names listed for both papers), shared names, earlier paper, and
later_cites_earlier against the packet's citation check.
"""
import argparse
import csv
import json
import sqlite3
from collections import Counter

ap = argparse.ArgumentParser()
ap.add_argument("prompt_version")
ap.add_argument("--db", default="data/gi.sqlite3")
ap.add_argument("--out", default="data/local_vs_opus.tsv")
args = ap.parse_args()
db = sqlite3.connect(args.db)
db.row_factory = sqlite3.Row
rows = db.execute("""select l.pair_lo, l.pair_hi, l.decision ld, l.type lt, l.severity ls, l.earlier_paper le,
                            l.later_cites_earlier lc, l.byline_names_a lba, l.byline_names_b lbb, l.shared_names lsn,
                            l.precondition_failures lpf, l.summary lsum, l.stop_reason lstop,
                            o.decision od, o.type ot, o.severity os, o.earlier_paper oe, o.later_cites_earlier oc,
                            o.byline_names_a oba, o.byline_names_b obb, o.shared_names osn, o.summary osum
                     from agent_reviews l join agent_reviews o using (pair_lo, pair_hi)
                     where l.prompt_version = ? and o.prompt_version = 'v4' and o.model like 'claude%'""",
                  (args.prompt_version,)).fetchall()
n = len(rows)
print(f"{n} pairs reviewed by both")
if not n:
    raise SystemExit
agree = lambda a, b: sum(r[a] == r[b] for r in rows)
problem = lambda d: d == "problem"
print(f"decision agrees:            {agree('ld', 'od')}/{n}")
print(f"problem vs not agrees:      {sum(problem(r['ld']) == problem(r['od']) for r in rows)}/{n}")
print(f"type agrees:                {agree('lt', 'ot')}/{n}")
print(f"earlier paper agrees:       {agree('le', 'oe')}/{n}")
print(f"later_cites_earlier agrees: {agree('lc', 'oc')}/{n}")
both_names = lambda a, b: bool(json.loads(a or "[]")) and bool(json.loads(b or "[]"))
print(f"bylines listed for both:    local {sum(both_names(r['lba'], r['lbb']) for r in rows)}/{n}, "
      f"opus {sum(both_names(r['oba'], r['obb']) for r in rows)}/{n}")
shared = lambda x: bool(json.loads(x or "[]"))
print(f"shared-name call agrees:    {sum(shared(r['lsn']) == shared(r['osn']) for r in rows)}/{n}")
print(f"local forced to unsure by a precondition: {sum(r['lpf'] not in (None, '[]') for r in rows)}")
print(f"local stopped at max_tokens: {sum(r['lstop'] == 'max_tokens' for r in rows)}")
print("\nOpus type -> local type")
for (o, l), c in sorted(Counter((r["ot"], r["lt"]) for r in rows).items(), key=lambda x: (x[0][0] or "", -x[1])):
    print(f"  {o or '-':22} -> {l or '-':22} {c}")
with open(args.out, "w", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["pair_lo", "pair_hi", "opus_decision", "opus_type", "local_decision", "local_type", "opus_summary", "local_summary"])
    for r in rows:
        w.writerow([r["pair_lo"], r["pair_hi"], r["od"], r["ot"], r["ld"], r["lt"], r["osum"], r["lsum"]])
print(f"\nper-pair table -> {args.out}")
