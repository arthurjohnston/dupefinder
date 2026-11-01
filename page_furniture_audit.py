#!/usr/bin/env python3
"""Re-measure triaged pairs with and without per-page masthead text, to check that a pair's overlap
isn't just two papers from the same journal sharing the same masthead.

Zestera Publications (DOI prefix 10.64751) prints a running masthead, page footer and
received/accepted/published line on every page, and PyMuPDF extraction merges it into the body
text (see compare_two_papers.py's strip_page_furniture()). Two papers from one journal issue share
that text verbatim, so a pair's coverage could in principle be mostly masthead. This reads the
pairs a backlog triage CSV (BACKLOG_TRIAGE_*.csv: verdict, paper_a, paper_b, doi_a, doi_b, ...)
marked with --verdict, keeps those where either DOI has --doi-prefix, and prints each pair's
coverage, longest run and run count measured both ways, with the same settings the triage used.

First run (2026-10-04, 77 Zestera LEAD pairs): coverage moved by at most 6 points, and the longest
run often got LONGER, since the masthead had been splitting runs at page breaks -- the overlap in
those pairs is body text, not masthead. See FLAGGED_CASES_INDEX.txt's LEAD-12 "MASTHEAD CHECK".

    python3 page_furniture_audit.py --library-db computer-ethics/library.sqlite3 \\
        --triage-csv BACKLOG_TRIAGE_2026-10-04.csv
"""

import argparse
import csv
import sqlite3
from pathlib import Path

import compare_two_papers as ctp


def measure(conn, a, b, strip, shingle_size, x_drop):
    """(pct_a, pct_b, longest_run, run_count) for one pair, measured the way rank_paper_pairs.py does."""
    words_a = ctp.load_paper_words(conn, a, strip_furniture=strip)
    words_b = ctp.load_paper_words(conn, b, strip_furniture=strip)
    runs = ctp.find_shingle_matches(words_a, words_b, shingle_size=shingle_size, x_drop=x_drop)
    covered_a, covered_b = set(), set()
    for r in runs:
        covered_a.update(range(r[5], r[6]))
        covered_b.update(range(r[7], r[8]))
    return (round(100 * len(covered_a) / max(1, len(words_a))),
            round(100 * len(covered_b) / max(1, len(words_b))),
            runs[0][0] if runs else 0, len(runs))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--library-db", type=Path, required=True)
    p.add_argument("--triage-csv", type=Path, required=True)
    p.add_argument("--verdict", default="LEAD", help="triage verdict to re-measure (default LEAD)")
    p.add_argument("--doi-prefix", default="10.64751", help="keep pairs where either DOI has this prefix")
    p.add_argument("--shingle-size", type=int, default=10)
    p.add_argument("--x-drop", type=int, default=3)
    args = p.parse_args()

    conn = sqlite3.connect(args.library_db)
    with open(args.triage_csv, newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["verdict"].upper() == args.verdict.upper()
                and args.doi_prefix in r["doi_a"] + r["doi_b"]]
    print(f"{len(rows)} pairs\n{'pair':>15}  {'with masthead':>22}  {'masthead removed':>22}  titles")
    for r in rows:
        a, b = int(r["paper_a"]), int(r["paper_b"])
        before = measure(conn, a, b, False, args.shingle_size, args.x_drop)
        after = measure(conn, a, b, True, args.shingle_size, args.x_drop)
        fmt = lambda m: f"{m[0]:>3}%/{m[1]:>3}% {m[2]:>5}w {m[3]:>4}r"
        print(f"{a:>7}/{b:<7}  {fmt(before):>22}  {fmt(after):>22}  "
              f"{r['title_a'][:40]} | {r['title_b'][:40]}")


if __name__ == "__main__":
    main()
