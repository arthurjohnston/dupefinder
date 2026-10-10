"""Step 3 (ranking): give cross-author leads a tier, in the `leads` table (gi.sqlite3).

Reads `leads` (gi_report.py), `pairs`, `abstract_checks` (gi_abstract_check.py), `retraction_watch`
(gi_db.py import-rw) and `update_dois`. Writes tier, shape, book_side, abstracts, abs_run,
retraction_watch, lead_rank and primary_pair on the leads it ranks; every other row's are cleared.

Most cross-author candidates can't be verified (abstracts exist for about a quarter, PDFs for
under a tenth), so leads are ranked on the index's own evidence and on what is known
independently, not only on what the abstract check confirmed. Tiers, best first:
  1  either side is a Retraction Watch retraction whose reason mentions plagiarism
  2  the two abstracts share a 20+ word verbatim run (gi_abstract_check.py), and are not
     identical (identical abstracts in one venue are OpenAlex attaching one page's text to
     every item on it: an artifact)
  3  either side is a Retraction Watch retraction for any other reason
  4  everything else that gi_filters kept
  5  one side has no metadata at all (not even a 2022-update title) and the other side has no
     retraction, at >= --unidentified-min-pct; an unidentified side whose partner is a
     plagiarism retraction goes in tier 1 instead. abstracts = "partner unidentified" marks both.
     Also here: a side with 2022-update (text-extracted) metadata >= 85% shared both ways with its
     partner, which is almost always one PDF held twice by the index.
Within a tier: two-sided overlap before one-sided containment (shape), then pairs with a book or
chapter DOI on either side (an ISBN in the DOI) last -- the index often lists a book's editors as a
chapter's authors, so a chapter reprinting its author's own journal paper reads as cross-author --
then by the smaller share and by shared samples.

primary_pair = 1 marks the first document pair (in rank order) of each DOI pair: the index holds some
PDFs twice under one DOI, and counts per DOI pair should use only those.

Usage: gi_rank_leads.py [--db data/gi.sqlite3] [--min-pct 15] [--unidentified-min-pct 50] [--tsv OUT.tsv]
"""
import argparse
import csv
import re

import gi_db
from gi_filters import venue

