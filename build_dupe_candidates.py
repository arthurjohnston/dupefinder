#!/usr/bin/env python3
"""Persist candidate duplicate pairs into a review-ready `potential_dupes` table.

find_duplicates.py is the ad-hoc/exploratory report: it prints ranked pairs
to the console for a quick look and forgets them the moment the process
exits. This script is the next pipeline stage todo.md's "Looking for dupes"
section asks for: it reuses the same similarity search, but PERSISTS every
pair above --threshold into library.sqlite3 enriched with filterable flags,
so a future review UI (the CLI/webpage in todo.md's "UX" section) has a
stateful table to work from instead of re-running the search every time.

Flags computed per pair:
  - same_author: 1 if the two papers share an author (name match against
    the `authors` table). Reused text between a person's own two papers is
    still a literal duplicate, but a different ethical question than a
    stranger's paper copying them -- flagged so it's filterable, not hidden.
  - later_cites_earlier: 1 if the chronologically later paper's citations
    table appears (heuristic word-overlap against raw citation strings) to
    reference the earlier paper. NULL when chronology is unknown (see
    below) -- properly-cited paraphrasing is a milder situation than an
    uncredited copy, and this is only meaningful once we know which paper
    came second.
  - earlier_paper_id / later_paper_id: resolved by year when the two
    papers' years differ; both left NULL when they're equal or either is
    missing -- chronology is left genuinely unknown rather than guessed.
  - lcs_ratio / ngram_jaccard: real textual-overlap checks (text_overlap.py)
    -- more accurate than the embedding cosine similarity alone, which is a
    semantic measure and can be fooled by two paragraphs that are merely
    on the same narrow topic. Too expensive to run across a whole corpus's
    O(n^2) pairs (that's what cosine similarity via LSH exists to avoid),
    but cheap on the already-small candidate set that survives it -- see
    text_overlap.py's docstring. lcs_ratio is the longest run of verbatim
    shared words, as a fraction of the shorter paragraph; ngram_jaccard is
    word --ngram-size shingle overlap (default 5), catching copying that's
    distributed rather than one long run.

Candidate pairs come from the persisted LSH index (lsh_index.py) rather than
a brute-force N x N compare: lsh_index.scan_candidate_pairs() returns every
pair of paragraphs that hashed into the same bucket in at least one of its
tables, and exact cosine similarity is computed only for that (much smaller)
candidate set. See todo.md's "Candidate index design (LSH)" for the full
design, the measured recall/bucket-occupancy numbers behind the defaults,
and the known gap (an exact-duplicate cluster bigger than --max-bucket-size
can be missed). Pass --brute-force to fall back to the old exhaustive
find_duplicates.find_pairs matrix multiply -- e.g. to spot-check recall, or
sweep a --threshold low enough that the index wasn't tuned for it.

Idempotent: re-running recomputes the candidate set from current
embeddings, but a pair's `status`/`reviewed_at` is only ever set once, on
first insert, and is never overwritten by a later run -- so re-running
after adding new papers never resets a human's prior
confirmed/false_positive/unsure call. Candidate rows whose paragraph no
longer exists (e.g. extract_papers.py's same-document dedup removed it) are
deleted first; a pair that still exists but has since dropped below
--threshold is deliberately left alone rather than silently discarded,
since it may already carry a human review.
"""

import argparse
import logging
import re
import time
from pathlib import Path

import numpy as np

import db
import find_duplicate_papers
import find_duplicates as fd
import lsh_index
import text_overlap
from resolve_author_openalex_ids import normalize_author_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("build_dupe_candidates")

# 2026-08-13: lowered from 0.90 after discovering the project's own best-documented ground-truth
# case (Saxby thesis vs. Ferdaus et al.'s MDPI "Taro Roots" -- see tests/cases/saxby-taro-2023.json)
# sits at 0.877 cosine similarity -- below the old 0.90 default, so it never became a candidate
# at all despite extraction/embedding/LSH all working correctly. 0.85 clears that case with margin.
# Lowering this after the corpus already has candidates requires a --full-rescan (see lsh_index.py's
# scan_candidate_pairs docstring) since incrementally-scanned pairs already rejected under the old,
# higher threshold won't otherwise be re-examined.
DEFAULT_THRESHOLD = 0.85
CITATION_WORD_OVERLAP = 0.7
STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "and", "or", "to", "with",
    "via", "towards", "toward", "using", "from", "into", "under", "over",
}
WORD_RE = re.compile(r"[a-z]{4,}")


