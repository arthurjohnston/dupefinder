#!/usr/bin/env python3
"""One-off, targeted variant of bulk_retrieve_author_works.py for a specific, small set of
authors flagged by a hand-verified potential_dupes finding -- not a general-purpose tool.

Why this exists instead of just running resolve_author_openalex_ids.py +
bulk_retrieve_author_works.py: both of those operate corpus-wide and gate on an author
already having several papers in the corpus (resolve_author_openalex_ids.py's identity
resolution needs >=2 papers to vote across for real corroboration; bulk_retrieve_author_works.py
defaults to only chasing authors with >=3 corpus papers already, specifically to avoid
pulling in tens of thousands of barely-relevant single-paper authors' entire unrelated
careers -- see that script's own docstring). A flagged author worth running this for typically
has exactly ONE paper in the corpus so far (that's what makes them a *candidate* worth
investigating, not evidence already in hand) -- the corpus-wide tools would simply skip them.

This script resolves identity the same way resolve_author_openalex_ids.py does (ask OpenAlex
"who wrote this DOI" via query_openalex_authorships_batch(), match on name similarity --
OpenAlex's own disambiguation, not a guess from a bare name) but against a small (doi,
author_name) target list loaded from --targets-file instead of a corpus-wide query, and with NO
multi-paper corroboration requirement (can't have any, with only one paper each) -- so treat a
resolution here as weaker evidence than resolve_author_openalex_ids.py's own corpus-wide table,
and check the log's name-similarity score for each match before trusting it blindly.

Everything downstream (harvesting an author's full OpenAlex work list, topic-filtering,
downloading) reuses bulk_retrieve_author_works.py's/bulk_retrieve_crossref.py's existing,
tested functions unchanged -- this script is only the identity-resolution + author-list
substitution, not a reimplementation of retrieval.

--targets-file format: a JSON list of {"doi": ..., "author_name": ...} objects (an optional
"note" field is ignored by this script, useful for your own bookkeeping of which case/direction
each row came from). Not tracked in git -- the specific authors/cases being checked are working
data, not code; keep that file wherever your case notes already live.

Usage:
    python3 retrieve_flagged_case_authors.py --email you@your-institution.edu --targets-file authors.json --dry-run
    python3 retrieve_flagged_case_authors.py --email you@your-institution.edu --targets-file authors.json
"""
import argparse
import json
import logging
from pathlib import Path

import requests

import bulk_retrieve_author_works as brc_authors
import bulk_retrieve_crossref as brc
import filter_low_relevance_papers as flrp
import resolve_author_openalex_ids as raoi
import retrieve_papers as rp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("retrieve_flagged_case_authors")


def load_target_authors(targets_file):
    """[(doi, author_name), ...] from a JSON file of {"doi", "author_name", "note"?} objects."""
    with open(targets_file) as f:
        rows = json.load(f)
    return [(row["doi"], row["author_name"]) for row in rows]


