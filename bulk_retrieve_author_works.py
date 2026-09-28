#!/usr/bin/env python3
"""Bulk-retrieve the rest of a known author's publication list -- the
retrieval half of the author-publication-graph expansion (see
resolve_author_openalex_ids.py's docstring for the identity-resolution half,
which MUST be run first: this script only ever acts on authors already
resolved to a disambiguated OpenAlex author ID in that script's
`author_openalex_ids` table, never on a bare name).

Why this is worth a dedicated source, distinct from every other bulk_retrieve_*.py
script here: every one of those grows the corpus by keyword search, which this
project's own "Full-corpus plagiarism audit" (see todo.md) found has a near-zero
hit rate for landing BOTH sides of a real plagiarism pair together -- the actual
prerequisite for find_duplicates.py/build_dupe_candidates.py to ever see it.
Following a specific author's own publication graph is structurally different: an
author with multiple papers already in this corpus is, almost by definition,
someone this project already has independent reason to care about (prolific in
computer-ethics-adjacent work, or already flagged in a potential_dupes row), and
their other papers are exactly where self-citation/self-reuse and their close
collaborators' work would surface.

Harvest: OpenAlex's `works?filter=author.id:<id>` (cursor-paginated,
OPENALEX_WORKS_PER_PAGE per page), one query per resolved author -- NOT a
keyword search, so DEFAULT_KEYWORDS/topic filtering doesn't apply here at
all; an author already in this corpus is presumed relevant by virtue of
being here, and filter_low_relevance_papers.py remains the backstop for
anything that turns out not to be (see its own docstring). A work with no
DOI is skipped (parse_author_work() returns None) -- CORE (bulk_retrieve_core.py)
is this project's dedicated channel for no-DOI content; keeping this script's
resumability keyed on DOI, like bulk_retrieve_crossref.py/bulk_retrieve_theses.py,
is simpler and those two channels' coverage already overlaps considerably in
practice.

OA resolution: OpenAlex's own work object (from the SAME author-works
response, no extra request) already carries `best_oa_location`/`open_access`
-- reused exactly as query_openalex_batch() parses it, fed into
bulk_retrieve_crossref.py's own `fetch_candidate()` as a pre-resolved
`openalex_hits` entry. A work OpenAlex itself has no OA location for still
gets one real shot via Unpaywall (fetch_candidate()'s existing per-DOI
fallback, unchanged) before being given up on for this run.

Dedup: a global `seen_dois` set across the WHOLE run (not per-author) --
two of our resolved authors can be co-authors of the same external paper,
and `bulk_retrieve_crossref.py`'s `load_known_dois()` for anything already
downloaded under any key project-wide (including a prior filter_low_relevance_papers.py
exclusion -- see that function's own docstring), same as every other bulk
script here.

Two more scope guards added after a live dry-run against all 8,092 authors
resolve_author_openalex_ids.py had resolved (min 2 papers) projected
500K-800K candidates -- almost entirely a resolved author's ENTIRE
unrelated career, not the computer-ethics-adjacent slice this corpus is
actually about, and a wildly different order of magnitude than every other
bulk_retrieve_*.py source (tens to low hundreds):
  1. `--min-papers` (default 3, up from resolve_author_openalex_ids.py's own
     default of 2) -- only chase an author's OTHER work when 3+ of their
     papers are ALREADY independently in the corpus, a stronger signal
     they're actually someone this project has reason to care about, not
     just barely over the resolution floor. Filters against
     `author_openalex_ids.total_papers` directly (no need to re-run
     resolve_author_openalex_ids.py -- that count already reflects each
     author's corpus-paper tally, computed once at resolution time).
  2. Title-relevance filtering AT HARVEST TIME, not just as a post-download
     backstop -- reuses filter_low_relevance_papers.py's own
     `is_on_topic_title()` (same ON_TOPIC_TITLE_TERMS list, same
     generous-inclusion-favored design) to skip a candidate before ever
     spending a download on it, rather than downloading a stranger-topic
     paper and relying on filter_low_relevance_papers.py to catch it
     afterward. filter_low_relevance_papers.py itself remains the backstop
     regardless (its off-topic detector is comfortably idempotent against
     rows this harvest-time check already excluded -- they were simply
     never downloaded, nothing to reconsider).

A third scope guard added 2026-09-13, after a live run resolved -- alongside genuine
computer-ethics scholars (Timnit Gebru, Solon Barocas, Jon Kleinberg, ...) -- a large
number of recurring paper-mill front identities documented in
computer-ethics/flagged_cases/README.md (e.g. "Muthukumaran Vaithianathan," and,
found live in this run, names like "A RAVIKUMAR," "V Ramya," "R. Gopinath"). Their
corpus-paper tally is real (>= --min-papers), so they clear the same bar a genuine
prolific scholar does, but every one of their corpus papers checked was registered
under a `LOW_SCRUTINY_DOI_PREFIXES` prefix (IAEME `10.34218`, Pearl Blue `10.63282`) --
not a topic problem `is_on_topic_title()` would catch (a fabricated paper-mill title
can easily contain "AI"/"algorithm"/etc.), but an *identity* problem: this script's
own premise ("an author already in this corpus is presumed relevant by virtue of
being here") specifically does not hold for a front identity manufactured to pad a
predatory publisher's catalog. `is_paper_mill_identity()` computes, per resolved
author, what fraction of their OWN corpus papers (not the OpenAlex candidates being
considered) fall under a low-scrutiny prefix, and `load_resolved_authors()` excludes
anyone at or above `PAPER_MILL_FRACTION_THRESHOLD` -- `--no-paper-mill-filter` restores
the old unfiltered behavior.

Usage:
    python3 bulk_retrieve_author_works.py --email you@your-institution.edu --dry-run
    python3 bulk_retrieve_author_works.py --email you@your-institution.edu
"""