def init_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS potential_dupes (
            id INTEGER PRIMARY KEY,
            paragraph_id_1 INTEGER NOT NULL REFERENCES paragraphs(id),
            paragraph_id_2 INTEGER NOT NULL REFERENCES paragraphs(id),
            paper_id_1 INTEGER NOT NULL REFERENCES papers(id),
            paper_id_2 INTEGER NOT NULL REFERENCES papers(id),
            similarity REAL NOT NULL,
            same_paper INTEGER NOT NULL,
            same_author INTEGER,
            later_cites_earlier INTEGER,
            earlier_paper_id INTEGER REFERENCES papers(id),
            later_paper_id INTEGER REFERENCES papers(id),
            lcs_ratio REAL,
            ngram_jaccard REAL,
            status TEXT NOT NULL DEFAULT 'unreviewed',
            reviewed_at TEXT,
            ai_check TEXT,
            ai_check_reason TEXT,
            ai_checked_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(paragraph_id_1, paragraph_id_2)
        );
        CREATE INDEX IF NOT EXISTS idx_potential_dupes_paper1 ON potential_dupes(paper_id_1);
        CREATE INDEX IF NOT EXISTS idx_potential_dupes_paper2 ON potential_dupes(paper_id_2);
        CREATE INDEX IF NOT EXISTS idx_potential_dupes_status ON potential_dupes(status);

        CREATE TABLE IF NOT EXISTS potential_dupe_authors (
            potential_dupe_id INTEGER NOT NULL REFERENCES potential_dupes(id),
            author_id INTEGER NOT NULL REFERENCES authors(id),
            PRIMARY KEY (potential_dupe_id, author_id)
        );
        CREATE INDEX IF NOT EXISTS idx_pda_author ON potential_dupe_authors(author_id);
        """
    )
    conn.commit()
    ensure_columns(conn)


def ensure_columns(conn):
    """CREATE TABLE IF NOT EXISTS above only helps a brand-new database --
    an existing potential_dupes table (this project already has one with
    live data) needs an explicit ALTER to pick up columns added later.
    SQLite has no ADD COLUMN IF NOT EXISTS, so check PRAGMA table_info first.

    ai_check/ai_check_reason/ai_checked_at: a coarse pre-filter pass distinct
    from `status` -- `status` is the human's d/f/u verdict via review_dupes.py
    on a candidate worth looking at; ai_check is an AI-assisted first pass
    that screens out the "obviously not a real duplicate" candidates (shared
    license/funding boilerplate, generic disclaimer text, etc. -- see
    todo.md) before a human ever has to look at them. 'yes' = plausible,
    worth a human review; 'no' = screened out, with ai_check_reason
    recording why. Never overwritten automatically once set (same spirit as
    `status`/`reviewed_at`), so re-running build_dupe_candidates.py can't
    silently erase a prior AI screening pass any more than it can a human's."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(potential_dupes)")}
    for column, coltype in (("lcs_ratio", "REAL"), ("ngram_jaccard", "REAL"),
                             ("ai_check", "TEXT"), ("ai_check_reason", "TEXT"), ("ai_checked_at", "TEXT")):
        if column not in existing:
            conn.execute(f"ALTER TABLE potential_dupes ADD COLUMN {column} {coltype}")
    conn.commit()


def load_paper_years(conn):
    return dict(conn.execute("SELECT id, year FROM papers").fetchall())


def load_paper_authors(conn):
    """paper_id -> set of author_id."""
    authors_by_paper = {}
    for paper_id, author_id in conn.execute("SELECT paper_id, author_id FROM paper_authors"):
        authors_by_paper.setdefault(paper_id, set()).add(author_id)
    return authors_by_paper


