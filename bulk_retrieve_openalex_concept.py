#!/usr/bin/env python3
"""Bulk-retrieve an entire OpenAlex-classified field via its `concepts.id`
tag, instead of hoping a hand-picked keyword list covers a field's real
vocabulary -- the same "--whole-X instead of guessing keywords" idea as
bulk_retrieve_crossref.py's --whole-prefix (whole publisher catalog) and
bulk_retrieve_author_works.py (whole author bibliography), applied to a
whole ACADEMIC FIELD this time.

Why this exists: the field-diversification effort (hindawi/, nursing/,
anthropology/ -- see todo.md) initially grew each new corpus by a dozen or
so hand-picked Crossref/DataCite keywords per field, which works but tops
out in the thousands, not the ~100K scale wanted. OpenAlex tags every work
with a field/subfield classification (its "concepts" -- e.g. "Anthropology"
is concepts.id C19165224, ~2.1M works; the vast majority of that IS
cultural/social anthropology specifically, with the narrower
biological/forensic-anthropology sub-concepts each only in the low
thousands) -- searching `works?filter=concepts.id:<id>` pulls a field's
FULL catalog directly, no keyword-vocabulary guessing needed at all.

Deliberately does NOT apply filter_low_relevance_papers.py's
is_on_topic_title() (unlike bulk_retrieve_author_works.py, which does) --
that term list is computer-ethics-specific, and here the concept filter
ITSELF is the relevance mechanism (OpenAlex's own field classification, not
a guessed keyword match). filter_low_relevance_papers.py's CRANK detector
(--skip-offtopic) remains a separate, field-agnostic step to run after
retrieval, same as every other new-field batch this session.

OA resolution: OpenAlex's own work object (from the SAME concept-search
response, no extra request) already carries `best_oa_location`/`open_access`
-- reused exactly as retrieve_papers.parse_openalex_work() extracts it, fed
into bulk_retrieve_crossref.py's own `fetch_candidate()` as a pre-resolved
`openalex_hits` entry, same technique as bulk_retrieve_author_works.py.

Dedup: bulk_retrieve_crossref.py's `load_known_dois()` against whichever
--db this run targets -- naturally scoped per-field-directory (hindawi/,
nursing/, anthropology/ each have their own state.sqlite3), same as every
other bulk script here.

Requires OPENALEX_API_KEY to be practical at this scale -- OpenAlex's
anonymous daily budget was confirmed live (2026-08-27) to run out well
before reaching real field-catalog volume; see retrieve_papers.py's
OPENALEX_API_KEY_ENV_VAR comment. Without one set, this still runs
(anonymous), just liable to stall out mid-harvest the same way any other
OpenAlex-dependent script here does when the budget/rate-limit is hit
(logs a warning, keeps whatever was already harvested, moves on -- doesn't
crash).

Usage:
    python3 bulk_retrieve_openalex_concept.py --email you@your-institution.edu \\
        --concept-id C19165224 --db anthropology/state.sqlite3 --outdir anthropology/papers \\
        --max-results 50000 --dry-run
"""

import argparse
import logging
import os
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import retrieve_papers as rp

logger = logging.getLogger("bulk_retrieve_openalex_concept")


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


