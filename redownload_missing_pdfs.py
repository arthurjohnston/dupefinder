#!/usr/bin/env python3
"""Re-fetch the source PDF for specific already-cataloged papers whose file is missing on
disk -- NOT a general retrieval script (see retrieve_papers.py/bulk_retrieve_*.py for that);
this is narrowly for the "we already have this paper's metadata/paragraphs/embeddings in
library.sqlite3, but its PDF file itself is gone from computer-ethics/papers/" gap (~81% of
this corpus's papers.file_path entries don't resolve to a file on disk -- see CLAUDE.md's
memory notes and REVIEWING.md's non-negotiable pdftotext-byline-verification step, which
this gap has been blocking on a large fraction of review candidates).

Deliberately reuses retrieve_papers.py's own OA-resolution/download machinery
(query_unpaywall/download_with_landing_page_fallback/download_pdf) rather than
reimplementing it -- same Unpaywall polite-pool contract, same landing-page-PDF-discovery
fallback for institutional-repository/thesis-style OA locations. Two differences from that
script's own flow, both narrower on purpose:
  1. No Crossref DOI resolution -- every paper this touches already has a DOI on file in
     library.sqlite3 (it was already cataloged), so there's nothing to look up.
  2. An arXiv fast path: `10.48550/arxiv.<id>` DOIs skip Unpaywall entirely and hit
     arxiv.org's own PDF URL directly -- arXiv preprints are always OA and this avoids an
     Unpaywall round-trip (and its 1 req/sec shared-host rate limit) for what's usually a
     large fraction of a batch (see this corpus's own dupe_comparisons/ candidates).

Downloads land at the EXACT path already recorded in library.sqlite3's papers.file_path
(resolved against both the repo root and the corpus dir, whichever the row's existing
convention is -- see CLAUDE.md's note that this project has both styles on file) -- so a
restored file just works with every existing reference to it (pdftotext, compare_two_papers.py,
etc.), no database update needed. A paper with no file_path on file at all is skipped with a
warning rather than inventing a new path/schema convention here.

Usage:
    python3 redownload_missing_pdfs.py --library-db computer-ethics/library.sqlite3 \\
        --repo-root . --email you@your-institution.edu --paper-ids 1040,3094,41476
    python3 redownload_missing_pdfs.py --library-db computer-ethics/library.sqlite3 \\
        --repo-root . --email you@your-institution.edu --scan-dir computer-ethics/dupe_comparisons
"""

import argparse
import glob
import re
from pathlib import Path

import requests

from retrieve_papers import (
    USER_AGENT_TEMPLATE,
    RateLimiter,
    RetrievalError,
    download_with_landing_page_fallback,
    query_unpaywall,
)

import db

DEFAULT_MIN_INTERVAL = 1.0  # matches retrieve_papers.py's own polite-pool pace
ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def resolve_existing_path(repo_root, corpus_dir, file_path):
    """Returns the Path a `file_path` string from papers.file_path actually resolves to,
    trying it both ways this corpus's rows are known to be stored (see CLAUDE.md) -- or None
    if it resolves to neither and a fresh file needs to be written at the corpus-dir-relative
    interpretation (the more common of the two styles)."""
    for base in (repo_root, corpus_dir):
        p = base / file_path
        if p.exists():
            return p
    return None


def dest_path_for(repo_root, corpus_dir, file_path):
    """Where to WRITE a newly-downloaded file for this file_path -- corpus-dir-relative
    unless the string itself already starts with the corpus dir's own name (the other style
    on file), matching resolve_existing_path()'s two conventions."""
    if file_path.startswith(corpus_dir.name + "/"):
        return repo_root / file_path
    return corpus_dir / file_path


