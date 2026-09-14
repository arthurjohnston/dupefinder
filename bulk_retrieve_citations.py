#!/usr/bin/env python3
"""Bulk-retrieve papers by chasing citations already extracted from the corpus,
instead of searching Crossref by keyword.

Why this exists: keyword search (bulk_retrieve_crossref.py) turned out to have a real
precision problem at scale -- broad single-word matching (e.g. "privacy ethics",
"surveillance ethics") pulls in a lot of off-topic medical/nursing/religious-ethics
content via Crossref's fuzzy query.bibliographic ranking, driving download hit rates
down to ~0.7% on some batches. A citation pulled from a paper already in this corpus is
a much stronger relevance signal than a keyword match -- it's not a guess about topical
overlap, it's a paper someone already in the corpus actually read and referenced.

It's also a direct shot at the thing this project is actually for: the one confirmed
cross-author case found so far (Saxby thesis vs. Ferdaus et al.'s MDPI "Taro Roots",
see tests/cases/saxby-taro-2023.json) has later_cites_earlier=1 -- the plagiarizing
paper's own bibliography named the source it copied from, uncredited as quotation.
build_dupe_candidates.py's later_cites_earlier flag only fires when the cited paper
already happens to be in the corpus; right now that's rare, because nothing has ever
deliberately gone and fetched what our papers cite. This does that.

Pipeline:
  1. Load raw_text citation strings out of library.sqlite3's citations table (extracted
     by extract_papers.py -- see its "Known heuristic gaps" for why this text is noisy:
     hanging-indent/author-year reference lists especially don't split per-entry cleanly,
     and some rows are misclassified body/table text, not citations at all).
  2. Filter out obviously-malformed strings (WELLFORMED_RE: needs a plausible 4-digit
     year and a reasonable length) before spending an API call on them.
  3. Dedupe by normalized text -- many papers in the same subfield cite the same sources.
  4. Prioritize citations *from* papers that already show same-author self-reuse in
     potential_dupes (same_author=1) -- these are documents we already know have a
     text-reuse habit, so checking whether they also reuse (undisclosed) from something
     they cite is the highest-value slice, not a blind pull across every citation.
  5. Resolve each prioritized citation to a DOI via Crossref's query.bibliographic
     (the same endpoint retrieve_papers.py's resolve_doi() uses, but scored differently:
     resolve_doi() SequenceMatcher-compares a known-clean title against Crossref's
     returned title, which doesn't work well against a raw citation string cluttered
     with authors/venue/year/page numbers -- so this uses word-overlap against the
     returned title instead, same spirit as build_dupe_candidates.py's own cites()
     heuristic for later_cites_earlier).
  6. For each resolved DOI not already known in state.sqlite3, reuse retrieve_papers.py's
     query_unpaywall()/download_pdf() exactly as bulk_retrieve_crossref.py does -- same
     state.sqlite3 schema, so extract_papers.py needs zero changes to pick these up.

Resumable/dedup-aware the same way: a DOI already status='downloaded' under any key is
skipped, never re-fetched.
"""

import argparse
import logging
import re
from pathlib import Path

import requests

import db
import build_dupe_candidates as bdc
import bulk_retrieve_crossref as brc
import retrieve_papers as rp

CROSSREF_WORKS_API = "https://api.crossref.org/works"

# Needs a plausible publication year and a sane length -- cheap enough to filter out the
# worst extraction noise (garbled table data, stray body-text fragments) before an API call.
WELLFORMED_RE = re.compile(r"(19|20)\d{2}")
MIN_LEN, MAX_LEN = 25, 400

DEFAULT_MAX_CITATIONS = 1000
DEFAULT_MIN_OVERLAP = 0.6  # looser than build_dupe_candidates.CITATION_WORD_OVERLAP (0.7) --
                            # here we're scoring a returned *title* against the citation's full
                            # raw text (author/venue/year clutter included), not the other way
                            # around, so a slightly looser bar avoids rejecting good matches.

logger = logging.getLogger("bulk_retrieve_citations")


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


def normalize_citation(text):
    text = re.sub(r"^\s*[\[\(]?\d{1,4}[\]\).]?\s*", "", text)  # strip leading "[12]"/"24."
    return " ".join(text.lower().split())


def is_wellformed(text):
    return MIN_LEN <= len(text) <= MAX_LEN and WELLFORMED_RE.search(text) is not None


def load_prioritized_citations(conn, max_citations):
    """Citations from papers already showing same-author self-reuse first (see module
    docstring), then everything else, deduped by normalized text, well-formed only."""
    self_reuse_papers = {
        row[0] for row in conn.execute(
            "SELECT paper_id_1 FROM potential_dupes WHERE same_author = 1 "
            "UNION SELECT paper_id_2 FROM potential_dupes WHERE same_author = 1"
        )
    }
    logger.info("%d paper(s) show same-author self-reuse -- their citations are prioritized", len(self_reuse_papers))

    rows = conn.execute("SELECT paper_id, raw_text FROM citations").fetchall()
    priority, rest = [], []
    for paper_id, raw_text in rows:
        if not is_wellformed(raw_text):
            continue
        (priority if paper_id in self_reuse_papers else rest).append(raw_text)

    seen, ordered = set(), []
    for raw_text in priority + rest:
        norm = normalize_citation(raw_text)
        if norm in seen:
            continue
        seen.add(norm)
        ordered.append(raw_text)
        if len(ordered) >= max_citations:
            break

    n_from_priority = sum(1 for t in ordered if normalize_citation(t) in
                           {normalize_citation(r) for r in priority})
    logger.info("selected %d well-formed, deduped citation(s) to resolve (%d from self-reuse papers)",
                len(ordered), n_from_priority)
    return ordered


