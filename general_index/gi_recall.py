"""Step 6: how many Retraction Watch plagiarism retractions the ranked leads surface (gi.sqlite3).

Denominator: distinct DOIs of retractions whose reason mentions plagiarism, published 2020 or earlier
(the index is a 2021 snapshot), that have a row in the slice metadata for the slices loaded
(retraction_watch.in_slice_metadata). A DOI counts as found if it is either side of any ranked lead
(any tier); "identified partner" if at least one such lead's other side is identified, else
"unidentified only". Reads the rw_recall view; --list prints the found DOIs.

A paper's source falls in the loaded slices only part of the time (4 of 16 slices: about a quarter),
so divide found/denominator by that share for a rough recall over cases whose source is in the index.

Usage: gi_recall.py [--db data/gi.sqlite3] [--list]
"""
import argparse

import gi_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    db = gi_db.connect(args.db)
    r = db.execute("select * from rw_recall").fetchone()
    slices = db.execute("select count(distinct slice) from documents").fetchone()[0] or 16
    share = slices / 16
    print(f"Retraction Watch recall, slices loaded: {slices}: identified partner {r['identified_partner']} | "
          f"unidentified only {r['unidentified_only']} | total {r['found']} of {r['plagiarism_papers']} "
          f"({100 * r['found'] / max(1, r['plagiarism_papers']):.1f}%; /{share:.2f} partner coverage = "
          f"~{100 * r['found'] / max(1, r['plagiarism_papers']) / share:.0f}% of cases whose source is in the index)")
    if args.list:
        for row in db.execute("""with sides as (select lower(doi_a) as doi from leads where tier is not null
                                               union select lower(doi_b) from leads where tier is not null)
                                 select distinct rw.original_doi, rw.title from retraction_watch rw
                                 where rw.is_plagiarism = 1 and rw.original_year <= 2020 and rw.in_slice_metadata = 1
                                   and rw.original_doi_lower in (select doi from sides)"""):
            print(f"  {row[0]}  {row[1][:90]}")


if __name__ == "__main__":
    main()
