#!/usr/bin/env python3
"""Rescues already-failed state.sqlite3 candidates (status != 'downloaded')
via Europe PMC as a fallback OA channel, distinct from and complementary to
retrieve_papers.py's OpenAlex/Unpaywall-based resolution.

Why this exists: a bulk_retrieve_crossref.py run against Hindawi/Wiley
journals (--doi-prefix 10.1155, --container-title targeting journals named
in Wiley's 2023-2024 mass-retraction disclosure -- see todo.md) found
Unpaywall/OpenAlex's on-file OA location for these DOIs is almost always
either the legacy `downloads.hindawi.com` CDN or `onlinelibrary.wiley.com`
directly, and BOTH are now behind active Cloudflare bot-protection --
confirmed live (HTTP 403 on the landing page itself, before
retrieve_papers.py's citation_pdf_url landing-page fallback ever gets a
chance to run, since that needs a real HTML response first). 13,481 of
14,246 candidates in that run failed this way. Not a bug to fix -- a
publisher's deliberate anti-bulk-access control, not attempted to be
bypassed here.

Europe PMC (europepmc.org, EBI/EMBL's free public full-text index,
distinct from and broader than NCBI's own PMC -- it separately mirrors NIH
PMC's own content plus additional European-funded-research deposits) is a
genuinely different, independent OA channel: many journals (Hindawi's
CC-BY content very much included) get independently deposited there
regardless of what the publisher's own site allows, and Europe PMC's own
CDN has no comparable bot-protection observed. Live-validated on a real
failed-DOI sample from that run: 2/5 had a Europe PMC PMC ID with a direct
`?pdf=render` OA PDF link (`availabilityCode: "OA"`, real CC BY license).

Coverage will be uneven and skewed toward biomedical/life-science content
-- Europe PMC only indexes journals in PubMed's own scope, so a pure math/
CS/engineering Hindawi journal (Mathematical Problems in Engineering,
Security and Communication Networks) will have much lower hit rates than a
biomedical-adjacent one (Journal of Healthcare Engineering,
Computational Intelligence and Neuroscience) -- expected, not a bug.

Batched (`DOI:a OR DOI:b OR ...`, BATCH_SIZE DOIs/request -- confirmed
live this works and returns each hit's own doi/pmcid pair, no per-DOI
round-trip needed), same reasoning as retrieve_papers.py's
query_openalex_batch(): the whole point is not paying one HTTP round-trip
per DOI at bulk-retrieval scale.

Resumable the same way every other bulk script here is: upserts the SAME
state.sqlite3 key (`doi:<doi>`) an existing failed row already used, via
retrieve_papers.make_key()/PaperStore -- a rescue is a correction to the
existing row (status error/no_oa/oa_url_not_pdf -> downloaded), not a new
candidate. Only ever touches rows whose current status is in
--rescue-statuses (default: error, no_oa, oa_url_not_pdf) -- never
re-attempts an already-`downloaded` row, and never touches an
`excluded_crank`/`excluded_offtopic` row (see filter_low_relevance_papers.py
-- same "a deliberate exclusion is not the same as a retrieval failure"
distinction bulk_retrieve_crossref.py's load_known_dois() makes).

Usage:
    python3 rescue_via_europepmc.py --db hindawi/state.sqlite3 --outdir hindawi/papers --dry-run
    python3 rescue_via_europepmc.py --db hindawi/state.sqlite3 --outdir hindawi/papers
"""

import argparse
import logging
from pathlib import Path

import requests

import retrieve_papers as rp

logger = logging.getLogger("rescue_via_europepmc")

EUROPEPMC_SEARCH_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
BATCH_SIZE = 25
DEFAULT_RESCUE_STATUSES = ("error", "no_oa", "oa_url_not_pdf")


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


def load_rescue_candidates(conn, statuses):
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT key, doi, title FROM papers WHERE status IN ({placeholders}) AND doi IS NOT NULL", statuses
    ).fetchall()
    return [{"key": key, "doi": doi, "title": title} for key, doi, title in rows]