def load_paper_author_keys(conn):
    """paper_id -> set of normalized author names (normalize_author_name(): "Last, First" flipped to
    "First Last", accents/punctuation/case stripped). What same_author is computed from, rather than
    author_id: the `authors` table is keyed by the raw name string, and upstream sources disagree on
    order, so one person routinely has two author_ids -- "Marin, Lavinia" (Zenodo/DataCite) vs.
    "Lavinia Marin" (Crossref). Comparing ids missed 583 genuine same-author rows on the
    computer-ethics corpus (2026-09-19, see todo.md), each then surfacing as a fake cross-author
    candidate. Deliberately exact after normalization -- no initials/fuzzy matching: a wrong
    same_author=1 hides a real cross-author case from review, which is the expensive direction."""
    keys_by_paper = {}
    for paper_id, name in conn.execute(
            "SELECT pa.paper_id, a.name FROM paper_authors pa JOIN authors a ON a.id = pa.author_id"):
        key = normalize_author_name(name)
        if key:
            keys_by_paper.setdefault(paper_id, set()).add(key)
    return keys_by_paper


def backfill_same_author(conn, author_keys_by_paper):
    """Flip same_author 0 -> 1 on existing rows whose papers share a normalized author name -- rows
    persisted before load_paper_author_keys() existed, which incremental scanning never revisits.
    Only ever 0 -> 1 (the same correction review_dupes.py's (a) key makes), never touching
    status/reviewed_at. Cheap enough to run every time: one pass over same_author=0 rows."""
    rows = conn.execute(
        "SELECT id, paper_id_1, paper_id_2 FROM potential_dupes WHERE same_paper = 0 AND same_author = 0"
    ).fetchall()
    ids = [row_id for row_id, a, b in rows
           if author_keys_by_paper.get(a, set()) & author_keys_by_paper.get(b, set())]
    conn.executemany("UPDATE potential_dupes SET same_author = 1 WHERE id = ?", [(i,) for i in ids])
    conn.commit()
    return len(ids)


def load_citations(conn):
    """paper_id -> list of raw citation strings."""
    citations_by_paper = {}
    for paper_id, raw_text in conn.execute("SELECT paper_id, raw_text FROM citations"):
        citations_by_paper.setdefault(paper_id, []).append(raw_text)
    return citations_by_paper


def load_duplicate_paper_pairs(conn):
    """Set of frozenset({paper_id_1, paper_id_2}) pairs registered by
    find_duplicate_papers.py -- two `papers` rows known to be the same
    underlying work retrieved/cataloged twice (matching DOI, or the same
    title modulo case/HTML-entity/punctuation noise -- see that module's
    docstring). Consulted so a pair like this is never even scored as a
    fresh "cross-paper" candidate in the first place, rather than relying on
    a human noticing and pressing review_dupes.py's (p) key after the fact.
    Creates the table if find_duplicate_papers.py has never been run yet
    (an empty table -- same as not consulting it at all, just without a
    special-case branch here)."""
    find_duplicate_papers.init_table(conn)
    pairs = conn.execute("SELECT paper_id_1, paper_id_2 FROM duplicate_papers").fetchall()
    return {frozenset((a, b)) for a, b in pairs}


def significant_words(text):
    return set(WORD_RE.findall(text.lower())) - STOPWORDS


def cites(title, citation_texts):
    """Heuristic: does any raw citation string plausibly reference a paper
    with this title? Same spirit as retrieve_papers.py's Crossref title
    matching, but word-overlap instead of SequenceMatcher since a citation
    string also contains authors/venue/year the title won't match against."""
    title_words = significant_words(title)
    if len(title_words) < 3:
        return False
    for raw in citation_texts:
        overlap = title_words & significant_words(raw)
        if len(overlap) / len(title_words) >= CITATION_WORD_OVERLAP:
            return True
    return False


def chronology(paper_id_a, paper_id_b, years):
    year_a, year_b = years.get(paper_id_a), years.get(paper_id_b)
    if year_a is None or year_b is None or year_a == year_b:
        return None, None
    return (paper_id_a, paper_id_b) if year_a < year_b else (paper_id_b, paper_id_a)


