#!/usr/bin/env python3
"""Unit tests for find_review_candidates.py -- in particular that the two
--sort modes ('lcs' vs 'gap', todo.md post-mortem 7's "different slice"
follow-up) actually produce different orderings, and that the base filter
(ai_check IS NULL, same_paper=0, same_author=0, all three metrics < max)
is applied correctly."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import build_dupe_candidates as bdc  # noqa: E402
import find_review_candidates as frc  # noqa: E402


class TestFindCandidates(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "library.sqlite3"
        self.conn = db.connect(self.db_path)
        self.conn.executescript(
            """
            CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, text TEXT);
            """
        )
        bdc.init_tables(self.conn)
        self.conn.executemany(
            "INSERT INTO papers (id, title) VALUES (?, ?)",
            [(1, "Paper One"), (2, "Paper Two"), (3, "Paper Three"), (4, "Paper Four")],
        )
        self.conn.executemany(
            "INSERT INTO paragraphs (id, paper_id, text) VALUES (?, ?, ?)",
            [(10, 1, "text a"), (11, 2, "text b"), (12, 3, "text c"), (13, 4, "text d")],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _insert_dupe(self, pid1, pid2, paper1, paper2, similarity, lcs_ratio, ngram_jaccard,
                      same_author=0, ai_check=None):
        self.conn.execute(
            """
            INSERT INTO potential_dupes
                (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2, similarity,
                 same_paper, same_author, lcs_ratio, ngram_jaccard, ai_check, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, '2026-01-01T00:00:00Z')
            """,
            (pid1, pid2, paper1, paper2, similarity, same_author, lcs_ratio, ngram_jaccard, ai_check),
        )
        self.conn.commit()

    def test_lcs_sort_ranks_high_lcs_first(self):
        # high lcs_ratio, low ngram_jaccard vs. low lcs_ratio, high ngram_jaccard
        self._insert_dupe(10, 11, 1, 2, 0.90, 0.90, 0.20)
        self._insert_dupe(12, 13, 3, 4, 0.90, 0.20, 0.90)
        rows = frc.find_candidates(self.conn, limit=10, max_metric=0.99, sort="lcs")
        self.assertEqual([r[0] for r in rows], [1, 2])  # id 1 (lcs=0.90) ranked before id 2 (lcs=0.20)

    def test_gap_sort_ranks_high_ngram_minus_lcs_first(self):
        self._insert_dupe(10, 11, 1, 2, 0.90, 0.90, 0.20)  # gap = 0.20 - 0.90 = -0.70
        self._insert_dupe(12, 13, 3, 4, 0.90, 0.20, 0.90)  # gap = 0.90 - 0.20 = +0.70
        rows = frc.find_candidates(self.conn, limit=10, max_metric=0.99, sort="gap")
        self.assertEqual([r[0] for r in rows], [2, 1])  # id 2 (bigger gap) ranked first

    def test_excludes_same_author_rows(self):
        self._insert_dupe(10, 11, 1, 2, 0.90, 0.50, 0.50, same_author=1)
        rows = frc.find_candidates(self.conn, limit=10, max_metric=0.99, sort="lcs")
        self.assertEqual(rows, [])

    def test_excludes_already_classified_rows(self):
        self._insert_dupe(10, 11, 1, 2, 0.90, 0.50, 0.50, ai_check="no")
        rows = frc.find_candidates(self.conn, limit=10, max_metric=0.99, sort="lcs")
        self.assertEqual(rows, [])

    def test_excludes_rows_at_or_above_max_metric(self):
        self._insert_dupe(10, 11, 1, 2, 0.995, 0.50, 0.50)  # similarity >= 0.99
        rows = frc.find_candidates(self.conn, limit=10, max_metric=0.99, sort="lcs")
        self.assertEqual(rows, [])

    def test_offset_and_limit_paginate_correctly(self):
        self._insert_dupe(10, 11, 1, 2, 0.90, 0.90, 0.20)
        self._insert_dupe(12, 13, 3, 4, 0.90, 0.70, 0.20)
        first = frc.find_candidates(self.conn, limit=1, max_metric=0.99, offset=0, sort="lcs")
        second = frc.find_candidates(self.conn, limit=1, max_metric=0.99, offset=1, sort="lcs")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0][0], second[0][0])


if __name__ == "__main__":
    unittest.main()