def resolve_citation(session, raw_text, rate_limiter, args):
    """Best-effort DOI resolution for one raw citation string. Returns a
    bulk_retrieve_crossref.parse_crossref_item()-shaped dict, or None."""
    params = {"query.bibliographic": raw_text, "rows": 3, "mailto": args.email}
    resp = rp.http_get(session, CROSSREF_WORKS_API, params, rate_limiter=rate_limiter,
                        max_retries=args.max_retries, timeout=args.timeout, logger=logger)
    if resp.status_code != 200:
        raise rp.RetrievalError(f"crossref lookup failed: HTTP {resp.status_code}")
    items = resp.json().get("message", {}).get("items", [])

    citation_words = bdc.significant_words(raw_text)
    best, best_score = None, 0.0
    for item in items:
        paper = brc.parse_crossref_item(item)
        if not paper:
            continue
        title_words = bdc.significant_words(paper["title"])
        if len(title_words) < 2:
            continue
        score = len(title_words & citation_words) / len(title_words)
        if score > best_score:
            best, best_score = paper, score

    if best and best_score >= args.min_overlap:
        return best, best_score
    return None, best_score


def parse_args():
    parser = argparse.ArgumentParser(description="Bulk-retrieve papers by resolving citations already in the corpus.")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent + mailto=")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"), help="source of citations")
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"), help="retrieval manifest (same as every other retrieve script)")
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--max-citations", type=int, default=DEFAULT_MAX_CITATIONS,
                         help=f"how many prioritized, well-formed, deduped citation strings to attempt "
                              f"resolving (default {DEFAULT_MAX_CITATIONS} -- this is the pilot-sizing knob)")
    parser.add_argument("--max-papers", type=int, default=None, help="stop after downloading this many new papers total")
    parser.add_argument("--max-workers", type=int, default=8, help="concurrent fetch threads for the download phase (default 8)")
    parser.add_argument("--min-overlap", type=float, default=DEFAULT_MIN_OVERLAP,
                         help=f"minimum significant-word overlap between a citation and a Crossref candidate's "
                              f"title to accept the match (default {DEFAULT_MIN_OVERLAP})")
    parser.add_argument("--dry-run", action="store_true", help="resolve and report counts; query Unpaywall/download nothing")
    parser.add_argument("--min-interval-crossref", type=float, default=1.0)
    parser.add_argument("--min-interval-unpaywall", type=float, default=1.0)
    parser.add_argument("--min-interval-download", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_citations.log"))
    return parser.parse_args()


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    lib_conn = db.connect(args.library_db)
    citations = load_prioritized_citations(lib_conn, args.max_citations)
    lib_conn.close()
    if not citations:
        logger.info("no well-formed citations found -- nothing to do")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    crossref_limiter = rp.RateLimiter(args.min_interval_crossref)
    unpaywall_limiter = rp.RateLimiter(args.min_interval_unpaywall)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)

    seen_dois, candidates = set(), []
    resolved = unresolved = 0
    for i, raw_text in enumerate(citations, 1):
        try:
            paper, score = resolve_citation(session, raw_text, crossref_limiter, args)
        except rp.RetrievalError as exc:
            logger.warning("crossref lookup error for citation %r: %s", raw_text[:80], exc)
            unresolved += 1
            continue

        if not paper:
            unresolved += 1
            continue
        doi_norm = rp.normalize_doi(paper["doi"])
        if not doi_norm or doi_norm.lower() in seen_dois:
            continue
        seen_dois.add(doi_norm.lower())
        resolved += 1
        if doi_norm.lower() in known_dois:
            continue
        candidates.append(paper)
        if i % 100 == 0:
            logger.info("resolved %d/%d citation(s) so far (%d new candidate(s))", i, len(citations), len(candidates))

    logger.info("resolution done: %d/%d citation(s) resolved to a DOI (score>=%.2f), %d new candidate(s) to fetch",
                resolved, len(citations), args.min_overlap, len(candidates))
    if args.dry_run:
        logger.info("--dry-run: stopping before any Unpaywall/download activity")
        store.conn.close()
        return
    store.conn.close()  # resolution phase is single-threaded (and Crossref-host-limited either way); download phase uses its own per-thread connections

    # Same reasoning as bulk_retrieve_crossref.py's download loop: candidates go to many
    # different hosts, so real thread concurrency helps here even though resolution above
    # (all against api.crossref.org, one host) wouldn't have benefited from it.
    thread_store = rp.ThreadLocalPaperStore(args.db)
    tasks = [(session, paper, thread_store, unpaywall_limiter, download_limiter, args) for paper in candidates]
    downloaded = 0
    for _, result in rp.concurrent_fetch(tasks, lambda t: brc.fetch_candidate(*t), max_workers=args.max_workers,
                                          max_successes=args.max_papers, logger=logger):
        if result == "downloaded":
            downloaded += 1
            if downloaded % 25 == 0:
                logger.info("downloaded %d so far", downloaded)

    logger.info("done. downloaded %d new paper(s) out of %d resolved candidate(s)", downloaded, len(candidates))


if __name__ == "__main__":
    main()