def pairs_from_lsh(conn, threshold, min_length, max_bucket_size, full_rescan=False, logger_=None,
                    max_candidate_pairs=lsh_index.MAX_CANDIDATE_PAIRS):
    """Exact-cosine-verified pairs, sourced from lsh_index.scan_candidate_pairs()
    (incremental by default -- see its docstring) instead of a brute-force
    compare. Row data (title/text/embedding) is fetched only for the
    paragraph ids that actually show up in the candidate set
    (fd.load_paragraphs_by_ids), not the whole paragraphs table -- no O(n)
    (let alone O(n^2)) work happens against the full corpus here."""
    candidate_ids = lsh_index.scan_candidate_pairs(
        conn, max_bucket_size=max_bucket_size, logger_=logger_, full_rescan=full_rescan,
        max_candidate_pairs=max_candidate_pairs,
    )
    if not candidate_ids:
        return []

    needed_ids = {pid for pair in candidate_ids for pid in pair}
    if logger_:
        logger_.info("fetching row data for %d paragraph(s) referenced by %d LSH candidate pair(s)",
                      len(needed_ids), len(candidate_ids))
    by_id = {row[0]: row for row in fd.load_paragraphs_by_ids(conn, needed_ids)}

    if logger_:
        logger_.info("computing exact cosine similarity for %d candidate pair(s)", len(candidate_ids))
    pairs = []
    for id_a, id_b in candidate_ids:
        row_a, row_b = by_id.get(id_a), by_id.get(id_b)
        if row_a is None or row_b is None:
            continue  # stale bucket row -- paragraph re-embedded/removed since last indexed; delete_orphaned_candidates/invalidate_paragraphs clean these up over time
        if min_length and (len(row_a[3]) < min_length or len(row_b[3]) < min_length):
            continue
        vec_a = np.frombuffer(row_a[4], dtype=np.float32)
        vec_b = np.frombuffer(row_b[4], dtype=np.float32)
        score = float(np.dot(vec_a, vec_b))
        if score < threshold:
            continue
        same_paper = row_a[1] == row_b[1]
        pairs.append((score, row_a, row_b, same_paper))

    pairs.sort(key=lambda x: x[0], reverse=True)
    return pairs


def read_pairs_in_batches(conn, table, batch_size):
    """Paginates (paragraph_id_lo, paragraph_id_hi) rows out of `table` by
    rowid, batch_size at a time -- so a caller consuming a huge candidate
    set never holds more than one batch in memory, matching
    scan_candidate_pairs_to_table()'s own bounded-memory design on the
    write side. `table` must be a plain rowid table (no WITHOUT ROWID)."""
    last_rowid = 0
    while True:
        rows = conn.execute(
            f"SELECT rowid, paragraph_id_lo, paragraph_id_hi FROM {table} WHERE rowid > ? ORDER BY rowid LIMIT ?",
            (last_rowid, batch_size),
        ).fetchall()
        if not rows:
            return
        last_rowid = rows[-1][0]
        yield [(r[1], r[2]) for r in rows]


