#!/usr/bin/env python3
"""One-off targeted retrieval for a specific flagged-case author/paper pair: pulls (1) the
author's own other publications via OpenAlex author-works search, and (2) every resolvable
reference in one target paper's own citation list -- so both "does this author do this
elsewhere" and "what did this paper actually draw on" are covered, not just a single
already-known overlap.

Citation splitting: a hand-verified case's citations table can have a handful of giant raw-text
blobs, not one row per citation -- a known extraction gap for hanging-indent/author-year
reference lists (see CLAUDE.md's extract_papers.py section). Split here with an APA-style
"Lastname, Initials... (Year)." lookahead regex before resolving each entry individually;
bulk_retrieve_citations.py's own is_wellformed() would otherwise just skip these blobs outright
(they exceed its MAX_LEN).

Reuses, rather than reimplements: bulk_retrieve_citations.resolve_citation() (Crossref
bibliographic search + significant-word-overlap scoring, tolerant of a full messy citation
string) for the citation half; retrieve_flagged_case_authors.py's OpenAlex-author-identity-
then-works-search shape for the author-works half (see that script for the original, more
general version -- this one is narrower: a single targeted author/paper pair via CLI args, no
--no-topic-filter option, since the ask here is "everything they wrote and cited" for one
specific case, not a topic-filtered general sweep).
"""
import argparse
import logging
import re
from pathlib import Path

import requests

import bulk_retrieve_author_works as braw
import bulk_retrieve_citations as brcite
import bulk_retrieve_crossref as brc
import db
import resolve_author_openalex_ids as roai
import retrieve_papers as rp

logger = logging.getLogger("retrieve_flagged_case_context")

# Same shape as bulk_retrieve_citations.py's own splitter would need for well-formed entries, but
# built specifically for APA hanging-indent blobs: a new citation starts at "Lastname, Initial(s)."
# optionally followed by more "Lastname, Initial(s)." author entries (joined by ", " or " & "),
# then "(Year)." -- e.g. "Abi-Hashem, N. (2018)." or "Devault, A., Forget, G., & Dubeau, D. (2015)."
# (?<!-) blocks a false match partway through a hyphenated surname like "Abi-Hashem" (without it,
# "Hashem, N. (2018)." alone satisfies the pattern and silently eats the "Abi-" prefix).
CITATION_SPLIT_RE = re.compile(
    r"(?=(?<!-)[A-Z][a-zA-Z'-]+,\s(?:[A-Z]\.\s?){1,2}"
    r"(?:,\s[A-Z][a-zA-Z'-]+,\s(?:[A-Z]\.\s?){1,2})*"
    r"(?:,?\s?&\s[A-Z][a-zA-Z'-]+,\s(?:[A-Z]\.\s?){1,2})?\s?\(\d{4}[a-z]?\)\.)"
)


def split_citations(raw_blobs):
    full_text = " ".join(raw_blobs)
    parts = CITATION_SPLIT_RE.split(full_text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 20]


