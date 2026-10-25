"""Look up DOIs for title-only General Index documents, with retrieve_papers.resolve_doi().

Documents with no slice metadata but a title (and often authors) in the 2022 update sit in
meta_alt with no DOI (gi_load_meta_alt.py). This runs retrieve_papers.py's own Crossref
bibliographic search on their title + authors, accepted only at title similarity >=
--title-match-threshold (0.82, the same bar retrieve_papers.py uses), and stores the result in
meta_alt.resolved_doi / resolved_score.

--controls N first runs the same lookup on N documents whose DOI the slice metadata already has,
and reports how often the lookup returns that DOI. Optimistic: those titles are the cleaner
publisher titles, not the text-extracted ones meta_alt holds.

Usage: gi_resolve_titles.py META_DB --email ADDR (--dkeys DKEYS_JSON | --controls N) [--interval 0.3]
"""
import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402


def lookup(session, limiter, args, logger, title, author):
    authors = [a.strip() for a in (author or "").replace(" and ", ";").split(";") if a.strip()][:3]
    try:
        return rp.resolve_doi(session, {"title": title, "authors": authors}, limiter, args, logger)
    except rp.RetrievalError as exc:
        logger.warning("lookup failed for %r: %s", title[:60], exc)
        return None, 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meta_db")
    ap.add_argument("--email", required=True)
    ap.add_argument("--dkeys", help="JSON list of dkeys to resolve (must be in meta_alt)")
    ap.add_argument("--controls", type=int, default=0)
    ap.add_argument("--interval", type=float, default=0.3)
    ap.add_argument("--title-match-threshold", type=float, default=0.82)
    args = ap.parse_args()
    args.max_retries, args.timeout = 3, 60
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("gi_resolve_titles")
    db = sqlite3.connect(args.meta_db, timeout=120)
    session, limiter = requests.Session(), rp.RateLimiter(args.interval)

    if args.controls:
        rows = db.execute("select doi, title, author from meta where doi is not null and title is not null "
                          "and length(title) > 25 and title not like '%[%' and title not like '%||%' "
                          "order by random() limit ?", (args.controls,)).fetchall()
        found = correct = 0
        for i, (doi, title, author) in enumerate(rows, 1):
            got, _ = lookup(session, limiter, args, logger, title, author)
            found += bool(got)
            correct += bool(got) and got.lower() == doi.lower()
            if i % 50 == 0:
                print(f"{i} controls: {found} resolved, {correct} correct", flush=True)
        print(f"controls: {len(rows)} looked up, {found} resolved, {correct} correct "
              f"(precision {100 * correct / max(found, 1):.0f}%, recall {100 * correct / max(len(rows), 1):.0f}%)")
        return

    # Only here, not for --controls: adding columns while gi_load_meta_alt.py is still inserting
    # 6-value rows into meta_alt would break its inserts.
    for col in ("resolved_doi text", "resolved_score real"):
        try:
            db.execute(f"alter table meta_alt add column {col}")
        except sqlite3.OperationalError:
            pass
    dkeys = json.load(open(args.dkeys))
    done = resolved = 0
    for k in dkeys:
        row = db.execute("select title, author from meta_alt where dkey = ? and resolved_score is null",
                         (k,)).fetchone()
        if not row or not row[0]:
            continue
        got, score = lookup(session, limiter, args, logger, row[0], row[1])
        db.execute("update meta_alt set resolved_doi = ?, resolved_score = ? where dkey = ?",
                   (got.lower() if got else None, score, k))
        done += 1
        resolved += bool(got)
        if done % 100 == 0:
            db.commit()
            print(f"{done:,} looked up, {resolved:,} resolved", flush=True)
    db.commit()
    print(f"done: {done:,} looked up, {resolved:,} resolved")


if __name__ == "__main__":
    main()