def process_lsh_candidates_streaming(conn, threshold, min_length, max_bucket_size, full_rescan, max_candidate_pairs,
                                      ngram_size, titles_by_paper, authors_by_paper, citations_by_paper, years,
                                      duplicate_paper_pairs, logger_=None, group_batch_size=50_000,
                                      pair_batch_size=200_000, author_keys_by_paper=None):
    """End-to-end streaming replacement for pairs_from_lsh() + a single
    build_candidates() call, added 2026-08-26 after a real OOM: at this
    corpus's post-21-bit-rehash bucket density, a --full-rescan's candidate
    set touches 65-96% of all 10.2M embedded paragraphs regardless of
    --max-bucket-size (measured directly -- lowering the cap doesn't help,
    see todo.md), so loading every touched paragraph's full embedding+text
    into memory at once (what pairs_from_lsh()'s fd.load_paragraphs_by_ids()
    call was designed for -- its own docstring says "a few thousand
    paragraphs at most") is tens of GB no matter how the pair-count ceiling
    is tuned. This function never holds more than pair_batch_size pairs'
    worth of paragraph data (embeddings + text) in memory at once, and never
    holds more than group_batch_size LSH groups' worth of raw bucket rows at
    once either (via scan_candidate_pairs_to_table()) -- peak memory is
    bounded by batch size, not corpus size, however large the corpus grows.

    Candidates are written to potential_dupes batch-by-batch as they're
    verified (via build_candidates(), unchanged, called once per batch
    instead of once for the whole run) rather than accumulated into one
    giant list first -- so a run that gets interrupted partway still has
    everything it persisted so far, same "commit progress as you go, don't
    lose it all to one failure" discipline as every other long-running
    script in this codebase.

    Returns the total number of candidate pairs persisted (across all
    batches, post-threshold-filter -- not the raw LSH candidate count)."""
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _lsh_candidate_staging "
        "(paragraph_id_lo INTEGER, paragraph_id_hi INTEGER, PRIMARY KEY (paragraph_id_lo, paragraph_id_hi))"
    )
    conn.execute("DELETE FROM _lsh_candidate_staging")
    conn.commit()

    n_staged = lsh_index.scan_candidate_pairs_to_table(
        conn, "_lsh_candidate_staging", max_bucket_size=max_bucket_size, logger_=logger_, full_rescan=full_rescan,
        max_candidate_pairs=max_candidate_pairs, group_batch_size=group_batch_size,
    )
    if not n_staged:
        conn.execute("DROP TABLE _lsh_candidate_staging")
        conn.commit()
        return 0

    total_persisted = 0
    batch_num = 0
    for batch in read_pairs_in_batches(conn, "_lsh_candidate_staging", pair_batch_size):
        batch_num += 1
        needed_ids = {pid for pair in batch for pid in pair}
        if logger_:
            logger_.info("batch %d: fetching row data for %d paragraph(s) referenced by %d candidate pair(s)",
                         batch_num, len(needed_ids), len(batch))
        by_id = {row[0]: row for row in fd.load_paragraphs_by_ids(conn, needed_ids)}

        pairs = []
        for id_a, id_b in batch:
            row_a, row_b = by_id.get(id_a), by_id.get(id_b)
            if row_a is None or row_b is None:
                continue  # stale bucket row -- see pairs_from_lsh()'s identical comment
            if min_length and (len(row_a[3]) < min_length or len(row_b[3]) < min_length):
                continue
            vec_a = np.frombuffer(row_a[4], dtype=np.float32)
            vec_b = np.frombuffer(row_b[4], dtype=np.float32)
            score = float(np.dot(vec_a, vec_b))
            if score < threshold:
                continue
            same_paper = row_a[1] == row_b[1]
            pairs.append((score, row_a, row_b, same_paper))
        pairs.sort(key=lambda x: x[0], reverse=True)

        if pairs:
            build_candidates(conn, pairs, titles_by_paper, authors_by_paper, citations_by_paper, years,
                              ngram_size, logger_=logger_, duplicate_paper_pairs=duplicate_paper_pairs,
                              author_keys_by_paper=author_keys_by_paper)
            total_persisted += len(pairs)
        if logger_:
            logger_.info("batch %d done: %d candidate(s) persisted this batch, %d total so far",
                         batch_num, len(pairs), total_persisted)

    conn.execute("DROP TABLE _lsh_candidate_staging")
    conn.commit()
    return total_persisted


def delete_orphaned_candidates(conn):
    """Removes potential_dupes rows that no longer have a valid embedded-paragraph pair on both
    sides -- either because a paragraph was deleted outright (extract_papers.py's same-document
    dedup, a paper re-extracted), or because a paragraph's embedding was retroactively cleared
    while the row itself still exists (embed_paragraphs.py's boilerplate/non-English skip
    catching, on a later run, a paragraph that was embedded normally the first time -- found live
    2026-08-30: 6,719 stale rows in the main corpus referenced a paragraph already correctly
    marked model='skipped-boilerplate', embedding=NULL, from exactly this sequence). Both cases
    mean the same thing downstream -- a candidate pair without two real embeddings isn't a valid
    candidate -- so both are cleaned up the same way, every run, rather than needing a one-off
    fix each time a new skip mechanism is added (see also fix_non_english_paragraphs.py, written
    for the same underlying problem before this general form of the check existed).

    NOT EXISTS (correlated), not "NOT IN (SELECT ...)": the NOT IN form was confirmed via
    EXPLAIN QUERY PLAN to make SQLite materialize the *entire* subquery result as an ephemeral
    in-memory structure -- twice, once per NOT IN clause ('LIST SUBQUERY 1'/'LIST SUBQUERY 2',
    each a full 'SCAN paragraphs') -- rather than a per-row indexed lookup. At real corpus scale
    (paragraphs in the tens of millions) that's tens of millions of ids materialized twice on
    every single run of this function, which turned out to be the actual, largest contributor to
    a real multi-GB RSS climb chasing what looked at first like an unrelated LSH-sync memory bug
    (see todo.md's 2026-09-03 post-mortem -- this was the true final piece of that investigation,
    found only by comparing an isolated reproduction that stayed flat against the real pipeline
    invocation that didn't, then diffing exactly what the real one did that the reproduction
    didn't). NOT EXISTS lets SQLite do one indexed point-lookup into `paragraphs` (primary key on
    `id`) per potential_dupes row instead -- cheap regardless of how large `paragraphs` is, since
    potential_dupes (the candidate table, not the corpus) is what actually bounds the row count
    here."""
    cur = conn.execute(
        """
        DELETE FROM potential_dupes
        WHERE NOT EXISTS (SELECT 1 FROM paragraphs p WHERE p.id = potential_dupes.paragraph_id_1
                             AND p.embedding IS NOT NULL)
           OR NOT EXISTS (SELECT 1 FROM paragraphs p WHERE p.id = potential_dupes.paragraph_id_2
                             AND p.embedding IS NOT NULL)
        """
    )
    conn.commit()
    return cur.rowcount


