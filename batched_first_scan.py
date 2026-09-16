#!/usr/bin/env python3
"""Run a corpus's first-ever (or otherwise mostly-new) full LSH candidate scan in small,
memory-bounded batches of "new" paragraphs, instead of one call to
build_dupe_candidates.process_lsh_candidates_streaming() that tries to process the whole
corpus as "new" simultaneously.

Use this instead of a direct `build_dupe_candidates.py` invocation whenever most of the
corpus is simultaneously new -- a first-ever run, or a very large one-time embedding batch,
rather than the small-trickle-of-new-paragraphs scenario the incremental algorithm was
tuned for. For an ordinary incremental run (a new retrieval batch against an
already-scanned corpus), use build_dupe_candidates.py directly; this script is pure
overhead there.

Why this is needed (see todo.md's 2026-09-03 post-mortem for the full investigation):
lsh_index._discover_candidate_groups()'s incremental-mode query finds every LSH bucket
"touched" by at least one new paragraph, then fetches ALL members of those buckets in one
unchunked .fetchall(). That's correctly cheap in the design's intended scenario (a small
trickle of newly-embedded paragraphs against an already-large, mostly-"old" corpus) but
when essentially every paragraph is new and unscanned, essentially every bucket is
"touched," so that one fetchall() pulls close to the entire multi-hundred-million-row
lsh_buckets table into memory. That's a structural gap in the incremental algorithm at
this scale, not something a smaller --max-bucket-size or the lsh_indexed fixes (both real,
already applied) can fix on their own. The real fix -- SQL-level bucket-size filtering in
_discover_candidate_groups(), so Pass 1 never fetches oversized/boilerplate bucket
membership it's just going to discard -- is still not done; until it is, this script is the
procedure.

The workaround uses only already-tested, unmodified primitives (lsh_index.mark_scanned(),
build_dupe_candidates.process_lsh_candidates_streaming()) to control how many paragraphs
are eligible as "new" per call: mark EVERY pending paragraph "scanned" up front (hiding all
of them from _discover_candidate_groups()'s query), then for each batch, un-mark just that
batch (making only it "new" and everything else "old") before calling the normal,
unmodified streaming path -- which itself re-marks the batch "scanned" (now legitimately,
since it was actually processed) when it finishes. Net DB work is O(corpus size) for the
marking bookkeeping, not O(batches x corpus size) -- each paragraph gets marked, unmarked,
and re-marked exactly once across the whole run, regardless of how many batches there are.

**Interrupting this script requires a manual recovery step, every time.** The "mark
everything up front" design means a kill mid-run leaves a large majority of the corpus
marked lsh_scanned without ever having been genuinely processed -- confirmed live (a kill
mid-batch-1 left 9,372,714 paragraphs stuck marked-but-unprocessed; the next attempt
reported only 200,000 "pending," which would have silently skipped the other ~9.37M).
There is no safe partial-state resume, only "let it finish" or "kill, clear lsh_scanned,
restart from batch 1":

    sqlite3 <library.sqlite3> 'DELETE FROM lsh_scanned'

That recovery is lossless -- potential_dupes inserts are INSERT OR IGNORE on a unique pair
constraint, so candidates from already-completed batches are never duplicated or lost, just
safely re-derived. (A later `build_dupe_candidates.py --full-rescan` also recovers, by
ignoring lsh_scanned and re-deriving groups straight from lsh_buckets.) Don't kill this
without clearing lsh_scanned before the next attempt.

Proven on two ~14M-paragraph corpora (anthropology, computer-ethics).
"""
import argparse
import logging
from pathlib import Path

import build_dupe_candidates as bdc
import db
import lsh_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("batched_first_scan")

DEFAULT_BATCH_SIZE = 200_000
# Deliberately far below lsh_index.DEFAULT_MAX_BUCKET_SIZE (30): at first-scan scale the
# oversized-bucket skip is the main thing keeping Pass 2 tractable, and boilerplate buckets
# on these corpora run to tens of thousands of members. Raise it if a run's "skipped N
# oversized group(s)" count looks like it's discarding real signal.
DEFAULT_MAX_BUCKET_SIZE = 4
DEFAULT_UNMARK_CHUNK = 5000


