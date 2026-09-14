#!/usr/bin/env python3
"""Resolves a disambiguated OpenAlex author ID for authors already in
library.sqlite3 who have multiple papers in the corpus -- the identity-
resolution step bulk_retrieve_author_works.py needs before it can ask
OpenAlex "what else has this person published" (see that script's own
docstring for the retrieval half; this script is purely the ID-matching
half, run first, standalone, and idempotent).

Why not just search OpenAlex's author-search endpoint by name: our own
`authors` table is name-only (no external ID), and common names collide
across genuinely different real researchers -- guessing wrong would pull a
stranger's entire unrelated bibliography into the corpus (the same
off-topic-pollution failure mode filter_low_relevance_papers.py exists to
catch; see todo.md). Instead: every paper we already have a DOI for, we can
ask OpenAlex "who wrote this" via query_openalex_authorships_batch()
(retrieve_papers.py) -- and that authorship is OpenAlex's OWN
disambiguation, not a guess we're making from a bare name string, at no
extra API cost beyond what a batched work lookup already costs.

Algorithm, per author with >= --min-papers (default 2) papers in the corpus:
  1. Batch-fetch authorships for all their papers' DOIs (50 DOIs/request,
     same OPENALEX_BATCH_SIZE batching as retrieve_papers.py's other
     OpenAlex callers).
  2. For each paper, find the authorship entry whose display_name best
     matches our stored author name (NAME_MATCH_THRESHOLD, difflib ratio --
     same mechanism and threshold, 0.82, as retrieve_papers.py's own
     Crossref title-match acceptance bar, applied here to names instead of
     titles) -- comparing on a name NORMALIZED for "Last, First" vs.
     "First Last" ordering first (see normalize_author_name()), since our
     corpus mixes both depending on which upstream source supplied the
     author list, while OpenAlex always returns "First Last".
  3. Take the openalex_id that wins a plurality of that author's papers'
     matches (need real corroboration, not one lucky match, since a common
     name really can appear as different actual people even after the
     per-paper name-similarity check -- see MIN_VOTE_FRACTION). Ties, or an
     author with zero matched papers, are left unresolved rather than
     guessed -- exactly the same "false negatives are the cheap failure
     here, false positives are not" discipline filter_low_relevance_papers.py
     documents, applied to identity instead of topic relevance.

Persists into a new `author_openalex_ids` table (author_id PRIMARY KEY,
openalex_id, matched_papers, total_papers, resolved_at) -- one row per
resolved author, never guessed/overwritten silently: idempotent, skips
authors already resolved unless --recompute. Unresolved authors are simply
absent from the table (not recorded as a negative -- a later run, once more
of their papers are in the corpus, might resolve them where this run
couldn't).

Usage:
    python3 resolve_author_openalex_ids.py --email you@example.com [--min-papers 2] [--recompute]
"""

import argparse
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

import requests

import db
import retrieve_papers as rp

logger = logging.getLogger("resolve_author_openalex_ids")

NAME_MATCH_THRESHOLD = 0.82  # see module docstring: same bar as retrieve_papers.py's own title-match default
MIN_VOTE_FRACTION = 0.5  # the winning openalex_id must own a strict majority of an author's matched papers


