#!/usr/bin/env python3
"""Unit tests for bulk_retrieve_author_works.py -- parse_author_work()'s
field extraction/no-DOI filtering, search_author_works()'s cursor pagination,
and load_resolved_authors()'s join against resolve_author_openalex_ids.py's
author_openalex_ids table."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bulk_retrieve_author_works as braw  # noqa: E402


def _fake_response(results, next_cursor=None):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"results": results, "meta": {"next_cursor": next_cursor}}
    return resp


class TestParseAuthorWork(unittest.TestCase):
    def test_extracts_title_authors_year_doi_oa(self):
        work = {
            "doi": "https://doi.org/10.1/a", "title": "A Real Paper",
            "publication_year": 2021,
            "authorships": [{"author": {"display_name": "Jane Doe"}}, {"author": {"display_name": "John Smith"}}],
            "best_oa_location": {"pdf_url": "http://example.com/a.pdf"},
            "open_access": {"oa_status": "gold"},
        }
        paper = braw.parse_author_work(work)
        self.assertEqual(paper["title"], "A Real Paper")
        self.assertEqual(paper["doi"], "10.1/a")
        self.assertEqual(paper["year"], 2021)
        self.assertEqual(paper["authors"], ["Jane Doe", "John Smith"])
        self.assertEqual(paper["pdf_url"], "http://example.com/a.pdf")
        self.assertEqual(paper["oa_status"], "gold")

    def test_no_doi_returns_none(self):
        """CORE (bulk_retrieve_core.py) is the dedicated no-DOI channel -- see module docstring."""
        work = {"doi": None, "title": "No DOI Work"}
        self.assertIsNone(braw.parse_author_work(work))

    def test_no_title_returns_none(self):
        work = {"doi": "https://doi.org/10.1/b", "title": "", "display_name": ""}
        self.assertIsNone(braw.parse_author_work(work))

    def test_falls_back_to_open_access_oa_url_without_best_location(self):
        work = {"doi": "https://doi.org/10.1/c", "title": "T", "best_oa_location": None,
                "open_access": {"oa_status": "green", "oa_url": "http://example.com/c.pdf"}}
        paper = braw.parse_author_work(work)
        self.assertEqual(paper["pdf_url"], "http://example.com/c.pdf")

    def test_no_oa_location_at_all_leaves_pdf_url_none(self):
        work = {"doi": "https://doi.org/10.1/d", "title": "T", "best_oa_location": None, "open_access": {}}
        paper = braw.parse_author_work(work)
        self.assertIsNone(paper["pdf_url"])


class TestSearchAuthorWorks(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.args = Mock(email="test@example.com", max_retries=4, timeout=30.0)

    def test_paginates_via_next_cursor(self):
        page1 = _fake_response([{"id": 1}], next_cursor="page2")
        page2 = _fake_response([{"id": 2}], next_cursor=None)
        with patch.object(braw.rp, "http_get", side_effect=[page1, page2]) as mock_get:
            results = list(braw.search_author_works(self.session, MagicMock(), "A123", 100, self.args))
        self.assertEqual(len(results), 2)
        self.assertEqual(mock_get.call_count, 2)

    def test_stops_at_max_results(self):
        page = _fake_response([{"id": 1}, {"id": 2}, {"id": 3}], next_cursor="more")
        with patch.object(braw.rp, "http_get", return_value=page):
            results = list(braw.search_author_works(self.session, MagicMock(), "A123", 2, self.args))
        self.assertEqual(len(results), 2)

    def test_stops_on_empty_page(self):
        page = _fake_response([], next_cursor="ignored")
        with patch.object(braw.rp, "http_get", return_value=page) as mock_get:
            results = list(braw.search_author_works(self.session, MagicMock(), "A123", 100, self.args))
        self.assertEqual(results, [])
        self.assertEqual(mock_get.call_count, 1)

    def test_non_200_stops_this_author(self):
        resp = Mock(status_code=500)
        with patch.object(braw.rp, "http_get", return_value=resp) as mock_get:
            results = list(braw.search_author_works(self.session, MagicMock(), "A123", 100, self.args))
        self.assertEqual(results, [])
        self.assertEqual(mock_get.call_count, 1)

    def test_sends_author_id_filter(self):
        page = _fake_response([])
        with patch.object(braw.rp, "http_get", return_value=page) as mock_get:
            list(braw.search_author_works(self.session, MagicMock(), "A999", 100, self.args))
        params = mock_get.call_args.args[2]
        self.assertEqual(params["filter"], "author.id:A999")


class TestLoadResolvedAuthors(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "library.sqlite3"
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE author_openalex_ids (author_id INTEGER PRIMARY KEY, openalex_id TEXT,
                                               matched_papers INTEGER, total_papers INTEGER, resolved_at TEXT);
            CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT);
            CREATE TABLE paper_authors (paper_id INTEGER, author_id INTEGER);
        """)
        conn.executemany("INSERT INTO authors (id, name) VALUES (?, ?)",
                          [(1, "Jane Doe"), (2, "Unresolved Author"), (3, "Two Paper Author"),
                           (4, "Front Identity"), (5, "Mostly Real Scholar")])
        conn.executemany(
            "INSERT INTO author_openalex_ids (author_id, openalex_id, matched_papers, total_papers, resolved_at) "
            "VALUES (?, ?, ?, ?, 'now')",
            [(1, "A123", 3, 3), (3, "A456", 2, 2), (4, "A789", 3, 3), (5, "A999", 5, 5)],
        )
        # Jane Doe: 3 papers, none under a low-scrutiny prefix -- a real author.
        conn.executemany("INSERT INTO papers (id, doi) VALUES (?, ?)",
                          [(101, "10.1/a"), (102, "10.1/b"), (103, "10.1/c"),
                           # Two Paper Author: same, real.
                           (104, "10.1/d"), (105, "10.1/e"),
                           # Front Identity: all 3 papers under IAEME/Pearl Blue -- a paper-mill identity.
                           (106, "10.34218/x"), (107, "10.63282/y"), (108, "10.34218/z"),
                           # Mostly Real Scholar: 1 of 5 under a low-scrutiny prefix -- below threshold, kept.
                           (109, "10.1/f"), (110, "10.1/g"), (111, "10.1/h"), (112, "10.1/i"), (113, "10.34218/j")])
        conn.executemany("INSERT INTO paper_authors (paper_id, author_id) VALUES (?, ?)",
                          [(101, 1), (102, 1), (103, 1), (104, 3), (105, 3),
                           (106, 4), (107, 4), (108, 4),
                           (109, 5), (110, 5), (111, 5), (112, 5), (113, 5)])
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()

    def test_only_resolved_authors_returned(self):
        result = braw.load_resolved_authors(self.conn, min_total_papers=1)
        self.assertEqual(result, {1: ("Jane Doe", "A123"), 3: ("Two Paper Author", "A456"),
                                   5: ("Mostly Real Scholar", "A999")})

    def test_min_total_papers_filters_out_authors_below_threshold(self):
        """The default --min-papers=3 scope guard (module docstring's guard #1) --
        an author resolved with only 2 corpus papers is excluded at min_total_papers=3."""
        result = braw.load_resolved_authors(self.conn, min_total_papers=3)
        self.assertEqual(result, {1: ("Jane Doe", "A123"), 5: ("Mostly Real Scholar", "A999")})

    def test_paper_mill_identity_excluded_by_default(self):
        """Module docstring's scope-guard #3 -- an author whose corpus papers are 100% under
        IAEME/Pearl Blue is excluded even though they clear min_total_papers, while a real
        author with only one low-scrutiny paper (below PAPER_MILL_FRACTION_THRESHOLD) is kept."""
        result = braw.load_resolved_authors(self.conn, min_total_papers=1)
        self.assertNotIn(4, result)
        self.assertIn(5, result)

    def test_no_paper_mill_filter_restores_front_identity(self):
        result = braw.load_resolved_authors(self.conn, min_total_papers=1, filter_paper_mill=False)
        self.assertIn(4, result)
        self.assertEqual(result[4], ("Front Identity", "A789"))

    def test_paper_mill_fractions_computes_per_author_ratio(self):
        fractions = braw.paper_mill_fractions(self.conn, [1, 4, 5])
        self.assertEqual(fractions[1], 0.0)
        self.assertEqual(fractions[4], 1.0)
        self.assertAlmostEqual(fractions[5], 0.2)


if __name__ == "__main__":
    unittest.main()