import argparse
import logging
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import db
import filter_low_relevance_papers as flrp
import retrieve_papers as rp

logger = logging.getLogger("bulk_retrieve_author_works")

# DOI-prefix families this project has independently confirmed, across dozens of cases in
# computer-ethics/flagged_cases/, to be a recurring source of paper-mill republication under
# fabricated/recurring front identities -- IAEME and Pearl Blue. See this module's own docstring
# (scope guard #3) for why an author's corpus-paper *count* alone isn't enough signal that
# they're a real scholar worth chasing the rest of the career of.
LOW_SCRUTINY_DOI_PREFIXES = ("10.34218", "10.63282")

# An author whose corpus papers are at or above this fraction from a low-scrutiny prefix is
# treated as a likely front identity, not a real prolific author who merely happens to have one
# paper there -- deliberately high (not just "a majority") since a genuine scholar's own
# occasional low-scrutiny-venue paper shouldn't disqualify their whole real career from being
# chased; a paper-mill identity checked live during this filter's development had 100% of its
# papers under these two prefixes, with no in-between cases observed.
PAPER_MILL_FRACTION_THRESHOLD = 0.8


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


def paper_mill_fractions(conn, author_ids):
    """{author_id: fraction} -- for each given author_id, what fraction of THEIR OWN corpus
    papers (via paper_authors, not the OpenAlex candidates being considered for download) have
    a DOI under one of LOW_SCRUTINY_DOI_PREFIXES. A paper with no DOI counts toward the
    denominator but never the numerator (an unresolvable case is treated as evidence AGAINST
    paper-mill status, not for it -- see module docstring's scope-guard #3 for why erring
    toward keeping a real author is the right default here). One grouped query for every author
    at once rather than one query per author -- this runs against thousands of resolved authors,
    and paper_authors/papers are both large tables."""
    if not author_ids:
        return {}
    placeholders = ",".join("?" * len(author_ids))
    prefix_case = " OR ".join("p.doi LIKE ?" for _ in LOW_SCRUTINY_DOI_PREFIXES)
    prefix_params = [f"{prefix}%" for prefix in LOW_SCRUTINY_DOI_PREFIXES]
    rows = conn.execute(
        f"""SELECT pa.author_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN {prefix_case} THEN 1 ELSE 0 END) AS low_scrutiny
            FROM paper_authors pa
            JOIN papers p ON p.id = pa.paper_id
            WHERE pa.author_id IN ({placeholders})
            GROUP BY pa.author_id""",
        prefix_params + list(author_ids),
    ).fetchall()
    return {author_id: (low_scrutiny / total if total else 0.0) for author_id, total, low_scrutiny in rows}


def load_resolved_authors(conn, min_total_papers=1, filter_paper_mill=True,
                           paper_mill_threshold=PAPER_MILL_FRACTION_THRESHOLD):
    """{author_id: (name, openalex_id)} for every author resolve_author_openalex_ids.py
    already resolved -- this script never resolves an identity itself. Filters
    on `total_papers` (that author's own corpus-paper tally, computed once at
    resolution time -- see module docstring's scope-guard #1) rather than
    re-counting from paper_authors, so this stays a cheap single-table read.

    `filter_paper_mill` (module docstring's scope-guard #3, on by default) additionally drops
    any author whose OWN corpus papers are at or above `paper_mill_threshold` fraction under a
    LOW_SCRUTINY_DOI_PREFIXES prefix -- a likely paper-mill front identity rather than a genuine
    prolific author, regardless of how many corpus papers cleared `min_total_papers`."""
    rows = conn.execute(
        """SELECT a.id, a.name, o.openalex_id FROM author_openalex_ids o
           JOIN authors a ON a.id = o.author_id
           WHERE o.total_papers >= ?""",
        (min_total_papers,),
    ).fetchall()
    if not filter_paper_mill:
        return {author_id: (name, openalex_id) for author_id, name, openalex_id in rows}
    fractions = paper_mill_fractions(conn, [author_id for author_id, _, _ in rows])
    return {
        author_id: (name, openalex_id)
        for author_id, name, openalex_id in rows
        if fractions.get(author_id, 0.0) < paper_mill_threshold
    }


