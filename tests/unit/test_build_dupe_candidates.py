#!/usr/bin/env python3
"""Unit tests for build_dupe_candidates.py's streaming candidate pipeline
(read_pairs_in_batches, process_lsh_candidates_streaming) -- added 2026-08-26
after a real OOM on this project's own corpus: pairs_from_lsh()'s
fd.load_paragraphs_by_ids() call was designed for "a few thousand paragraphs
at most" (its own docstring) but a post-21-bit-rehash --full-rescan's
candidate set touches 65-96% of the whole corpus regardless of
--max-bucket-size, so loading every touched paragraph's embedding+text at
once is tens of GB no matter how the pair-count ceiling is tuned. These
tests confirm the batched replacement produces the same end result as the
old all-at-once path would have, just without ever holding more than one
batch in memory."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import build_dupe_candidates as bdc  # noqa: E402
import lsh_index  # noqa: E402


def make_db():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "library.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT, year INTEGER,
                              file_path TEXT UNIQUE, extracted_at TEXT);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE paper_authors (paper_id INTEGER, author_id INTEGER, author_order INTEGER,
                                     PRIMARY KEY (paper_id, author_id));
        CREATE TABLE citations (id INTEGER PRIMARY KEY, paper_id INTEGER, raw_text TEXT);
        CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, para_index INTEGER,
                                  text TEXT, embedding BLOB, embedding_dim INTEGER, model TEXT);
        """
    )
    bdc.init_tables(conn)
    conn.commit()
    return conn, Path(tmpdir)


def insert_paragraph(conn, pid, paper_id, text, vector):
    # Unit-normalized, matching real embeddings (embed_paragraphs.py's normalize_embeddings=True) --
    # the actual scoring code (process_lsh_candidates_streaming, pairs_from_lsh) is a raw np.dot(),
    # which only equals cosine similarity because real embeddings are pre-normalized. An un-normalized
    # test vector produces a "similarity" outside [-1, 1], not a realistic fixture.
    v = np.asarray(vector, dtype=np.float32)
    v = v / np.linalg.norm(v)
    conn.execute(
        "INSERT INTO paragraphs (id, paper_id, para_index, text, embedding, embedding_dim, model) "
        "VALUES (?, ?, 0, ?, ?, ?, 'test-model')",
        (pid, paper_id, text, v.tobytes(), len(v)),
    )


class TestDeleteOrphanedCandidates(unittest.TestCase):
    """2026-08-30: delete_orphaned_candidates() used to only check whether a referenced
    paragraph row still existed -- not whether it still had a real embedding. A paragraph whose
    embedding was retroactively cleared (embed_paragraphs.py's boilerplate/non-English skip
    catching, on a later run, something it embedded normally the first time) left the row itself
    in place, so the old NOT IN (SELECT id FROM paragraphs) check missed it entirely. Found live
    in the main corpus: 6,719 stale rows referencing an already-correctly-skipped paragraph."""

    def setUp(self):
        self.conn, _ = make_db()
        self.conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        self.conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")

    def tearDown(self):
        self.conn.close()

    def _insert_candidate(self, cid, p1, p2):
        self.conn.execute(
            """INSERT INTO potential_dupes (id, paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
                                             similarity, same_paper, created_at)
               VALUES (?, ?, ?, 1, 2, 0.9, 0, '2026-01-01T00:00:00Z')""",
            (cid, p1, p2),
        )

    def test_removes_row_referencing_deleted_paragraph(self):
        insert_paragraph(self.conn, 100, 1, "text", [1, 0, 0, 0])
        # paragraph 200 deliberately never inserted -- simulates a deleted row
        self._insert_candidate(1, 100, 200)
        self.conn.commit()
        removed = bdc.delete_orphaned_candidates(self.conn)
        self.assertEqual(removed, 1)

    def test_removes_row_referencing_paragraph_with_cleared_embedding(self):
        insert_paragraph(self.conn, 100, 1, "text", [1, 0, 0, 0])
        insert_paragraph(self.conn, 200, 2, "text", [0, 1, 0, 0])
        self._insert_candidate(1, 100, 200)
        self.conn.commit()
        # Simulate embed_paragraphs.py retroactively skip-marking paragraph 200 on a later run:
        # the row persists, but embedding is cleared.
        self.conn.execute("UPDATE paragraphs SET embedding = NULL, model = 'skipped-boilerplate' WHERE id = 200")
        self.conn.commit()
        removed = bdc.delete_orphaned_candidates(self.conn)
        self.assertEqual(removed, 1)
        self.assertIsNone(self.conn.execute("SELECT id FROM potential_dupes WHERE id = 1").fetchone())

    def test_leaves_valid_candidates_alone(self):
        insert_paragraph(self.conn, 100, 1, "text", [1, 0, 0, 0])
        insert_paragraph(self.conn, 200, 2, "text", [0, 1, 0, 0])
        self._insert_candidate(1, 100, 200)
        self.conn.commit()
        removed = bdc.delete_orphaned_candidates(self.conn)
        self.assertEqual(removed, 0)
        self.assertIsNotNone(self.conn.execute("SELECT id FROM potential_dupes WHERE id = 1").fetchone())


