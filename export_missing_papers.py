#!/usr/bin/env python3
"""Export every state.sqlite3 row retrieve_papers.py couldn't get a PDF for,
as a CSV for manual follow-up (Google Scholar, publisher sites, etc.).

Includes every status except 'downloaded' -- 'error'/'oa_url_not_pdf' are
retried automatically on retrieve_papers.py's own next run (so some of these
will resolve themselves without manual effort), while 'no_oa'/
'doi_unknown_to_unpaywall' are skipped on future runs (barring --recheck) --
the `status` column lets you filter for whichever you'd rather chase by hand
first. See CLAUDE.md's retrieve_papers.py section for what each status means.
"""

import argparse
import csv
import sqlite3
import urllib.parse
from pathlib import Path


def scholar_url(title: str) -> str:
    return "https://scholar.google.com/scholar?q=" + urllib.parse.quote(title or "")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("not_found_papers.csv"))
    args = parser.parse_args()

    conn = sqlite3.connect(args.state_db)
    rows = conn.execute(
        "SELECT title, authors, year, doi, status, error, updated_at FROM papers "
        "WHERE status != 'downloaded' ORDER BY status, title"
    ).fetchall()
    conn.close()

    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "authors", "year", "doi", "status", "error", "google_scholar_search", "updated_at"])
        for title, authors, year, doi, status, error, updated_at in rows:
            writer.writerow([title, authors, year, doi, status, error, scholar_url(title), updated_at])

    by_status = {}
    for row in rows:
        by_status[row[4]] = by_status.get(row[4], 0) + 1

    print(f"Wrote {len(rows):,} row(s) to {args.output}")
    for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"  {status:28s} {count:,}")


if __name__ == "__main__":
    main()