def parse_author_work(work):
    """Thin wrapper around retrieve_papers.parse_openalex_work() -- kept as its
    own name here since it's this module's public API (existing tests/callers
    use bulk_retrieve_author_works.parse_author_work specifically), but the
    actual field-extraction logic moved to retrieve_papers.py 2026-08-27 once
    bulk_retrieve_openalex_concept.py needed the identical generic logic."""
    return rp.parse_openalex_work(work)


def search_author_works(session, rate_limiter, openalex_id, max_results, args):
    """Thin wrapper around retrieve_papers.search_openalex_works() with
    filter_str=f"author.id:{openalex_id}" -- see that function's docstring
    for the (now-shared) pagination logic. Kept as its own name here for the
    same reason as parse_author_work() above."""
    yield from rp.search_openalex_works(session, rate_limiter, f"author.id:{openalex_id}", max_results, args, logger)


def parse_args():
    p = argparse.ArgumentParser(description="Bulk-retrieve the rest of already-known authors' publication lists.")
    p.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent + mailto=")
    p.add_argument("--library-db", default="library.sqlite3", help="Where author_openalex_ids/authors live")
    p.add_argument("--outdir", type=Path, default=Path("papers"))
    p.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    p.add_argument("--max-per-author", type=int, default=200,
                    help="Stop harvesting one author's works after this many (default 200 -- bounds a single "
                         "prolific author's whole career from dominating one run)")
    p.add_argument("--min-papers", type=int, default=3,
                    help="Only chase authors with at least this many papers already in the corpus (default 3, "
                         "stricter than resolve_author_openalex_ids.py's own default of 2 -- see module "
                         "docstring's scope-guard #1)")
    p.add_argument("--no-topic-filter", action="store_true",
                    help="Disable harvest-time title relevance filtering (module docstring's scope-guard #2) "
                         "-- download every candidate regardless of title, same as before that guard existed")
    p.add_argument("--no-paper-mill-filter", action="store_true",
                    help="Disable the paper-mill-identity filter (module docstring's scope-guard #3) -- chase "
                         "every resolved author's OpenAlex work graph regardless of what fraction of their OWN "
                         "corpus papers are under a known low-scrutiny DOI prefix (IAEME/Pearl Blue), same as "
                         "before that guard existed")
    p.add_argument("--paper-mill-threshold", type=float, default=PAPER_MILL_FRACTION_THRESHOLD,
                    help=f"Fraction of an author's own corpus papers under a low-scrutiny DOI prefix at or "
                         f"above which they're treated as a likely front identity and skipped (default "
                         f"{PAPER_MILL_FRACTION_THRESHOLD})")
    p.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    p.add_argument("--max-workers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true", help="Harvest and report counts; query Unpaywall/download nothing")
    p.add_argument("--min-interval-openalex", type=float, default=0.3)
    p.add_argument("--min-interval-unpaywall", type=float, default=1.0)
    p.add_argument("--min-interval-download", type=float, default=0.5)
    p.add_argument("--max-retries", type=int, default=4)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_author_works.log"))
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

    library_conn = db.connect(args.library_db)
    resolved = load_resolved_authors(library_conn, min_total_papers=args.min_papers,
                                      filter_paper_mill=not args.no_paper_mill_filter,
                                      paper_mill_threshold=args.paper_mill_threshold)
    logger.info("%d resolved author(s) with >= %d corpus papers to check for new works%s",
                len(resolved), args.min_papers,
                "" if args.no_paper_mill_filter else
                f" (paper-mill identities at >= {args.paper_mill_threshold:.0%} excluded)")

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)

    session = requests.Session()
    # (OPENALEX_API_KEY itself is read and applied inside rp.search_openalex_works(), not here --
    # this used to be a locally-held variable before that function was extracted, per its own docstring.)
    session.headers["User-Agent"] = f"dupefinder-bulk-retrieve-author-works/1.0 (mailto:{args.email})"
    openalex_limiter = rp.RateLimiter(min_interval=args.min_interval_openalex)

    seen_dois = set(known_dois)
    candidates, openalex_hits = [], {}
    offtopic_skipped = 0
    for author_id, (name, openalex_id) in resolved.items():
        found = new = 0
        try:
            for work in search_author_works(session, openalex_limiter, openalex_id, args.max_per_author, args):
                found += 1
                paper = parse_author_work(work)
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
            logger.warning("stopped harvesting after author %r: %s -- keeping the %d candidate(s) already "
                            "found this run (set %s for a much higher rate limit)",
                            name, exc, len(candidates), rp.OPENALEX_API_KEY_ENV_VAR)
            break
        except rp.RetrievalError as exc:
            logger.warning("stopped harvesting after author %r: %s -- keeping the %d candidate(s) already found this run",
                            name, exc, len(candidates))
            break
        if new:
            logger.info("  %s (%s): %d found, %d new", name, openalex_id, found, new)

    logger.info("total: %d new candidate(s) across %d author(s) (%d off-topic-by-title skipped before ever "
                "downloading; not yet checked against state.sqlite3)",
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
            if downloaded % 25 == 0:
                logger.info("downloaded %d so far", downloaded)

    logger.info("done. downloaded %d new paper(s)", downloaded)


if __name__ == "__main__":
    main()