TSV_COLS = ["tier", "shape", "book_side", "shared", "pct_a", "pct_b", "abstracts", "abs_run",
            "retraction_watch", "doi_a", "year_a", "title_a", "author_a", "doi_b", "year_b", "title_b", "author_b"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--min-pct", type=float, default=15)
    ap.add_argument("--unidentified-min-pct", type=float, default=50,
                    help="tier 5 (one side unidentified, no retraction): minimum larger-side share")
    ap.add_argument("--tsv", help="also write the pre-database leads TSV here")
    args = ap.parse_args()
    db = gi_db.connect(args.db)

    retracted = {}  # last record in the CSV wins, as before
    for r in db.execute("select original_doi_lower, reason from retraction_watch "
                        "where original_doi_lower is not null order by file_order"):
        retracted[r[0]] = r[1]
    alt_dois = {r[0] for r in db.execute("select doi from update_dois")}
    abstracts = {(r["doi_a"], r["doi_b"]): r for r in db.execute("select * from abstract_checks")}

    leads, skipped_both_unnamed = [], 0
    rows = db.execute("""select l.pair_id, l.doi_a, l.doi_b, l.pair_lo, l.pair_hi, l.reason, l.authors,
                                p.shared, p.pct_a, p.pct_b
                         from leads l join pairs p using (pair_id)
                         where l.reason = 'no metadata' or (l.reason = '' and l.authors = 'no shared author')
                         order by l.pair_id""")
    for r in rows:
        unidentified = r["reason"] == "no metadata"
        if unidentified and r["doi_a"].startswith("gi:") and r["doi_b"].startswith("gi:"):
            skipped_both_unnamed += 1
            continue
        hi_pct, lo_pct = max(r["pct_a"], r["pct_b"]), min(r["pct_a"], r["pct_b"])
        if hi_pct < args.min_pct:
            continue
        da, db_ = r["doi_a"].lower(), r["doi_b"].lower()
        rw = "; ".join(f"{side}: {retracted[d].strip(';')}" for side, d in (("A", da), ("B", db_))
                       if d in retracted)
        ab = abstracts.get((da, db_))
        abs_state = "not checked" if ab is None else ab["verdict"]
        if ab and ab["verdict"] == "shared text" and (ab["containment5"] or 0) >= 0.99 \
                and venue({"journal": None, "doi": da}) == venue({"journal": None, "doi": db_}):
            abs_state = "identical (artifact)"
        if unidentified:
            tier = 1 if "plagiarism" in rw.lower() else 5
            if tier == 5 and hi_pct < args.unidentified_min_pct:
                continue
            abs_state = "partner unidentified"
        elif (da.startswith("gi:") or db_.startswith("gi:") or da in alt_dois or db_ in alt_dois) and \
                lo_pct >= 85 and "plagiarism" not in rw.lower():
            # A side whose metadata is text-extracted (no DOI, or a DOI found somewhere in the PDF),
            # ~identical both ways to its partner: almost always the index holding one PDF twice.
            tier = 5
            abs_state = "partner unidentified (near-identical copy)"
        elif "plagiarism" in rw.lower():
            tier = 1
        elif abs_state == "shared text":
            tier = 2
        elif rw:
            tier = 3
        else:
            tier = 4
        book = bool(re.search(r"97[89][-0-9]{9,}|/b978|/978", da + " " + db_))
        shape = "one-sided" if lo_pct < 10 and hi_pct >= 3 * lo_pct else "two-sided"
        leads.append(dict(pair_id=r["pair_id"], lo=r["pair_lo"], hi=r["pair_hi"], tier=tier, shape=shape,
                          book_side="yes" if book else "", abstracts=abs_state,
                          abs_run=ab["longest_run"] if ab else None, retraction_watch=rw,
                          lo_pct=lo_pct, shared=r["shared"]))
    leads.sort(key=lambda x: (x["tier"], x["book_side"] == "yes", x["shape"] == "one-sided",
                              -x["lo_pct"], -x["shared"]))
    seen = set()
    for i, x in enumerate(leads, 1):
        x["lead_rank"] = i
        key = (x["lo"], x["hi"])
        x["primary_pair"] = int(key not in seen)
        seen.add(key)
    db.execute("update leads set tier = null, shape = null, book_side = null, abstracts = null, abs_run = null, "
               "retraction_watch = null, lead_rank = null, primary_pair = null")
    db.executemany("update leads set tier = :tier, shape = :shape, book_side = :book_side, abstracts = :abstracts, "
                   "abs_run = :abs_run, retraction_watch = :retraction_watch, lead_rank = :lead_rank, "
                   "primary_pair = :primary_pair where pair_id = :pair_id", leads)
    db.commit()

    if args.tsv:
        with open(args.tsv, "w") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(TSV_COLS)
            for r in db.execute("select * from lead_details where tier is not null order by lead_rank"):
                w.writerow([r["tier"], r["shape"], r["book_side"] or "", r["shared"], f"{r['pct_a']:.1f}",
                            f"{r['pct_b']:.1f}", r["abstracts"], r["abs_run"] if r["abs_run"] is not None else "",
                            r["retraction_watch"], r["doi_a"], r["year_a"] or "", " ".join((r["title_a"] or "").split()),
                            r["author_a"] or "", r["doi_b"], r["year_b"] or "", " ".join((r["title_b"] or "").split()),
                            r["author_b"] or ""])
    total = {t: sum(1 for x in leads if x["tier"] == t) for t in (1, 2, 3, 4, 5)}
    prim = {t: sum(1 for x in leads if x["tier"] == t and x["primary_pair"]) for t in (1, 2, 3, 4, 5)}
    print(f"{len(leads):,} cross-author leads at >= {args.min_pct}% ({len(seen):,} distinct DOI pairs); "
          f"skipped {skipped_both_unnamed:,} with neither side identified")
    for t, label in ((1, "plagiarism retraction"), (2, "shared abstract text"), (3, "other retraction"),
                     (4, "index evidence only"), (5, f"partner unidentified, >= {args.unidentified_min_pct}%")):
        print(f"  tier {t} ({label}): {total[t]:,} document pairs, {prim[t]:,} DOI pairs")


if __name__ == "__main__":
    main()