def build_candidates(conn, pairs, titles_by_paper, authors_by_paper, citations_by_paper, years, ngram_size,
                      logger_=None, duplicate_paper_pairs=None, author_keys_by_paper=None):
    """author_keys_by_paper: load_paper_author_keys()'s normalized-name sets, what same_author is
    computed from. None falls back to comparing authors_by_paper's author_ids (the old behavior,
    kept for callers/tests that don't load names)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    author_links = []
    duplicate_paper_pairs = duplicate_paper_pairs or set()

    # Commit periodically rather than once at the end -- library.sqlite3 can have other
    # writers connected concurrently (embed_paragraphs.py, another script) with a 30s
    # busy-timeout; one long uncommitted transaction over a large `pairs` list can hold
    # the write lock past that and crash a concurrent writer with "database is locked"
    # (see lsh_index.py's sync_index() and todo.md's post-mortem -- same failure mode).
    COMMIT_EVERY = 2000
    for i, (score, row_a, row_b, same_paper) in enumerate(pairs):
        if logger_ and i % COMMIT_EVERY == 0:
            logger_.info("persisted %d/%d candidate(s)", i, len(pairs))
        para_id_a, paper_id_a, text_a = row_a[0], row_a[1], row_a[3]
        para_id_b, paper_id_b, text_b = row_b[0], row_b[1], row_b[3]

        # Two DIFFERENT paper_ids can still be the same underlying work retrieved twice
        # (find_duplicate_papers.py's job -- title-case/HTML-entity/DOI-duplicate retrieval,
        # not a real cross-paper pair at all) -- same_paper=True here means the rest of this
        # function treats it exactly like a genuine same-paper_id match (same_author/
        # chronology left NULL rather than computed), same as review_dupes.py's (p) key would
        # correct it to by hand after the fact.
        if not same_paper and frozenset((paper_id_a, paper_id_b)) in duplicate_paper_pairs:
            same_paper = True

        # Same-author is only meaningful comparing two DIFFERENT papers (todo.md's "if the
        # author of the older paper is also the author of the newer paper..."); for a
        # same-paper pair it would be vacuously true (a paper always shares its own authors
        # with itself), so it's left NULL there rather than reporting a meaningless 1.
        author_sets = author_keys_by_paper if author_keys_by_paper is not None else authors_by_paper
        same_author = None if same_paper else bool(
            author_sets.get(paper_id_a, set()) & author_sets.get(paper_id_b, set())
        )

        earlier_id, later_id = (None, None) if same_paper else chronology(paper_id_a, paper_id_b, years)
        later_cites_earlier = None
        if earlier_id is not None:
            later_cites_earlier = cites(titles_by_paper[earlier_id], citations_by_paper.get(later_id, []))

        # More accurate than cosine similarity alone, and exactly why it only runs here:
        # too expensive to run across a whole corpus's O(n^2) pairs (text_overlap.py),
        # cheap once LSH + cosine have already narrowed things down to this candidate set.
        lcs_ratio = text_overlap.longest_common_word_run(text_a, text_b)
        ngram_jaccard = text_overlap.ngram_jaccard(text_a, text_b, n=ngram_size)

        cur = conn.execute(
            """
            INSERT INTO potential_dupes
                (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2, similarity,
                 same_paper, same_author, later_cites_earlier, earlier_paper_id, later_paper_id,
                 lcs_ratio, ngram_jaccard, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paragraph_id_1, paragraph_id_2) DO UPDATE SET
                similarity=excluded.similarity, same_paper=excluded.same_paper,
                same_author=excluded.same_author, later_cites_earlier=excluded.later_cites_earlier,
                earlier_paper_id=excluded.earlier_paper_id, later_paper_id=excluded.later_paper_id,
                lcs_ratio=excluded.lcs_ratio, ngram_jaccard=excluded.ngram_jaccard
            RETURNING id
            """,
            (para_id_a, para_id_b, paper_id_a, paper_id_b, score,
             int(same_paper), None if same_author is None else int(same_author),
             later_cites_earlier, earlier_id, later_id, lcs_ratio, ngram_jaccard, now),
        )
        dupe_id = cur.fetchone()[0]
        for author_id in authors_by_paper.get(paper_id_a, set()) | authors_by_paper.get(paper_id_b, set()):
            author_links.append((dupe_id, author_id))

        if author_links and (i + 1) % COMMIT_EVERY == 0:
            conn.executemany(
                "INSERT OR IGNORE INTO potential_dupe_authors (potential_dupe_id, author_id) VALUES (?, ?)",
                author_links,
            )
            author_links = []
            conn.commit()

    if author_links:
        conn.executemany(
            "INSERT OR IGNORE INTO potential_dupe_authors (potential_dupe_id, author_id) VALUES (?, ?)",
            author_links,
        )
    conn.commit()


def parse_args():
    parser = argparse.ArgumentParser(description="Persist candidate duplicate pairs into library.sqlite3.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                         help=f"minimum cosine similarity to persist as a candidate (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--min-length", type=int, default=0, help="skip paragraphs shorter than N characters")
    parser.add_argument("--max-bucket-size", type=int, default=lsh_index.DEFAULT_MAX_BUCKET_SIZE,
                         help="skip LSH buckets bigger than this when generating candidates "
                              f"(default {lsh_index.DEFAULT_MAX_BUCKET_SIZE}; see todo.md's LSH design writeup)")
    parser.add_argument("--max-candidate-pairs", type=int, default=lsh_index.MAX_CANDIDATE_PAIRS,
                         help="safety ceiling on projected candidate-pair count before scan_candidate_pairs() "
                              f"does any expensive work (default {lsh_index.MAX_CANDIDATE_PAIRS:,}). Raise this "
                              "deliberately, alongside --max-bucket-size, only once you've sized real memory "
                              "headroom for it -- see lsh_index.MAX_CANDIDATE_PAIRS's own comment for the "
                              "measured ~155 bytes/pair cost of the final candidate set.")
    parser.add_argument("--brute-force", action="store_true",
                         help="fall back to the old exhaustive N x N compare instead of the LSH candidate index "
                              "(e.g. to spot-check recall, or for a --threshold lower than the index was tuned for)")
    parser.add_argument("--full-rescan", action="store_true",
                         help="re-derive candidates from the WHOLE LSH index instead of just what's new since "
                              "the last scan (lsh_index.scan_candidate_pairs's default). Needed to catch a pair "
                              "that was already co-bucketed before but rejected at an earlier, higher --threshold "
                              "-- incremental scanning won't re-examine it. Slower; see lsh_index.py's docstring.")
    parser.add_argument("--ngram-size", type=int, default=5,
                         help="word n-gram size for the ngram_jaccard textual-overlap check (default 5; "
                              "see text_overlap.py)")
    parser.add_argument("--group-batch-size", type=int, default=50_000,
                         help="LSH groups processed per chunk during candidate discovery (default 50,000) -- "
                              "see lsh_index.scan_candidate_pairs_to_table()'s docstring for the OOM this bounds")
    parser.add_argument("--pair-batch-size", type=int, default=200_000,
                         help="candidate pairs verified/persisted per batch (default 200,000) -- bounds how much "
                              "paragraph embedding/text data is ever loaded into memory at once, regardless of "
                              "total candidate-set size; see process_lsh_candidates_streaming()'s docstring")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)  # extract_papers.py/embed_paragraphs.py may be writing concurrently (see db.py: WAL mode)
    init_tables(conn)

    removed = delete_orphaned_candidates(conn)
    if removed:
        logger.info("removed %d candidate(s) referencing paragraphs that no longer exist", removed)

    logger.info("looking up papers/authors/citations for enrichment...")
    titles_by_paper = dict(conn.execute("SELECT id, title FROM papers"))
    authors_by_paper = load_paper_authors(conn)
    author_keys_by_paper = load_paper_author_keys(conn)
    flipped = backfill_same_author(conn, author_keys_by_paper)
    if flipped:
        logger.info("corrected same_author 0 -> 1 on %d existing candidate(s) (author name-order variants)", flipped)
    citations_by_paper = load_citations(conn)
    years = load_paper_years(conn)
    duplicate_paper_pairs = load_duplicate_paper_pairs(conn)
    if duplicate_paper_pairs:
        logger.info("%d known duplicate-paper pair(s) (find_duplicate_papers.py) will be treated as same_paper",
                     len(duplicate_paper_pairs))

    if args.brute_force:
        # Brute force inherently needs every embedded paragraph -- this is the one
        # path that still pulls the full table, same as find_duplicates.py. Not
        # the target of the 2026-08-26 streaming fix (see process_lsh_candidates_streaming()):
        # --brute-force is documented as a small-scale spot-check path, not the
        # default full-corpus one.
        logger.info("loading every embedded paragraph for a brute-force N x N compare...")
        rows = fd.load_paragraphs(conn)
        if len(rows) < 2:
            logger.info("fewer than 2 embedded paragraphs -- nothing to compare. Run embed_paragraphs.py first.")
            return
        logger.info("loaded %d paragraph(s); computing pairwise cosine similarity", len(rows))
        mat = fd.to_matrix(rows)
        pairs = fd.find_pairs(rows, mat, args.threshold, cross_paper_only=False, min_length=args.min_length)
        logger.info("persisting %d candidate pair(s) into potential_dupes...", len(pairs))
        build_candidates(conn, pairs, titles_by_paper, authors_by_paper, citations_by_paper, years, args.ngram_size,
                          logger_=logger, duplicate_paper_pairs=duplicate_paper_pairs,
                          author_keys_by_paper=author_keys_by_paper)
        n_pairs_this_run = len(pairs)
    else:
        count = conn.execute("SELECT COUNT(*) FROM paragraphs WHERE embedding IS NOT NULL").fetchone()[0]
        if count < 2:
            logger.info("fewer than 2 embedded paragraphs -- nothing to compare. Run embed_paragraphs.py first.")
            return
        lsh_index.init_lsh_tables(conn)
        dim, model_name = conn.execute(
            "SELECT embedding_dim, model FROM paragraphs WHERE embedding IS NOT NULL LIMIT 1"
        ).fetchone()
        logger.info("syncing LSH index against %d embedded paragraph(s)...", count)
        _, n_indexed = lsh_index.sync_index(conn, model=model_name, embedding_dim=dim, logger_=logger)
        if n_indexed:
            logger.info("LSH-indexed %d paragraph(s) not previously in the candidate lookup", n_indexed)
        total_persisted = process_lsh_candidates_streaming(
            conn, args.threshold, args.min_length, args.max_bucket_size, args.full_rescan, args.max_candidate_pairs,
            args.ngram_size, titles_by_paper, authors_by_paper, citations_by_paper, years, duplicate_paper_pairs,
            logger_=logger, group_batch_size=args.group_batch_size, pair_batch_size=args.pair_batch_size,
            author_keys_by_paper=author_keys_by_paper,
        )
        logger.info("done: %d candidate pair(s) persisted into potential_dupes", total_persisted)
        n_pairs_this_run = total_persisted

    total = conn.execute("SELECT COUNT(*) FROM potential_dupes").fetchone()[0]
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM potential_dupes GROUP BY status"))
    same_author_n = conn.execute("SELECT COUNT(*) FROM potential_dupes WHERE same_author = 1").fetchone()[0]
    cited_n = conn.execute("SELECT COUNT(*) FROM potential_dupes WHERE later_cites_earlier = 1").fetchone()[0]
    conn.close()

    logger.info("%d pair(s) >= %.2f similarity -> %d candidate(s) total in potential_dupes",
                n_pairs_this_run, args.threshold, total)
    logger.info("  same_author=1: %d  |  later_cites_earlier=1: %d  |  by status: %s",
                same_author_n, cited_n, by_status)


if __name__ == "__main__":
    main()
