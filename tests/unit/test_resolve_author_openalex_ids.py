#!/usr/bin/env python3
"""Unit tests for resolve_author_openalex_ids.py -- name normalization/matching
(the "Last, First" vs "First Last" mismatch between our corpus and OpenAlex),
the plurality-vote resolution logic, and load_multi_paper_authors()'s
min-papers/no-DOI filtering, against a real temp-file library.sqlite3."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import resolve_author_openalex_ids as raoi  # noqa: E402
import retrieve_papers as rp  # noqa: E402


class TestNormalizeAuthorName(unittest.TestCase):
    def test_flips_last_comma_first(self):
        self.assertEqual(raoi.normalize_author_name("Bartlett, Lucinda"), "lucinda bartlett")

    def test_already_first_last_unchanged_besides_case(self):
        self.assertEqual(raoi.normalize_author_name("Lucinda Bartlett"), "lucinda bartlett")

    def test_strips_accents_and_punctuation(self):
        self.assertEqual(raoi.normalize_author_name("José Pérez-Ruiz"), "jose perez ruiz")

    def test_empty_or_none(self):
        self.assertEqual(raoi.normalize_author_name(""), "")
        self.assertEqual(raoi.normalize_author_name(None), "")

    def test_only_flips_first_comma(self):
        """A "Last, First, Jr." suffix stays attached to First, not split into its own token."""
        self.assertEqual(raoi.normalize_author_name("Smith, John, Jr."), "john jr smith")


class TestNameSimilarity(unittest.TestCase):
    def test_matches_across_name_order(self):
        self.assertGreaterEqual(raoi.name_similarity("Bartlett, Lucinda", "Lucinda Bartlett"), raoi.NAME_MATCH_THRESHOLD)

    def test_different_people_score_low(self):
        self.assertLess(raoi.name_similarity("Jane Doe", "John Smith"), raoi.NAME_MATCH_THRESHOLD)


class TestResolveAuthors(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.rate_limiter = rp.RateLimiter(min_interval=0)
        self.args = Mock(email="you@example.com", timeout=30.0)

    def test_plurality_winner_resolved(self):
        candidates = {1: ("Jane Doe", ["10.1/a", "10.1/b", "10.1/c"])}
        authorships = {
            "10.1/a": [{"openalex_id": "A1", "display_name": "Jane Doe"}],
            "10.1/b": [{"openalex_id": "A1", "display_name": "Jane Doe"}],
            "10.1/c": [{"openalex_id": "A2", "display_name": "Jane Doe"}],
        }
        with patch.object(raoi.rp, "query_openalex_authorships_batch", return_value=authorships):
            resolved = raoi.resolve_authors(self.session, candidates, self.rate_limiter, self.args)
        self.assertIn(1, resolved)
        openalex_id, matched, total = resolved[1]
        self.assertEqual(openalex_id, "A1")
        self.assertEqual(matched, 2)
        self.assertEqual(total, 3)

    def test_tie_leaves_unresolved(self):
        candidates = {1: ("Jane Doe", ["10.1/a", "10.1/b"])}
        authorships = {
            "10.1/a": [{"openalex_id": "A1", "display_name": "Jane Doe"}],
            "10.1/b": [{"openalex_id": "A2", "display_name": "Jane Doe"}],
        }
        with patch.object(raoi.rp, "query_openalex_authorships_batch", return_value=authorships):
            resolved = raoi.resolve_authors(self.session, candidates, self.rate_limiter, self.args)
        self.assertNotIn(1, resolved)

    def test_no_matching_name_leaves_unresolved(self):
        """A different (unrelated) person's authorship on the paper must not
        be attributed to our author just because the DOI matched."""
        candidates = {1: ("Jane Doe", ["10.1/a"])}
        authorships = {"10.1/a": [{"openalex_id": "A1", "display_name": "Someone Else Entirely"}]}
        with patch.object(raoi.rp, "query_openalex_authorships_batch", return_value=authorships):
            resolved = raoi.resolve_authors(self.session, candidates, self.rate_limiter, self.args)
        self.assertEqual(resolved, {})

    def test_rate_limited_stops_but_keeps_partial_results(self):
        candidates = {
            1: ("Jane Doe", ["10.1/a"]),
        }
        with patch.object(raoi.rp, "query_openalex_authorships_batch",
                           side_effect=rp.RateLimited("429")):
            resolved = raoi.resolve_authors(self.session, candidates, self.rate_limiter, self.args)
        self.assertEqual(resolved, {})  # nothing crashed; just nothing resolved this batch


class TestLoadMultiPaperAuthors(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "library.sqlite3"
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT, year INTEGER,
                                  file_path TEXT UNIQUE, extracted_at TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            CREATE TABLE paper_authors (paper_id INTEGER, author_id INTEGER, author_order INTEGER,
                                         PRIMARY KEY (paper_id, author_id));
        """)
        conn.executemany("INSERT INTO papers (id, doi, title, file_path) VALUES (?, ?, ?, ?)", [
            (1, "10.1/a", "T1", "p1.pdf"),
            (2, "10.1/b", "T2", "p2.pdf"),
            (3, None, "T3", "p3.pdf"),
        ])
        conn.executemany("INSERT INTO authors (id, name) VALUES (?, ?)", [
            (1, "Multi Paper Author"), (2, "Single Paper Author"), (3, "No Doi Author"),
        ])
        conn.executemany("INSERT INTO paper_authors (paper_id, author_id, author_order) VALUES (?, ?, 0)", [
            (1, 1), (2, 1),  # author 1: two papers, both with DOI
            (1, 2),          # author 2: one paper
            (3, 3),          # author 3: one paper, no DOI
        ])
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()

    def test_only_authors_meeting_min_papers_with_doi_included(self):
        result = raoi.load_multi_paper_authors(self.conn, min_papers=2)
        self.assertEqual(set(result), {1})
        name, dois = result[1]
        self.assertEqual(name, "Multi Paper Author")
        self.assertEqual(set(dois), {"10.1/a", "10.1/b"})

    def test_no_doi_author_never_included_regardless_of_threshold(self):
        result = raoi.load_multi_paper_authors(self.conn, min_papers=1)
        self.assertNotIn(3, result)


if __name__ == "__main__":
    unittest.main()