def parse_args():
    p = argparse.ArgumentParser(description="Bulk-retrieve an entire OpenAlex-classified field via concepts.id.")
    p.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent + mailto=")
    p.add_argument("--concept-id", required=True, action="append", dest="concept_ids",
                    help="OpenAlex concept id (repeatable), e.g. C19165224 for Anthropology -- look up via "
                         "https://api.openalex.org/concepts?search=<field name>")
    p.add_argument("--outdir", type=Path, default=Path("papers"))
    p.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    p.add_argument("--max-results", type=int, default=50_000,
                    help="Stop harvesting candidates per concept after this many OpenAlex results (default 50,000)")
    p.add_argument("--type", default=None,
                    help="OpenAlex work type to restrict to (e.g. 'dissertation') -- combined with each "
                         "--concept-id as filter=concepts.id:<id>,type:<type>. Added 2026-08-30: a "
                         "concept's full result set can be too large to harvest completely in one run "
                         "(a network interruption or a practical time budget can cut it short -- see "
                         "todo.md), which silently under-covers whatever type of work happens to be a "
                         "minority of that concept, like dissertations usually are relative to journal "
                         "articles. A separate type-scoped pass over the same concept is enough smaller "
                         "to harvest to completion on its own, guaranteeing full coverage of that type "
                         "regardless of whether the broader untyped harvest ever finishes. Omitted "
                         "(default): no type restriction, unchanged from before this flag existed.")
    p.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    p.add_argument("--max-workers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true", help="Harvest and report counts; query Unpaywall/download nothing")
    p.add_argument("--min-interval-openalex", type=float, default=0.3)
    p.add_argument("--min-interval-unpaywall", type=float, default=1.0)
    p.add_argument("--min-interval-download", type=float, default=0.5)
    p.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    p.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    p.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_openalex_concept.log"))
    p.add_argument("--no-key-ok", action="store_true",
                    help="Accepted but otherwise unused here -- exists so the require_api_keys.py "
                         "PreToolUse hook's documented escape hatch (append this flag to run "
                         "anonymously/slower without the usual API key) is valid argv for this "
                         "script instead of an 'unrecognized arguments' error.")
    return p.parse_args()


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)

    session = requests.Session()
    api_key = os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR)
    session.headers["User-Agent"] = f"dupefinder-bulk-retrieve-openalex-concept/1.0 (mailto:{args.email})"
    logger.info("%s", "using OPENALEX_API_KEY" if api_key else
                "anonymous, no OPENALEX_API_KEY set -- liable to hit the (real, confirmed) daily budget wall early")
    openalex_limiter = rp.RateLimiter(min_interval=args.min_interval_openalex)

    seen_dois = set(known_dois)
    candidates, openalex_hits = [], {}
    for concept_id in args.concept_ids:
        filter_str = f"concepts.id:{concept_id}"
        if args.type:
            filter_str += f",type:{args.type}"
        found = new = 0
        try:
            for work in rp.search_openalex_works(session, openalex_limiter, filter_str,
                                                  args.max_results, args, logger):
                found += 1
                paper = rp.parse_openalex_work(work)
                if paper is None or paper["doi"].lower() in seen_dois:
                    continue
                seen_dois.add(paper["doi"].lower())
                candidates.append(paper)
                if paper["pdf_url"]:
                    openalex_hits[paper["doi"].lower()] = {"oa_status": paper["oa_status"], "pdf_url": paper["pdf_url"]}
                new += 1
        except rp.RateLimited as exc:
            logger.warning("stopped harvesting after concept %r: %s -- keeping the %d candidate(s) already "
                            "found this run (set %s for a much higher rate limit)",
                            concept_id, exc, len(candidates), rp.OPENALEX_API_KEY_ENV_VAR)
            break
        except rp.RetrievalError as exc:
            logger.warning("stopped harvesting after concept %r: %s -- keeping the %d candidate(s) already found this run",
                            concept_id, exc, len(candidates))
            break
        logger.info("  concept=%s: %d found, %d new", concept_id, found, new)

    logger.info("total: %d new candidate(s) across %d concept(s) (not yet checked against state.sqlite3)",
                len(candidates), len(args.concept_ids))
    if args.dry_run:
        logger.info("--dry-run: stopping before any Unpaywall/download activity")
        return

    if args.max_papers is not None:
        candidates = candidates[:args.max_papers]

    unpaywall_limiter = rp.RateLimiter(min_interval=args.min_interval_unpaywall)
    download_limiter = rp.RateLimiter(min_interval=args.min_interval_download)
    thread_store = rp.ThreadLocalPaperStore(args.db)
    tasks = [(session, paper, thread_store, unpaywall_limiter, download_limiter, args, openalex_hits)
             for paper in candidates]
    downloaded = 0
    for _, result in rp.concurrent_fetch(tasks, lambda t: brc.fetch_candidate(*t), max_workers=args.max_workers,
                                          max_successes=args.max_papers, logger=logger):
        if result == "downloaded":
            downloaded += 1
            if downloaded % 25 == 0:
                logger.info("downloaded %d so far", downloaded)

    logger.info("done. downloaded %d new paper(s)", downloaded)


if __name__ == "__main__":
    main()
