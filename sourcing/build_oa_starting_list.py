#!/usr/bin/env python3
"""
Build a retrieve_papers.py-compatible starting list from candidates.jsonl,
restricted to legally open-access candidates (is_oa=true, from
enrich_open_access.py) -- and excluding DOIs retrieve_papers.py's own
state.sqlite3 already has settled, so re-running this after a partial or
completed retrieval pass doesn't requeue work that's already done.

Excludes:
  - status='downloaded' (already have the PDF)
  - status='no_oa' (Unpaywall already confirmed no OA copy last time --
    matches retrieve_papers.py's own no-op skip condition for that status)
retrieve_papers.py does not skip 'error'/'oa_url_not_pdf'/
'doi_unknown_to_unpaywall' on its own (it retries those every run), so
those are deliberately left in the output list by default -- pass
--exclude-transient-failures to also drop them if you'd rather not retry.

Also carries forward oa_url/oa_status/oa_alt_urls (from enrich_open_access.py)
so retrieve_papers.py can try OpenAlex's already-known location(s) before
ever calling Unpaywall -- see enrich_open_access.py's docstring for why.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidates", default="output/candidates.jsonl")
    p.add_argument("--state-db", default="../state.sqlite3", help="Path to retrieve_papers.py's state.sqlite3.")
    p.add_argument("--output", default="output/starting_oa_candidates.json")
    p.add_argument(
        "--exclude-transient-failures",
        action="store_true",
        help="Also exclude DOIs previously marked error/oa_url_not_pdf/doi_unknown_to_unpaywall "
             "(retrieve_papers.py would otherwise retry these every run).",
    )
    args = p.parse_args()

    settled_statuses = {"downloaded", "no_oa"}
    if args.exclude_transient_failures:
        settled_statuses |= {"error", "oa_url_not_pdf", "doi_unknown_to_unpaywall"}

    settled_dois: set[str] = set()
    state_path = Path(args.state_db)
    if state_path.exists():
        conn = sqlite3.connect(str(state_path))
        placeholders = ",".join("?" for _ in settled_statuses)
        rows = conn.execute(
            f"SELECT doi FROM papers WHERE status IN ({placeholders}) AND doi IS NOT NULL",
            tuple(settled_statuses),
        )
        settled_dois = {row[0].lower() for row in rows}
        conn.close()
    else:
        print(f"Warning: {state_path} not found -- not excluding anything already retrieved.")

    all_oa = []
    with open(args.candidates, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("is_oa") and rec.get("doi"):
                all_oa.append(rec)

    remaining = [r for r in all_oa if r["doi"].lower() not in settled_dois]

    # oa_url/oa_status are carried forward from enrich_open_access.py's OpenAlex lookup so
    # retrieve_papers.py can try that URL directly instead of re-querying Unpaywall for a DOI
    # it's already been told is open access -- see retrieve_papers.py's process_paper() docstring
    # comment on known_oa_url. Unpaywall is still queried as a fallback there if this URL turns
    # out to be stale/blocked, so dropping it here (the old behavior) wasn't wrong, just wasteful:
    # every one of these DOIs used to cost a fresh Unpaywall call regardless, and Unpaywall's
    # rate limit (1 req/sec, one shared host, doesn't scale with retrieve_papers.py's own
    # --max-workers) turned out to be the actual bottleneck on a 120k+-paper batch.
    entries = [
        {"title": r.get("title"), "year": r.get("year"), "doi": r.get("doi"),
         "oa_url": r.get("oa_url"), "oa_status": r.get("oa_status"),
         "oa_alt_urls": r.get("oa_alt_urls") or []}
        for r in remaining
    ]
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=0)

    print(f"OA candidates:               {len(all_oa):,}")
    print(f"Already settled in state db: {len(all_oa) - len(remaining):,}")
    print(f"Written to {args.output}:    {len(entries):,}")


if __name__ == "__main__":
    main()