def setup_logger(log_file):
    # Force-configure regardless of prior state -- resolve_author_openalex_ids.py calls
    # logging.basicConfig() at import time (module level, not inside a function), which makes a
    # later plain logging.basicConfig() call here a silent no-op (root logger already has a
    # handler) and this script's FileHandler never actually attaches. Confirmed for real: the
    # log file stayed empty through an entire dry run even though console output worked.
    # Clearing existing root handlers first sidesteps needing to fix that module instead.
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for handler in (logging.FileHandler(log_file), logging.StreamHandler()):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def resolve_author_openalex_id(session, doi, author_name, args):
    """Same identity-resolution shape as retrieve_flagged_case_authors.py: look up the paper's
    OpenAlex authorships for this DOI, name-match against author_name, require
    NAME_MATCH_THRESHOLD confidence -- no multi-paper corroboration needed for a single
    hand-picked target."""
    authorships = rp.query_openalex_authorships_batch(session, [doi], rp.RateLimiter(0.3), args, logger)
    candidates = authorships.get(rp.normalize_doi(doi).lower(), [])
    best, best_score = None, 0.0
    for cand in candidates:
        score = roai.name_similarity(author_name, cand["display_name"])
        if score > best_score:
            best, best_score = cand, score
    if best and best_score >= roai.NAME_MATCH_THRESHOLD:
        return best["openalex_id"], best["display_name"], best_score
    return None, None, best_score


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--paper-id", type=int, required=True,
                         help="library.sqlite3 papers.id of the target paper (whose own citations get resolved)")
    parser.add_argument("--author-name", required=True,
                         help="target author's name as it should be matched against OpenAlex authorships")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--max-per-author", type=int, default=200)
    parser.add_argument("--max-workers", type=int, default=6)
    parser.add_argument("--min-overlap", type=float, default=brcite.DEFAULT_MIN_OVERLAP)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-interval-crossref", type=float, default=1.0)
    parser.add_argument("--min-interval-openalex", type=float, default=0.3)
    parser.add_argument("--min-interval-unpaywall", type=float, default=1.0)
    parser.add_argument("--min-interval-download", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("retrieve_flagged_case_context.log"))
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    lib_conn = db.connect(args.library_db)
    doi = lib_conn.execute("SELECT doi FROM papers WHERE id=?", (args.paper_id,)).fetchone()[0]
    raw_blobs = [r[0] for r in lib_conn.execute(
        "SELECT raw_text FROM citations WHERE paper_id=? ORDER BY citation_order", (args.paper_id,)
    ).fetchall()]
    lib_conn.close()

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    crossref_limiter = rp.RateLimiter(args.min_interval_crossref)
    openalex_limiter = rp.RateLimiter(args.min_interval_openalex)
    unpaywall_limiter = rp.RateLimiter(args.min_interval_unpaywall)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)

    # --- Part 1: the author's own other works ---
    openalex_id, matched_name, score = resolve_author_openalex_id(session, doi, args.author_name, args)
    author_candidates = []
    if openalex_id:
        logger.info("resolved %r -> OpenAlex %s (matched %r, score %.2f)",
                     args.author_name, openalex_id, matched_name, score)
        for work in braw.search_author_works(session, openalex_limiter, openalex_id, args.max_per_author, args):
            paper = braw.parse_author_work(work)
            if paper:
                author_candidates.append(paper)
        logger.info("found %d work(s) by %r via OpenAlex", len(author_candidates), args.author_name)
    else:
        logger.warning("could not confidently resolve %r to an OpenAlex author id (best score %.2f) -- "
                        "skipping the author-works half", args.author_name, score)

    # --- Part 2: the target paper's own citations ---
    citations = split_citations(raw_blobs)
    logger.info("split target paper's %d raw citation blob(s) into %d individual entries", len(raw_blobs), len(citations))

    citation_candidates = []
    resolved = unresolved = 0
    for i, raw_text in enumerate(citations, 1):
        try:
            paper, score = brcite.resolve_citation(session, raw_text, crossref_limiter, args)
        except rp.RetrievalError as exc:
            logger.warning("crossref lookup error for citation %r: %s", raw_text[:80], exc)
            unresolved += 1
            continue
        if not paper:
            unresolved += 1
            continue
        resolved += 1
        citation_candidates.append(paper)
        if i % 25 == 0:
            logger.info("resolved %d/%d citation(s) so far", i, len(citations))
    logger.info("citation resolution done: %d/%d resolved (score>=%.2f)", resolved, len(citations), args.min_overlap)

    # --- Merge, dedup against known DOIs, download ---
    seen_dois, candidates = set(), []
    for paper in author_candidates + citation_candidates:
        doi_norm = rp.normalize_doi(paper.get("doi"))
        if not doi_norm or doi_norm.lower() in seen_dois:
            continue
        seen_dois.add(doi_norm.lower())
        if doi_norm.lower() in known_dois:
            continue
        candidates.append(paper)

    logger.info("%d new candidate(s) to fetch (author-works + citations combined, deduped, "
                "already-known excluded)", len(candidates))
    if args.dry_run:
        logger.info("--dry-run: stopping before Unpaywall/download activity")
        store.conn.close()
        return
    store.conn.close()

    thread_store = rp.ThreadLocalPaperStore(args.db)
    tasks = [(session, paper, thread_store, unpaywall_limiter, download_limiter, args) for paper in candidates]
    downloaded = 0
    for _, result in rp.concurrent_fetch(tasks, lambda t: brc.fetch_candidate(*t), max_workers=args.max_workers,
                                          logger=logger):
        if result == "downloaded":
            downloaded += 1
            if downloaded % 10 == 0:
                logger.info("downloaded %d so far", downloaded)

    logger.info("done. downloaded %d new paper(s) out of %d candidate(s)", downloaded, len(candidates))


if __name__ == "__main__":
    main()