def query_europepmc_batch(session, dois, rate_limiter, args):
    """Returns {doi.lower(): pdf_url} for every DOI in this batch Europe PMC
    has an open-access PDF for (availabilityCode 'OA', documentStyle 'pdf').
    A DOI with no record, or a record with no OA PDF, is simply absent."""
    query = " OR ".join(f'DOI:"{doi}"' for doi in dois)
    params = {"query": query, "format": "json", "pageSize": len(dois), "resultType": "core"}
    try:
        resp = rp.http_get(session, EUROPEPMC_SEARCH_API, params, rate_limiter=rate_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger)
    except rp.RetrievalError as exc:
        logger.warning("europepmc batch lookup failed (%d DOIs): %s", len(dois), exc)
        return {}
    if resp.status_code != 200:
        logger.warning("europepmc batch lookup HTTP %d (%d DOIs)", resp.status_code, len(dois))
        return {}

    results = {}
    for record in resp.json().get("resultList", {}).get("result", []):
        doi = record.get("doi")
        if not doi:
            continue
        for url_entry in (record.get("fullTextUrlList") or {}).get("fullTextUrl", []):
            if url_entry.get("availabilityCode") == "OA" and url_entry.get("documentStyle") == "pdf":
                results[doi.lower()] = url_entry["url"]
                break
    return results


def parse_args():
    p = argparse.ArgumentParser(description="Rescue failed state.sqlite3 candidates via Europe PMC as a fallback OA source.")
    p.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent "
                   "(required by Europe PMC's/most APIs' polite-pool terms of use -- no default, "
                   "every caller must supply their own)")
    p.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    p.add_argument("--outdir", type=Path, default=Path("papers"))
    p.add_argument("--rescue-statuses", nargs="+", default=list(DEFAULT_RESCUE_STATUSES),
                    help=f"Only attempt candidates currently in one of these statuses (default: {list(DEFAULT_RESCUE_STATUSES)})")
    p.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many rescued papers")
    p.add_argument("--dry-run", action="store_true", help="Look up Europe PMC coverage; download nothing")
    p.add_argument("--min-interval", type=float, default=1.0, help="Seconds between Europe PMC requests")
    p.add_argument("--min-interval-download", type=float, default=0.5)
    p.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    p.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    p.add_argument("--log-file", type=Path, default=Path("rescue_via_europepmc.log"))
    return p.parse_args()


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    store = rp.PaperStore(args.db)
    candidates = load_rescue_candidates(store.conn, args.rescue_statuses)
    logger.info("%d candidate(s) in status %s to check against Europe PMC", len(candidates), args.rescue_statuses)

    session = requests.Session()
    session.headers["User-Agent"] = f"dupefinder-rescue-via-europepmc/1.0 (mailto:{args.email})"
    rate_limiter = rp.RateLimiter(min_interval=args.min_interval)
    download_limiter = rp.RateLimiter(min_interval=args.min_interval_download)

    pdf_by_doi = {}
    for batch in rp.chunked(candidates, BATCH_SIZE):
        dois = [c["doi"] for c in batch]
        pdf_by_doi.update(query_europepmc_batch(session, dois, rate_limiter, args))

    hits = [c for c in candidates if c["doi"].lower() in pdf_by_doi]
    logger.info("%d/%d candidate(s) have an Europe PMC open-access PDF available", len(hits), len(candidates))
    if args.dry_run:
        logger.info("--dry-run: stopping before any download activity")
        return

    if args.max_papers is not None:
        hits = hits[:args.max_papers]

    rescued = 0
    for candidate in hits:
        pdf_url = pdf_by_doi[candidate["doi"].lower()]
        dest_path = args.outdir / f"{rp.slugify(candidate['title'])}-{rp.slugify(candidate['doi'])}.pdf"
        try:
            ok = rp.download_pdf(session, pdf_url, dest_path, download_limiter, args, logger)
        except rp.RetrievalError as exc:
            logger.error("download error for %r: %s", candidate["title"], exc)
            continue
        if ok:
            logger.info("rescued %r -> %s", candidate["title"], dest_path.name)
            store.upsert(candidate["key"], status="downloaded", pdf_url=pdf_url, file_path=str(dest_path),
                         error=None)
            rescued += 1
        else:
            logger.warning("europepmc URL for %r wasn't a real PDF: %s", candidate["title"], pdf_url)

    logger.info("done. rescued %d/%d candidate(s) via Europe PMC", rescued, len(hits))


if __name__ == "__main__":
    main()