def resolve_target_authors(session, rate_limiter, target_authors, args):
    """Returns [(name, openalex_id, match_score), ...] -- one entry per target_authors row
    that OpenAlex has authorship data for AND that name-matched above
    resolve_author_openalex_ids.NAME_MATCH_THRESHOLD. A row that doesn't resolve (DOI unknown
    to OpenAlex, or no authorship name clears the threshold) is logged and skipped -- same
    "false negatives are cheap, false positives are not" discipline as the corpus-wide tool."""
    dois = sorted({doi for doi, _ in target_authors})
    assert len(dois) <= rp.OPENALEX_BATCH_SIZE, (
        f"{len(dois)} unique DOIs exceeds OpenAlex's {rp.OPENALEX_BATCH_SIZE}-per-request batch cap -- "
        "this script assumes the target list is small enough for one request; add chunking if it grows"
    )
    authorships_by_doi = rp.query_openalex_authorships_batch(session, dois, rate_limiter, args, logger)

    resolved = []
    for doi, name in target_authors:
        authorships = authorships_by_doi.get(rp.normalize_doi(doi).lower(), [])
        if not authorships:
            logger.warning("no OpenAlex authorship data for doi=%s (name=%r) -- skipping", doi, name)
            continue
        best = max(authorships, key=lambda a: raoi.name_similarity(name, a["display_name"]))
        score = raoi.name_similarity(name, best["display_name"])
        if score < raoi.NAME_MATCH_THRESHOLD:
            logger.warning("best OpenAlex match for %r (doi=%s) is %r, only score=%.2f (< %.2f threshold) -- skipping",
                            name, doi, best["display_name"], score, raoi.NAME_MATCH_THRESHOLD)
            continue
        logger.info("resolved %r -> %s (matched %r, score=%.2f)", name, best["openalex_id"], best["display_name"], score)
        resolved.append((name, best["openalex_id"], score))
    return resolved


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--email", required=True)
    p.add_argument("--targets-file", type=Path, required=True,
                    help="JSON file of [{\"doi\": ..., \"author_name\": ...}, ...] -- see module docstring")
    p.add_argument("--outdir", type=Path, default=Path("papers"))
    p.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    p.add_argument("--max-per-author", type=int, default=200)
    p.add_argument("--no-topic-filter", action="store_true",
                    help="Off by default: a flagged author's field is often NOT the corpus's own "
                         "topic -- pass this to actually retrieve their unrelated other work, "
                         "which is the whole point here (checking for a pattern), rather than "
                         "having filter_low_relevance_papers.py's title filter discard it")
    p.add_argument("--max-papers", type=int, default=None)
    p.add_argument("--max-workers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-interval-openalex", type=float, default=0.3)
    p.add_argument("--min-interval-unpaywall", type=float, default=1.0)
    p.add_argument("--min-interval-download", type=float, default=0.5)
    p.add_argument("--max-retries", type=int, default=4)
    p.add_argument("--timeout", type=float, default=30.0)
    return p.parse_args()


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    target_authors = load_target_authors(args.targets_file)

    session = requests.Session()
    session.headers["User-Agent"] = f"dupefinder-retrieve-flagged-case-authors/1.0 (mailto:{args.email})"
    openalex_limiter = rp.RateLimiter(min_interval=args.min_interval_openalex)

    resolved = resolve_target_authors(session, openalex_limiter, target_authors, args)
    logger.info("%d/%d target author(s) resolved to an OpenAlex ID", len(resolved), len(target_authors))
    if not resolved:
        logger.info("nothing resolved -- stopping")
        return

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)
    seen_dois = set(known_dois)
    candidates, openalex_hits = [], {}
    offtopic_skipped = 0

    for name, openalex_id, score in resolved:
        found = new = 0
        try:
            for work in brc_authors.search_author_works(session, openalex_limiter, openalex_id, args.max_per_author, args):
                found += 1
                paper = brc_authors.parse_author_work(work)
                if paper is None or paper["doi"].lower() in seen_dois:
                    continue
                if not args.no_topic_filter and not flrp.is_on_topic_title(paper["title"]):
                    offtopic_skipped += 1
                    continue
                seen_dois.add(paper["doi"].lower())
                candidates.append(paper)
                if paper["pdf_url"]:
                    openalex_hits[paper["doi"].lower()] = {"oa_status": paper["oa_status"], "pdf_url": paper["pdf_url"]}
                new += 1
        except rp.RateLimited as exc:
            logger.warning("stopped harvesting after author %r: %s -- keeping %d candidate(s) found so far",
                            name, exc, len(candidates))
            break
        except rp.RetrievalError as exc:
            logger.warning("stopped harvesting after author %r: %s -- keeping %d candidate(s) found so far",
                            name, exc, len(candidates))
            break
        logger.info("  %s (%s, match=%.2f): %d found, %d new", name, openalex_id, score, found, new)

    logger.info("total: %d new candidate(s) across %d resolved author(s) (%d off-topic-by-title skipped)",
                len(candidates), len(resolved), offtopic_skipped)
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

    logger.info("done. downloaded %d new paper(s)", downloaded)


if __name__ == "__main__":
    main()