class TestReadPairsInBatches(unittest.TestCase):
    def setUp(self):
        self.conn, _ = make_db()
        self.conn.execute(
            "CREATE TEMP TABLE staging (paragraph_id_lo INTEGER, paragraph_id_hi INTEGER, "
            "PRIMARY KEY (paragraph_id_lo, paragraph_id_hi))"
        )
        self.conn.executemany(
            "INSERT INTO staging (paragraph_id_lo, paragraph_id_hi) VALUES (?, ?)",
            [(i, i + 1) for i in range(1, 11, 2)],  # 5 pairs
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_all_pairs_returned_across_batches(self):
        batches = list(bdc.read_pairs_in_batches(self.conn, "staging", batch_size=2))
        all_pairs = {p for batch in batches for p in batch}
        self.assertEqual(len(all_pairs), 5)
        self.assertEqual(len(batches), 3)  # 2 + 2 + 1

    def test_no_batch_exceeds_batch_size(self):
        batches = list(bdc.read_pairs_in_batches(self.conn, "staging", batch_size=2))
        for batch in batches:
            self.assertLessEqual(len(batch), 2)

    def test_empty_table_yields_nothing(self):
        self.conn.execute("DELETE FROM staging")
        self.conn.commit()
        batches = list(bdc.read_pairs_in_batches(self.conn, "staging", batch_size=2))
        self.assertEqual(batches, [])


class TestProcessLshCandidatesStreaming(unittest.TestCase):
    def setUp(self):
        self.conn, _ = make_db()
        self.conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'Paper A', 'a.pdf')")
        self.conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'Paper B', 'b.pdf')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _enrichment(self):
        return (dict(self.conn.execute("SELECT id, title FROM papers")),
                bdc.load_paper_authors(self.conn), bdc.load_citations(self.conn),
                bdc.load_paper_years(self.conn), set())

    def test_duplicate_pair_persisted(self):
        vector = np.random.default_rng(1).standard_normal(8)
        insert_paragraph(self.conn, 1, 1, "A paragraph about fairness in machine learning systems.", vector)
        insert_paragraph(self.conn, 2, 2, "A paragraph about fairness in machine learning systems.", vector)
        self.conn.commit()
        lsh_index.sync_index(self.conn, model="test-model", embedding_dim=8)

        titles, authors, citations, years, dup_pairs = self._enrichment()
        n = bdc.process_lsh_candidates_streaming(
            self.conn, threshold=0.5, min_length=0, max_bucket_size=30, full_rescan=False,
            max_candidate_pairs=1000, ngram_size=5, titles_by_paper=titles, authors_by_paper=authors,
            citations_by_paper=citations, years=years, duplicate_paper_pairs=dup_pairs,
        )
        self.assertEqual(n, 1)
        row = self.conn.execute("SELECT paper_id_1, paper_id_2, similarity FROM potential_dupes").fetchone()
        self.assertEqual((row[0], row[1]), (1, 2))
        self.assertAlmostEqual(row[2], 1.0, places=4)

    def test_below_threshold_not_persisted(self):
        rng = np.random.default_rng(2)
        insert_paragraph(self.conn, 1, 1, "Some original text about privacy.", rng.standard_normal(8))
        insert_paragraph(self.conn, 2, 2, "Totally unrelated content about robotics.", rng.standard_normal(8))
        self.conn.commit()
        lsh_index.sync_index(self.conn, model="test-model", embedding_dim=8)

        titles, authors, citations, years, dup_pairs = self._enrichment()
        n = bdc.process_lsh_candidates_streaming(
            self.conn, threshold=0.999, min_length=0, max_bucket_size=30, full_rescan=False,
            max_candidate_pairs=1000, ngram_size=5, titles_by_paper=titles, authors_by_paper=authors,
            citations_by_paper=citations, years=years, duplicate_paper_pairs=dup_pairs,
        )
        self.assertEqual(n, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM potential_dupes").fetchone()[0], 0)

    def test_small_pair_batch_size_still_finds_everything(self):
        """pair_batch_size=1 forces every candidate into its own batch --
        the persisted result must be identical to a single large batch."""
        rng = np.random.default_rng(3)
        v1, v2 = rng.standard_normal(8), rng.standard_normal(8)
        insert_paragraph(self.conn, 1, 1, "Text cluster one, first copy.", v1)
        insert_paragraph(self.conn, 2, 2, "Text cluster one, first copy.", v1)
        insert_paragraph(self.conn, 3, 1, "Text cluster two, first copy.", v2)
        insert_paragraph(self.conn, 4, 2, "Text cluster two, first copy.", v2)
        self.conn.commit()
        lsh_index.sync_index(self.conn, model="test-model", embedding_dim=8)

        titles, authors, citations, years, dup_pairs = self._enrichment()
        n = bdc.process_lsh_candidates_streaming(
            self.conn, threshold=0.5, min_length=0, max_bucket_size=30, full_rescan=False,
            max_candidate_pairs=1000, ngram_size=5, titles_by_paper=titles, authors_by_paper=authors,
            citations_by_paper=citations, years=years, duplicate_paper_pairs=dup_pairs,
            group_batch_size=1, pair_batch_size=1,
        )
        self.assertEqual(n, 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM potential_dupes").fetchone()[0], 2)

    def test_staging_table_cleaned_up(self):
        vector = np.random.default_rng(4).standard_normal(8)
        insert_paragraph(self.conn, 1, 1, "Shared text for cleanup test.", vector)
        insert_paragraph(self.conn, 2, 2, "Shared text for cleanup test.", vector)
        self.conn.commit()
        lsh_index.sync_index(self.conn, model="test-model", embedding_dim=8)

        titles, authors, citations, years, dup_pairs = self._enrichment()
        bdc.process_lsh_candidates_streaming(
            self.conn, threshold=0.5, min_length=0, max_bucket_size=30, full_rescan=False,
            max_candidate_pairs=1000, ngram_size=5, titles_by_paper=titles, authors_by_paper=authors,
            citations_by_paper=citations, years=years, duplicate_paper_pairs=dup_pairs,
        )
        tables = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_temp_master WHERE type='table'")}
        self.assertNotIn("_lsh_candidate_staging", tables)

    def test_nothing_staged_returns_zero(self):
        lsh_index.init_lsh_tables(self.conn)  # normally done by main() before this pipeline runs
        titles, authors, citations, years, dup_pairs = self._enrichment()
        n = bdc.process_lsh_candidates_streaming(
            self.conn, threshold=0.5, min_length=0, max_bucket_size=30, full_rescan=False,
            max_candidate_pairs=1000, ngram_size=5, titles_by_paper=titles, authors_by_paper=authors,
            citations_by_paper=citations, years=years, duplicate_paper_pairs=dup_pairs,
        )
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
