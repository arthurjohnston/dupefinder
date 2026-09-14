#!/usr/bin/env python3
"""Bulk-retrieve papers Retraction Watch's own curated database confirms were
retracted specifically for plagiarism -- into the same state.sqlite3 manifest
every other retrieve script uses.

Why this exists: every other bulk_retrieve_*.py script finds *candidates* --
papers that MIGHT be duplicates, which then need LSH/cosine/review to confirm.
This script is different: every paper it downloads is already a CONFIRMED
real plagiarism case, curated and manually checked by Retraction Watch itself
(not dupefinder's own heuristics). The project's single best-documented
ground-truth case so far (Saxby/Taro, tests/cases/saxby-taro-2023.json) came
from exactly this kind of source -- a real, externally-investigated case --
found by hand, one at a time. Retraction Watch's dataset has thousands more
of exactly that pattern, sitting in one file.

Source: Crossref now hosts the Retraction Watch database directly
(https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/),
a CSV updated on working days, CC-BY 4.0, no API key or registration needed:
https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv
(~66MB, ~72K rows as of 2026-08). Fetched fresh by default (--csv-path to use
a local copy instead, --refresh to force a re-fetch even if a local copy
exists).

**Important limitation, confirmed by inspecting the real data, not assumed:**
the CSV's `OriginalPaperDOI` column is the retracted paper's OWN DOI (the
article itself, as distinct from `RetractionDOI`, the separate retraction
NOTICE's DOI) -- NOT a pointer to whatever it plagiarized. There is no
structured field anywhere in this dataset linking a plagiarizing paper to its
source. This script therefore only gets you the confirmed-plagiarizing side
of each pair, not an automatic pair -- see write_retraction_metadata() for
what's preserved to make manual/AI investigation of the source easier later
(the Notes field occasionally has a PubPeer link or investigation-committee
detail, the Title/Journal/Author help a manual search), and the `--reason`
filter for narrowing to the strongest signal.

Reuses bulk_retrieve_crossref.py's fetch_candidate() (same OpenAlex-batch-
then-Unpaywall-fallback pipeline every other bulk script uses) and
retrieve_papers.py's OA-resolution/download machinery directly -- this script
is just a different way of building the candidate list (a curated CSV instead
of a keyword search), everything downstream is identical.
"""

import argparse
import csv
import json
import logging
import os
import re
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import retrieve_papers as rp

CSV_URL = "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"
DEFAULT_CSV_PATH = Path("retraction_watch.csv")
DEFAULT_REASON = "Plagiarism of/in Article"
METADATA_OUT_PATH = Path("retraction_watch_plagiarism_cases.json")

logger = logging.getLogger("bulk_retrieve_retraction_watch")


def setup_logger(log_file: Path):
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)


def fetch_csv(csv_path: Path, refresh: bool, timeout: float):
    if csv_path.exists() and not refresh:
        logger.info("using existing %s (pass --refresh to re-fetch)", csv_path)
        return
    logger.info("fetching Retraction Watch dataset from %s ...", CSV_URL)
    resp = requests.get(CSV_URL, timeout=timeout, stream=True)
    resp.raise_for_status()
    tmp_path = csv_path.with_suffix(csv_path.suffix + ".part")
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    tmp_path.rename(csv_path)
    logger.info("saved %s (%d bytes)", csv_path, csv_path.stat().st_size)