def unmark_scanned(conn, paragraph_ids, chunk_size=DEFAULT_UNMARK_CHUNK):
    """Inverse of lsh_index.mark_scanned() -- removes these ids from lsh_scanned so
    they're eligible again. Chunked the same way mark_scanned() itself is."""
    paragraph_ids = list(paragraph_ids)
    for start in range(0, len(paragraph_ids), chunk_size):
        chunk = paragraph_ids[start:start + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM lsh_scanned WHERE paragraph_id IN ({placeholders})", chunk)
    conn.commit()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                         help=f"how many paragraphs are eligible as 'new' per streaming call "
                              f"(default {DEFAULT_BATCH_SIZE}) -- the knob that bounds peak memory; "
                              f"lower it if a run still grows too large, raise it for fewer, "
                              f"bigger passes on a machine with room")
    parser.add_argument("--max-bucket-size", type=int, default=DEFAULT_MAX_BUCKET_SIZE,
                         help=f"per-table bucket size above which a bucket is skipped (default "
                              f"{DEFAULT_MAX_BUCKET_SIZE}, far below build_dupe_candidates.py's own "
                              f"{lsh_index.DEFAULT_MAX_BUCKET_SIZE} -- see this module's docstring)")
    parser.add_argument("--threshold", type=float, default=bdc.DEFAULT_THRESHOLD,
                         help=f"cosine similarity above which a pair is persisted (default "
                              f"{bdc.DEFAULT_THRESHOLD}, same as build_dupe_candidates.py)")
    parser.add_argument("--min-length", type=int, default=0,
                         help="minimum paragraph length to consider (default 0)")
    parser.add_argument("--ngram-size", type=int, default=5,
                         help="word n-gram size for text_overlap.py's ngram_jaccard (default 5)")
    parser.add_argument("--max-candidate-pairs", type=int, default=lsh_index.MAX_CANDIDATE_PAIRS,
                         help="safety ceiling on projected pairs per batch")
    parser.add_argument("--unmark-chunk", type=int, default=DEFAULT_UNMARK_CHUNK,
                         help=f"DELETE ... IN (...) chunk size for unmark_scanned() (default "
                              f"{DEFAULT_UNMARK_CHUNK})")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)

    removed = bdc.delete_orphaned_candidates(conn)
    if removed:
        logger.info("removed %d orphaned candidate(s)", removed)

    logger.info("loading enrichment data...")
    titles_by_paper = dict(conn.execute("SELECT id, title FROM papers"))
    authors_by_paper = bdc.load_paper_authors(conn)
    citations_by_paper = bdc.load_citations(conn)
    years = bdc.load_paper_years(conn)
    duplicate_paper_pairs = bdc.load_duplicate_paper_pairs(conn)

    row = conn.execute(
        "SELECT embedding_dim, model FROM paragraphs WHERE embedding IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        logger.error("no embedded paragraphs in %s -- run embed_paragraphs.py first", args.library_db)
        conn.close()
        raise SystemExit(1)
    dim, model_name = row
    lsh_index.init_lsh_tables(conn)
    _, n_indexed = lsh_index.sync_index(conn, model=model_name, embedding_dim=dim, logger_=logger)
    logger.info("sync_index: %d newly indexed", n_indexed)

    logger.info("computing full pending-scan list (cheap: lsh_indexed-based)...")
    all_new = [
        r[0] for r in conn.execute(
            """
            SELECT i.paragraph_id FROM lsh_indexed i
            LEFT JOIN lsh_scanned s ON s.paragraph_id = i.paragraph_id
            WHERE s.paragraph_id IS NULL
            """
        ).fetchall()
    ]
    n_total = len(all_new)
    if not n_total:
        logger.info("nothing pending -- every indexed paragraph is already scanned")
        conn.close()
        return
    n_batches = (n_total + args.batch_size - 1) // args.batch_size
    logger.info("%d paragraph(s) pending scan, in %d batch(es) of %d", n_total, n_batches, args.batch_size)

    logger.info("marking all %d pending paragraph(s) scanned up front (hides them all until unmarked "
                "per-batch) -- an interrupted run from here on needs `DELETE FROM lsh_scanned` before "
                "the next attempt, see this script's docstring", n_total)
    lsh_index.mark_scanned(conn, all_new)

    total_persisted = 0
    for batch_num, start in enumerate(range(0, n_total, args.batch_size), start=1):
        this_batch = all_new[start:start + args.batch_size]
        logger.info("batch %d/%d: unmarking %d paragraph(s) to make them eligible...",
                     batch_num, n_batches, len(this_batch))
        unmark_scanned(conn, this_batch, chunk_size=args.unmark_chunk)

        n = bdc.process_lsh_candidates_streaming(
            conn, args.threshold, args.min_length, args.max_bucket_size, False, args.max_candidate_pairs,
            args.ngram_size, titles_by_paper, authors_by_paper, citations_by_paper, years,
            duplicate_paper_pairs, logger_=logger,
        )
        total_persisted += n
        logger.info("batch %d/%d done: %d candidate(s) persisted, %d total so far",
                     batch_num, n_batches, n, total_persisted)

    logger.info("ALL BATCHES DONE: %d candidate(s) persisted total", total_persisted)
    conn.close()


if __name__ == "__main__":
    main()
