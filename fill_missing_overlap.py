#!/usr/bin/env python3
"""Backfill lcs_ratio/ngram_jaccard for existing potential_dupes rows that
predate text_overlap.py's checks (or were only ever discovered by an
incremental LSH scan that computed similarity but never ran the O(pair)
textual-overlap check on them -- see build_dupe_candidates.py's docstring).

This is the narrow/partial alternative to `build_dupe_candidates.py
--full-rescan`: --full-rescan re-derives the ENTIRE candidate set from the
whole LSH index (todo.md documents multi-hour stalls doing this at this
corpus's size), which is massive overkill just to fill in two already-known
pairs' missing columns. Every NULL row here already has both paragraph ids
persisted -- there's no candidate *discovery* to redo, just two cheap
per-pair metrics (text_overlap.py) to compute and UPDATE in place. Doesn't
touch similarity/same_author/chronology/status/ai_check/reviewed_at -- those
are unaffected by this gap and are left exactly as they are.

Idempotent: only ever targets rows where lcs_ratio IS NULL OR ngram_jaccard
IS NULL, so re-running after a fresh build_dupe_candidates.py run (which may
have left new NULLs of its own, same incremental-scan mechanism) just picks
up whatever's newly missing.
"""

import argparse
import logging
import time
from pathlib import Path

import db
import text_overlap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("fill_missing_overlap")


def find_missing(conn):
    return conn.execute(
        """
        SELECT pd.id, pd.paragraph_id_1, pd.paragraph_id_2, p1.text, p2.text
        FROM potential_dupes pd
        JOIN paragraphs p1 ON p1.id = pd.paragraph_id_1
        JOIN paragraphs p2 ON p2.id = pd.paragraph_id_2
        WHERE pd.lcs_ratio IS NULL OR pd.ngram_jaccard IS NULL
        """
    ).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--ngram-size", type=int, default=5,
                         help="word n-gram size for ngram_jaccard (default 5, matches build_dupe_candidates.py)")
    args = parser.parse_args()

    conn = db.connect(args.library_db)
    rows = find_missing(conn)
    logger.info("%d potential_dupes row(s) missing lcs_ratio/ngram_jaccard", len(rows))
    if not rows:
        conn.close()
        return

    start = time.time()
    COMMIT_EVERY = 500
    for i, (pd_id, para_id_1, para_id_2, text_1, text_2) in enumerate(rows):
        lcs_ratio = text_overlap.longest_common_word_run(text_1, text_2)
        ngram_jaccard = text_overlap.ngram_jaccard(text_1, text_2, n=args.ngram_size)
        conn.execute(
            "UPDATE potential_dupes SET lcs_ratio = ?, ngram_jaccard = ? WHERE id = ?",
            (lcs_ratio, ngram_jaccard, pd_id),
        )
        if (i + 1) % COMMIT_EVERY == 0:
            conn.commit()
            logger.info("filled %d/%d", i + 1, len(rows))
    conn.commit()

    remaining = conn.execute(
        "SELECT COUNT(*) FROM potential_dupes WHERE lcs_ratio IS NULL OR ngram_jaccard IS NULL"
    ).fetchone()[0]
    conn.close()
    logger.info("filled %d row(s) in %.1fs -- %d row(s) still missing (paragraph deleted since?)",
                len(rows), time.time() - start, remaining)


if __name__ == "__main__":
    main()