def load_plagiarism_cases(csv_path: Path, reason_filter: str):
    """Yields dicts for every row whose Reason field contains `reason_filter`
    and has a usable OriginalPaperDOI -- confirmed against the real dataset
    that ~99% of "Plagiarism of/in Article" rows do (2848/2884 as of
    2026-08-22)."""
    with open(csv_path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if reason_filter not in (row.get("Reason") or ""):
                continue
            doi = rp.normalize_doi((row.get("OriginalPaperDOI") or "").strip())
            if not doi:
                continue
            authors = [a.strip() for a in (row.get("Author") or "").split(";") if a.strip()]
            # Format observed in the real data: "M/D/YYYY H:MM" -- just want the year.
            # A plain split-on-"/" is too fragile (the trailing " H:MM" time component
            # rides along with the last split fragment, e.g. "2025 0:00", which isn't a
            # clean 4-digit token) -- confirmed as a real bug this way, not hypothetical.
            date = (row.get("OriginalPaperDate") or "").strip()
            year_match = re.search(r"\b(\d{4})\b", date)
            year = int(year_match.group(1)) if year_match else None
            yield {
                "doi": doi,
                "title": (row.get("Title") or "").strip(),
                "authors": authors,
                "year": year,
                "reason": row.get("Reason", ""),
                "notes": row.get("Notes", ""),
                "journal": row.get("Journal", ""),
                "retraction_doi": row.get("RetractionDOI", ""),
                "retraction_date": row.get("RetractionDate", ""),
                "urls": row.get("URLS", ""),
            }


def write_retraction_metadata(cases, out_path: Path):
    """Preserves what Retraction Watch itself recorded about each case --
    title/journal/author/notes/retraction-notice DOI -- keyed by the
    retracted paper's own DOI, for later manual/AI investigation of what it
    plagiarized (this dataset has no structured pointer to that, see module
    docstring). Not consumed by any other script; a reference file for
    whoever picks up that investigation."""
    by_doi = {c["doi"].lower(): c for c in cases}
    out_path.write_text(json.dumps(by_doi, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote retraction metadata for %d case(s) to %s", len(by_doi), out_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bulk-retrieve papers Retraction Watch confirms were retracted for plagiarism.")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent + mailto=")
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_CSV_PATH,
                         help=f"Local path for the Retraction Watch CSV (default {DEFAULT_CSV_PATH})")
    parser.add_argument("--refresh", action="store_true",
                         help="Re-fetch the CSV even if a local copy already exists (dataset is updated on working days)")
    parser.add_argument("--reason", default=DEFAULT_REASON,
                         help=f"Only rows whose Reason field contains this substring (default {DEFAULT_REASON!r} -- "
                              "the strongest full-text-copying signal; try 'Plagiarism' alone for a broader sweep "
                              "that also catches image/data plagiarism)")
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--metadata-out", type=Path, default=METADATA_OUT_PATH)
    parser.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="Fetch/filter the CSV and report counts; download nothing")
    parser.add_argument("--min-interval-unpaywall", type=float, default=1.0)
    parser.add_argument("--min-interval-openalex", type=float, default=0.3)
    parser.add_argument("--no-openalex", action="store_true")
    parser.add_argument("--min-interval-download", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_retraction_watch.log"))
    return parser.parse_args()


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    fetch_csv(args.csv_path, args.refresh, args.timeout)

    cases = list(load_plagiarism_cases(args.csv_path, args.reason))
    logger.info("%d row(s) match Reason contains %r", len(cases), args.reason)
    write_retraction_metadata(cases, args.metadata_out)

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    unpaywall_limiter = rp.RateLimiter(args.min_interval_unpaywall)
    openalex_limiter = rp.RateLimiter(args.min_interval_openalex)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)
    seen_dois = set()
    candidates = []
    for c in cases:
        doi_lower = c["doi"].lower()
        if doi_lower in seen_dois or doi_lower in known_dois:
            continue
        seen_dois.add(doi_lower)
        candidates.append(c)
    logger.info("%d new candidate(s) (not already in %s)", len(candidates), args.db)

    if args.dry_run:
        logger.info("--dry-run: stopping before any Unpaywall/OpenAlex/download activity")
        store.conn.close()
        return
    store.conn.close()

    openalex_hits = {}
    if not args.no_openalex and candidates:
        dois = [c["doi"] for c in candidates]
        n_batches = (len(dois) + rp.OPENALEX_BATCH_SIZE - 1) // rp.OPENALEX_BATCH_SIZE
        has_key = bool(os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR))
        logger.info("pre-resolving OA locations for %d candidate(s) via OpenAlex (%d batch(es) of up to %d, %s)...",
                    len(dois), n_batches, rp.OPENALEX_BATCH_SIZE,
                    "using OPENALEX_API_KEY" if has_key else "anonymous, no OPENALEX_API_KEY set")
        for batch in rp.chunked(dois, rp.OPENALEX_BATCH_SIZE):
            try:
                openalex_hits.update(rp.query_openalex_batch(session, batch, openalex_limiter, args, logger))
            except rp.RateLimited:
                remaining = len(dois) - len(openalex_hits)
                logger.warning("OpenAlex rate-limited us -- stopping OpenAlex lookups for the rest of this "
                                "run, %d remaining candidate(s) will use per-DOI Unpaywall instead", remaining)
                break
        logger.info("OpenAlex resolved %d/%d candidate(s) directly -- %d will fall back to per-DOI Unpaywall",
                    len(openalex_hits), len(dois), len(dois) - len(openalex_hits))

    thread_store = rp.ThreadLocalPaperStore(args.db)
    tasks = [(session, c, thread_store, unpaywall_limiter, download_limiter, args, openalex_hits) for c in candidates]
    downloaded = 0
    for _, result in rp.concurrent_fetch(tasks, lambda t: brc.fetch_candidate(*t), max_workers=args.max_workers,
                                          max_successes=args.max_papers, logger=logger):
        if result == "downloaded":
            downloaded += 1
            if downloaded % 25 == 0:
                logger.info("downloaded %d so far", downloaded)

    logger.info("done. downloaded %d new confirmed-plagiarism paper(s)", downloaded)


if __name__ == "__main__":
    main()