def collect_paper_ids_from_reports(paths):
    """Extracts every paper_id referenced in one or more batch_compare_papers.py-style
    plain-text reports (looks for that script's own "PAPER PAIR: A vs B" header line)."""
    ids = set()
    for path in paths:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"PAPER PAIR:\s*(\d+)\s*vs\s*(\d+)", text):
            ids.add(int(m.group(1)))
            ids.add(int(m.group(2)))
    return ids


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."),
                         help="repo root file_path strings are sometimes stored relative to")
    parser.add_argument("--email", required=True, help="contact email for Crossref/Unpaywall's polite pool")
    parser.add_argument("--paper-ids", help="comma-separated paper_id list")
    parser.add_argument("--scan-dir", help="directory of batch_compare_papers.py .txt reports -- every "
                                            "paper_id mentioned in any of them is a candidate (glob: *.txt)")
    parser.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL,
                         help=f"minimum seconds between requests to the same host (default {DEFAULT_MIN_INTERVAL})")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--overwrite", action="store_true", help="re-download even if a file already exists")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    corpus_dir = args.library_db.resolve().parent

    if args.paper_ids:
        paper_ids = {int(x) for x in args.paper_ids.split(",")}
    elif args.scan_dir:
        paper_ids = collect_paper_ids_from_reports(glob.glob(str(Path(args.scan_dir) / "*.txt")))
    else:
        raise SystemExit("pass --paper-ids or --scan-dir")

    print(f"{len(paper_ids)} paper(s) to check")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT_TEMPLATE.format(email=args.email)})
    rate_limiter = RateLimiter(args.min_interval)

    present = missing_no_doi = missing_no_path = 0
    outcomes = {}  # outcome label -> count
    for pid in sorted(paper_ids):
        row = conn.execute("SELECT title, doi, file_path FROM papers WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        title, doi, file_path = row
        if not file_path:
            missing_no_path += 1
            print(f"  [{pid}] no file_path on file at all, skipping: {title[:70]!r}")
            continue
        if not args.overwrite and resolve_existing_path(args.repo_root, corpus_dir, file_path):
            present += 1
            continue
        if not doi:
            missing_no_doi += 1
            print(f"  [{pid}] no DOI on file, can't resolve an OA location: {title[:70]!r}")
            continue

        dest = dest_path_for(args.repo_root, corpus_dir, file_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        m = ARXIV_DOI_RE.match(doi)
        pdf_url = f"https://arxiv.org/pdf/{m.group(1)}.pdf" if m else None
        if pdf_url is None:
            try:
                data = query_unpaywall(session, doi, rate_limiter, args, _Logger())
            except RetrievalError as exc:
                outcomes["unpaywall error"] = outcomes.get("unpaywall error", 0) + 1
                print(f"  [{pid}] unpaywall error ({doi}): {exc}")
                continue
            if data is None:
                outcomes["doi_unknown_to_unpaywall"] = outcomes.get("doi_unknown_to_unpaywall", 0) + 1
                print(f"  [{pid}] DOI unknown to unpaywall: {doi}")
                continue
            best_loc = data.get("best_oa_location") or {}
            pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")
            if not pdf_url:
                outcomes["no_oa"] = outcomes.get("no_oa", 0) + 1
                print(f"  [{pid}] no OA location (oa_status={data.get('oa_status')}): {doi}")
                continue

        try:
            ok, actual_url = download_with_landing_page_fallback(session, pdf_url, dest, rate_limiter, args, _Logger())
        except RetrievalError as exc:
            outcomes["download error"] = outcomes.get("download error", 0) + 1
            print(f"  [{pid}] download error ({pdf_url}): {exc}")
            continue
        if ok:
            outcomes["downloaded"] = outcomes.get("downloaded", 0) + 1
            print(f"  [{pid}] downloaded -> {dest}")
        else:
            outcomes["url_not_pdf"] = outcomes.get("url_not_pdf", 0) + 1
            print(f"  [{pid}] URL did not serve a PDF: {pdf_url}")

    print()
    print(f"already present: {present}  no file_path on file: {missing_no_path}  no DOI on file: {missing_no_doi}")
    for label, count in sorted(outcomes.items(), key=lambda x: -x[1]):
        print(f"  {label}: {count}")
    conn.close()


class _Logger:
    """Minimal stand-in for retrieve_papers.py's real logger -- this script prints its own
    per-paper outcome lines directly, so http_get()'s internal retry/backoff messages (the
    only thing that actually calls this) just go nowhere rather than doubling up output."""
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


if __name__ == "__main__":
    main()
