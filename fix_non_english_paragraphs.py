#!/usr/bin/env python3
"""Retroactively re-checks already-embedded paragraphs against the current
english_score() (review_dupes.py) and corrects any that should have been
skipped as non-English at embed time but weren't -- most immediately, the
2026-08-30 english_score() bug (see that function's docstring): a paragraph
that's almost entirely a non-Latin script (Cyrillic, Greek, ...) but happens
to contain one incidental Latin token (a bibliographic "No.", a stray "et",
a DOI fragment) could score a perfect 1.0 instead of the near-0 the filter
is supposed to produce, because the old scoring only divided by the count of
Latin-alphabet tokens found. Found by hand-reading unclassified candidates
in the hindawi/nursing/anthropology field-corpus review.

Not something embed_paragraphs.py's normal idempotent re-run picks up on its
own: its (paper_id, para_index, text)-keyed re-embed logic only re-processes
a paragraph when its stored TEXT changed. These paragraphs' text hasn't --
only how a smarter scoring function judges it has -- so a plain re-run of
embed_paragraphs.py silently leaves every one of them exactly as wrongly-
embedded as before. This script is the general, re-runnable fix for that
class of problem (also useful again if english_score() ever improves
further): it re-scores every CURRENTLY-embedded paragraph, not just ones
touched by today's specific bug.

For every paragraph that now fails --min-english-score:
  1. Clears its embedding (embedding=NULL, embedding_dim=NULL,
     model=SKIPPED_MODEL_SENTINEL) -- the same sentinel row
     skip_non_english_paragraphs() would have written the first time, so it
     reads identically to "always correctly skipped" from here on and is
     never re-scored again by a normal embed_paragraphs.py run.
  2. Removes its LSH bucket/scanned rows (lsh_index.invalidate_paragraphs())
     -- a bucket keyed on an embedding that's just been zeroed out is stale.
  3. Deletes any potential_dupes row referencing it, plus any now-orphaned
     potential_dupe_authors rows. build_dupe_candidates.py's own
     delete_orphaned_candidates() only handles paragraphs deleted outright
     (paper re-extracted, same-document dedup, ...) -- it does not notice a
     paragraph whose embedding was retroactively cleared while the row
     itself still exists, which is exactly this case.

Any affected candidate that already carried a human review status
(status != 'unreviewed') is logged individually before deletion -- the
paragraph being non-English means that review was made without knowing the
"paragraph" wasn't real content in the corpus's target language at all, so
discarding it is still correct, but it's surfaced rather than silently lost.

Downstream: after running this against a corpus, re-run write_dupe_reports.py
(and copy_dupe_pdfs.py, if used) for that corpus -- a report file counting
candidates that just got deleted here would otherwise go stale. This script
does not do that itself (same "each script does one job" discipline as the
rest of the pipeline).

Safe to re-run: a paragraph already sentinel-marked has embedding IS NULL,
so find_now_failing()'s query never looks at it again.
"""

import argparse
import logging
from pathlib import Path

import db
import embed_paragraphs as ep
import lsh_index
import review_dupes as rd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("fix_non_english_paragraphs")


def find_now_failing(conn, min_english_score):
    """Paragraph ids that currently have an embedding but score below
    min_english_score under the CURRENT english_score() -- i.e. would be
    skipped if embedded fresh today."""
    rows = conn.execute("SELECT id, text FROM paragraphs WHERE embedding IS NOT NULL").fetchall()
    return [pid for pid, text in rows if rd.english_score(text) < min_english_score]


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 32766 (or 999 on older builds) -- the "reviewed"/
# DELETE queries below bind bad_ids TWICE (once per IN clause), so the real per-query cap on how many
# ids a single chunk can hold is half whatever that limit is. 500 matches lsh_index.py's own chunk
# size for the identical kind of paragraph_id IN (...) operation. Found for real 2026-08-30: the main
# corpus's 302,636-id cleanup crashed with "too many SQL variables" -- the field corpora (largest was
# anthropology at 9,197 ids) had stayed under the limit purely by chance, not because this was safe.
CHUNK_SIZE = 500


def run(conn, min_english_score=ep.DEFAULT_MIN_ENGLISH_SCORE, logger_=None):
    bad_ids = find_now_failing(conn, min_english_score)
    if not bad_ids:
        if logger_:
            logger_.info("0 already-embedded paragraph(s) now fail english_score() < %.2f -- nothing to do",
                          min_english_score)
        return {"paragraphs_fixed": 0, "candidates_removed": 0}

    reviewed = []
    for chunk in _chunks(bad_ids, CHUNK_SIZE):
        placeholders = ",".join("?" * len(chunk))
        reviewed.extend(conn.execute(
            f"""SELECT id, status, paragraph_id_1, paragraph_id_2 FROM potential_dupes
                WHERE (paragraph_id_1 IN ({placeholders}) OR paragraph_id_2 IN ({placeholders}))
                  AND status != 'unreviewed'""",
            list(chunk) + list(chunk),
        ).fetchall())
    for pd_id, status, p1, p2 in reviewed:
        if logger_:
            logger_.warning("potential_dupes id=%d had status=%r (paragraph %s) -- deleting anyway, since the "
                             "underlying paragraph isn't real target-language content", pd_id, status,
                             p1 if p1 in bad_ids else p2)

    for chunk in _chunks(bad_ids, CHUNK_SIZE * 2):  # single IN clause, not doubled -- full chunk size is fine
        placeholders = ",".join("?" * len(chunk))
        conn.execute(
            f"UPDATE paragraphs SET embedding = NULL, embedding_dim = NULL, model = ? WHERE id IN ({placeholders})",
            [ep.SKIPPED_MODEL_SENTINEL] + list(chunk),
        )
    lsh_index.invalidate_paragraphs(conn, bad_ids)
    removed_candidates = 0
    for chunk in _chunks(bad_ids, CHUNK_SIZE):
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"""DELETE FROM potential_dupes
                WHERE paragraph_id_1 IN ({placeholders}) OR paragraph_id_2 IN ({placeholders})""",
            list(chunk) + list(chunk),
        )
        removed_candidates += cur.rowcount
    conn.execute("DELETE FROM potential_dupe_authors WHERE potential_dupe_id NOT IN (SELECT id FROM potential_dupes)")
    conn.commit()

    if logger_:
        logger_.info("%d already-embedded paragraph(s) now fail english_score() < %.2f -- cleared their "
                      "embeddings, removed %d now-invalid potential_dupes row(s) (%d had a prior review status)",
                      len(bad_ids), min_english_score, removed_candidates, len(reviewed))
    return {"paragraphs_fixed": len(bad_ids), "candidates_removed": removed_candidates}


def parse_args():
    parser = argparse.ArgumentParser(description="Retroactively re-check already-embedded paragraphs "
                                                   "against the current english_score() filter.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--min-english-score", type=float, default=ep.DEFAULT_MIN_ENGLISH_SCORE)
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    run(conn, args.min_english_score, logger_=logger)
    conn.close()


if __name__ == "__main__":
    main()