def init_table(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS author_openalex_ids (
            author_id INTEGER PRIMARY KEY REFERENCES authors(id),
            openalex_id TEXT NOT NULL,
            matched_papers INTEGER NOT NULL,
            total_papers INTEGER NOT NULL,
            resolved_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


_COMMA_SPLIT_RE = re.compile(r"\s*,\s*")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_author_name(name):
    """Lowercase, strip accents/punctuation, and flip "Last, First[, ...]"
    to "First Last" -- our corpus mixes both orderings depending on which
    upstream source (Crossref/DataCite/CORE) supplied the author list,
    while OpenAlex's authorships[].author.display_name is always
    "First Last". Only flips on the FIRST comma (a "Last, First, Jr."
    suffix stays attached to First rather than becoming its own token)."""
    if not name:
        return ""
    parts = _COMMA_SPLIT_RE.split(name.strip(), maxsplit=1)
    if len(parts) == 2:
        name = f"{parts[1]} {parts[0]}"
    unescaped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    stripped = _PUNCT_RE.sub(" ", unescaped.lower())
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def name_similarity(name_a, name_b):
    return SequenceMatcher(None, normalize_author_name(name_a), normalize_author_name(name_b)).ratio()


def load_multi_paper_authors(conn, min_papers):
    """{author_id: (name, [doi, ...])} for every author with >= min_papers
    papers that have a non-NULL doi (an author whose only corpus papers
    lack a DOI can never be resolved this way -- nothing to batch-query)."""
    rows = conn.execute(
        """
        SELECT a.id, a.name, p.doi
        FROM paper_authors pa
        JOIN authors a ON a.id = pa.author_id
        JOIN papers p ON p.id = pa.paper_id
        WHERE p.doi IS NOT NULL
        """
    ).fetchall()
    by_author = defaultdict(list)
    names = {}
    for author_id, name, doi in rows:
        names[author_id] = name
        by_author[author_id].append(doi)
    return {
        author_id: (names[author_id], dois)
        for author_id, dois in by_author.items()
        if len(dois) >= min_papers
    }


def resolve_authors(session, candidates, rate_limiter, args):
    """candidates: {author_id: (name, [doi, ...])}. Returns
    {author_id: (openalex_id, matched_papers, total_papers)} for every
    author a plurality winner could be determined for."""
    all_dois = sorted({doi for _, dois in candidates.values() for doi in dois})
    authorships_by_doi = {}
    for batch in rp.chunked(all_dois, rp.OPENALEX_BATCH_SIZE):
        try:
            authorships_by_doi.update(
                rp.query_openalex_authorships_batch(session, batch, rate_limiter, args, logger))
        except rp.RateLimited as exc:
            logger.warning("stopped resolving after a persistent 429: %s -- keeping whatever matched so far "
                            "(set %s for a much higher rate limit)", exc, rp.OPENALEX_API_KEY_ENV_VAR)
            break

    resolved = {}
    for author_id, (name, dois) in candidates.items():
        votes = Counter()
        for doi in dois:
            for authorship in authorships_by_doi.get(rp.normalize_doi(doi).lower(), []):
                if name_similarity(name, authorship["display_name"]) >= NAME_MATCH_THRESHOLD:
                    votes[authorship["openalex_id"]] += 1
        if not votes:
            continue
        ranked = votes.most_common()
        winner, winner_count = ranked[0]
        tied_for_first = len(ranked) > 1 and ranked[1][1] == winner_count
        matched_total = sum(votes.values())
        if not tied_for_first and winner_count / matched_total >= MIN_VOTE_FRACTION:
            resolved[author_id] = (winner, winner_count, len(dois))
        else:
            logger.info("author %r: no clear plurality winner among matched papers (%s), leaving unresolved",
                         name, dict(votes))
    return resolved


def save_resolved(conn, resolved):
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """INSERT INTO author_openalex_ids (author_id, openalex_id, matched_papers, total_papers, resolved_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(author_id) DO UPDATE SET
           openalex_id=excluded.openalex_id, matched_papers=excluded.matched_papers,
           total_papers=excluded.total_papers, resolved_at=excluded.resolved_at""",
        [(author_id, openalex_id, matched, total, now)
         for author_id, (openalex_id, matched, total) in resolved.items()],
    )
    conn.commit()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--email", required=True, help="Contact email sent as mailto= (OpenAlex polite-pool terms)")
    p.add_argument("--db", default="library.sqlite3")
    p.add_argument("--min-papers", type=int, default=2, help="Only resolve authors with at least this many papers")
    p.add_argument("--recompute", action="store_true", help="Re-resolve authors already in author_openalex_ids")
    p.add_argument("--min-interval-openalex", type=float, default=0.3)
    p.add_argument("--max-retries", type=int, default=4)
    p.add_argument("--timeout", type=float, default=30.0)
    return p.parse_args()


def main():
    # Moved here from module level 2026-09-04: a bare module-level logging.basicConfig() call
    # silently broke any OTHER script's own logging setup that imports this module (Python's
    # basicConfig() is a no-op once the root logger already has a handler -- confirmed for real,
    # retrieve_flagged_case_context.py's FileHandler never attached because this ran first at
    # import time). Every other script in this project sets up logging inside main()/its own
    # setup_logger(), not at import time -- this now matches that convention.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    args = parse_args()
    conn = db.connect(args.db)
    init_table(conn)

    candidates = load_multi_paper_authors(conn, args.min_papers)
    if not args.recompute:
        already = {row[0] for row in conn.execute("SELECT author_id FROM author_openalex_ids")}
        candidates = {k: v for k, v in candidates.items() if k not in already}
        logger.info("skipping %d already-resolved author(s)", len(already))
    logger.info("%d author(s) with >= %d papers to resolve", len(candidates), args.min_papers)

    session = requests.Session()
    session.headers["User-Agent"] = f"dupefinder-resolve-author-openalex-ids/1.0 (mailto:{args.email})"
    rate_limiter = rp.RateLimiter(min_interval=args.min_interval_openalex)

    resolved = resolve_authors(session, candidates, rate_limiter, args)
    save_resolved(conn, resolved)
    logger.info("resolved %d/%d author(s)", len(resolved), len(candidates))
    conn.close()


if __name__ == "__main__":
    main()
