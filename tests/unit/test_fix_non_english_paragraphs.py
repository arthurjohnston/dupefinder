#!/usr/bin/env python3
"""Unit tests for fix_non_english_paragraphs.py -- the retroactive cleanup
for paragraphs that were wrongly embedded as "English" before the
2026-08-30 english_score() bug fix (see review_dupes.english_score()'s
docstring)."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import build_dupe_candidates as bdc  # noqa: E402
import embed_paragraphs as ep  # noqa: E402
import fix_non_english_paragraphs as fnep  # noqa: E402
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
    lsh_index.init_lsh_tables(conn)
    conn.commit()
    return conn


def insert_paragraph(conn, pid, paper_id, text):
    v = np.random.RandomState(pid).rand(8).astype(np.float32)
    v = v / np.linalg.norm(v)
    conn.execute(
        "INSERT INTO paragraphs (id, paper_id, para_index, text, embedding, embedding_dim, model) "
        "VALUES (?, ?, 0, ?, ?, ?, 'test-model')",
        (pid, paper_id, text, v.tobytes(), len(v)),
    )


def insert_candidate(conn, cid, p1, p2, paper1, paper2, status="unreviewed"):
    conn.execute(
        """INSERT INTO potential_dupes (id, paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
                                         similarity, same_paper, status, created_at)
           VALUES (?, ?, ?, ?, ?, 0.9, 0, ?, '2026-01-01T00:00:00Z')""",
        (cid, p1, p2, paper1, paper2, status),
    )


class TestFixNonEnglishParagraphs(unittest.TestCase):
    RUSSIAN_CITATION = ("24 Махмутов З. А., Габдрахманова Г. Ф. Особенности этнической идентичности "
                         "виртуальных татарских сообществ в социальной сети Вконтакте // "
                         "Историческая этнология. 2016. Т. 1, No 2. С. 276-292.")

    def test_clears_embedding_and_marks_sentinel(self):
        conn = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        insert_paragraph(conn, 100, 1, self.RUSSIAN_CITATION)
        insert_paragraph(conn, 101, 1, "This is a genuinely English paragraph about machine learning.")
        conn.commit()

        result = fnep.run(conn, min_english_score=0.03)
        self.assertEqual(result["paragraphs_fixed"], 1)

        row = conn.execute("SELECT embedding, embedding_dim, model FROM paragraphs WHERE id=100").fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertEqual(row[2], ep.SKIPPED_MODEL_SENTINEL)

        # The genuinely English paragraph must be untouched.
        row2 = conn.execute("SELECT embedding, model FROM paragraphs WHERE id=101").fetchone()
        self.assertIsNotNone(row2[0])
        self.assertEqual(row2[1], "test-model")

    def test_deletes_referencing_potential_dupes_and_orphaned_authors(self):
        conn = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        conn.execute("INSERT INTO authors (id, name) VALUES (1, 'Someone')")
        insert_paragraph(conn, 100, 1, self.RUSSIAN_CITATION)
        insert_paragraph(conn, 200, 2, self.RUSSIAN_CITATION)
        insert_candidate(conn, 1, 100, 200, 1, 2)
        conn.execute("INSERT INTO potential_dupe_authors (potential_dupe_id, author_id) VALUES (1, 1)")
        conn.commit()

        result = fnep.run(conn, min_english_score=0.03)
        self.assertEqual(result["candidates_removed"], 1)
        self.assertIsNone(conn.execute("SELECT id FROM potential_dupes WHERE id=1").fetchone())
        self.assertIsNone(
            conn.execute("SELECT * FROM potential_dupe_authors WHERE potential_dupe_id=1").fetchone()
        )

    def test_removes_lsh_bucket_rows(self):
        conn = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        insert_paragraph(conn, 100, 1, self.RUSSIAN_CITATION)
        conn.execute("INSERT INTO lsh_buckets (table_num, bucket_key, paragraph_id) VALUES (0, 42, 100)")
        conn.execute("INSERT INTO lsh_scanned (paragraph_id) VALUES (100)")
        conn.commit()

        fnep.run(conn, min_english_score=0.03)
        self.assertIsNone(conn.execute("SELECT * FROM lsh_buckets WHERE paragraph_id=100").fetchone())
        self.assertIsNone(conn.execute("SELECT * FROM lsh_scanned WHERE paragraph_id=100").fetchone())

    def test_preserves_english_paragraph_candidates(self):
        conn = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        insert_paragraph(conn, 100, 1, "This is a genuinely English paragraph about machine learning fairness.")
        insert_paragraph(conn, 200, 2, "This is a genuinely English paragraph about machine learning fairness.")
        insert_candidate(conn, 1, 100, 200, 1, 2)
        conn.commit()

        result = fnep.run(conn, min_english_score=0.03)
        self.assertEqual(result["paragraphs_fixed"], 0)
        self.assertEqual(result["candidates_removed"], 0)
        self.assertIsNotNone(conn.execute("SELECT id FROM potential_dupes WHERE id=1").fetchone())

    def test_reviewed_candidate_still_removed_but_logged(self):
        conn = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        insert_paragraph(conn, 100, 1, self.RUSSIAN_CITATION)
        insert_paragraph(conn, 200, 2, self.RUSSIAN_CITATION)
        insert_candidate(conn, 1, 100, 200, 1, 2, status="confirmed")
        conn.commit()

        result = fnep.run(conn, min_english_score=0.03)
        self.assertEqual(result["candidates_removed"], 1)
        self.assertIsNone(conn.execute("SELECT id FROM potential_dupes WHERE id=1").fetchone())

    def test_idempotent_second_run_finds_nothing(self):
        conn = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        insert_paragraph(conn, 100, 1, self.RUSSIAN_CITATION)
        conn.commit()

        fnep.run(conn, min_english_score=0.03)
        result = fnep.run(conn, min_english_score=0.03)
        self.assertEqual(result["paragraphs_fixed"], 0)

    def test_large_id_list_does_not_hit_sqlite_variable_limit(self):
        # Regression test for a real crash: the main corpus's 302,636-id cleanup hit SQLite's
        # "too many SQL variables" error because the original implementation bound every id into
        # one single query instead of chunking. 2,000 ids is well past CHUNK_SIZE (500) and enough
        # to prove chunking is actually happening, without the test itself being slow.
        conn = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        n = 2000
        for i in range(n):
            insert_paragraph(conn, 100 + i, 1, self.RUSSIAN_CITATION)
        conn.commit()

        result = fnep.run(conn, min_english_score=0.03)
        self.assertEqual(result["paragraphs_fixed"], n)
        remaining = conn.execute("SELECT COUNT(*) FROM paragraphs WHERE embedding IS NOT NULL").fetchone()[0]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
