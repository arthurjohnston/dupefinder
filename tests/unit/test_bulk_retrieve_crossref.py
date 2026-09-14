#!/usr/bin/env python3
"""Unit tests for bulk_retrieve_crossref.py -- specifically search_crossref()'s
query-param construction and parse_args()'s --whole-prefix validation/dispatch,
added alongside that flag (see its --help text and todo.md for why it exists:
a keyword-restricted crawl of a --doi-prefix structurally can't reach the
publisher's full catalog, confirmed by a live coverage check against IAEME)."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bulk_retrieve_crossref as brc  # noqa: E402


def _fake_response(items, next_cursor=None):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"message": {"items": items, "next-cursor": next_cursor}}
    return resp


class TestSearchCrossrefKeywordParam(unittest.TestCase):
    """keyword=None (--whole-prefix's mode) must omit query.bibliographic
    entirely rather than sending it as the literal string "None" -- that
    would silently turn into a real (nonsensical) bibliographic search term
    instead of "no restriction"."""

    def setUp(self):
        self.session = Mock()
        self.args = Mock(email="test@example.com")

    def test_keyword_none_omits_query_bibliographic(self):
        with patch.object(brc.rp, "http_get", return_value=_fake_response([])) as mock_get:
            list(brc.search_crossref(self.session, MagicMock(), None, "2020-01-01", "2020-12-31",
                                      10, self.args, doi_prefix="10.34218"))
        params = mock_get.call_args.args[2]
        self.assertNotIn("query.bibliographic", params)
        self.assertIn("prefix:10.34218", params["filter"])

    def test_keyword_set_includes_query_bibliographic(self):
        with patch.object(brc.rp, "http_get", return_value=_fake_response([])) as mock_get:
            list(brc.search_crossref(self.session, MagicMock(), "AI ethics", "2020-01-01", "2020-12-31",
                                      10, self.args, doi_prefix=None))
        params = mock_get.call_args.args[2]
        self.assertEqual(params["query.bibliographic"], "AI ethics")
        self.assertNotIn("prefix:", params["filter"])


class TestParseArgsWholePrefix(unittest.TestCase):
    def _parse(self, argv):
        with patch.object(sys, "argv", ["bulk_retrieve_crossref.py"] + argv):
            return brc.parse_args()

    def test_whole_prefix_requires_doi_prefix(self):
        with self.assertRaises(SystemExit):
            self._parse(["--email", "a@b.com", "--whole-prefix"])

    def test_whole_prefix_incompatible_with_keyword(self):
        with self.assertRaises(SystemExit):
            self._parse(["--email", "a@b.com", "--whole-prefix", "--doi-prefix", "10.34218",
                          "--keyword", "AI ethics"])

    def test_whole_prefix_with_doi_prefix_sets_single_none_keyword(self):
        args = self._parse(["--email", "a@b.com", "--whole-prefix", "--doi-prefix", "10.34218"])
        self.assertEqual(args.keywords, [None])
        self.assertEqual(args.doi_prefixes, ["10.34218"])

    def test_default_without_whole_prefix_unchanged(self):
        args = self._parse(["--email", "a@b.com"])
        self.assertEqual(args.keywords, brc.DEFAULT_KEYWORDS)

    def test_container_title_incompatible_with_keyword(self):
        with self.assertRaises(SystemExit):
            self._parse(["--email", "a@b.com", "--container-title", "Security and Communication Networks",
                          "--keyword", "AI ethics"])

    def test_container_title_incompatible_with_whole_prefix(self):
        with self.assertRaises(SystemExit):
            self._parse(["--email", "a@b.com", "--whole-prefix", "--doi-prefix", "10.1155",
                          "--container-title", "Security and Communication Networks"])

    def test_container_title_sets_single_none_keyword(self):
        args = self._parse(["--email", "a@b.com", "--container-title", "Security and Communication Networks",
                             "--doi-prefix", "10.1155"])
        self.assertEqual(args.keywords, [None])
        self.assertEqual(args.container_titles, ["Security and Communication Networks"])

    def test_container_title_repeatable(self):
        args = self._parse(["--email", "a@b.com", "--container-title", "Journal A",
                             "--container-title", "Journal B"])
        self.assertEqual(args.container_titles, ["Journal A", "Journal B"])


class TestSearchCrossrefContainerTitleParam(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.args = Mock(email="test@example.com")

    def test_container_title_sent_as_query_param(self):
        with patch.object(brc.rp, "http_get", return_value=_fake_response([])) as mock_get:
            list(brc.search_crossref(self.session, MagicMock(), None, "2020-01-01", "2020-12-31",
                                      10, self.args, container_title="Security and Communication Networks"))
        params = mock_get.call_args.args[2]
        self.assertEqual(params["query.container-title"], "Security and Communication Networks")
        self.assertNotIn("query.bibliographic", params)

    def test_no_container_title_param_when_not_given(self):
        with patch.object(brc.rp, "http_get", return_value=_fake_response([])) as mock_get:
            list(brc.search_crossref(self.session, MagicMock(), "AI ethics", "2020-01-01", "2020-12-31",
                                      10, self.args))
        params = mock_get.call_args.args[2]
        self.assertNotIn("query.container-title", params)


class TestFetchCandidateLandingPageFallback(unittest.TestCase):
    """fetch_candidate() must download via download_with_landing_page_fallback()
    (not plain download_pdf()) -- added 2026-08-25 after a live
    bulk_retrieve_author_works.py run landed 721/~3,700 candidates in
    oa_url_not_pdf, the exact landing-page case that fallback exists for.
    Both callers of this shared function benefit (see its own docstring)."""

    def setUp(self):
        self.session = Mock()
        self.store = Mock()
        self.store.get.return_value = None
        self.args = Mock(outdir=Path("/tmp/fake"), max_retries=4, timeout=30.0)
        self.paper = {"title": "A Paper", "authors": ["Jane Doe"], "year": 2020, "doi": "10.1/a"}
        self.openalex_hits = {"10.1/a": {"oa_status": "gold", "pdf_url": "http://example.com/landing"}}

    def test_uses_landing_page_fallback_and_records_worked_url(self):
        with patch.object(brc.rp, "download_with_landing_page_fallback",
                           return_value=(True, "http://example.com/real.pdf")) as mock_dl:
            result = brc.fetch_candidate(self.session, self.paper, self.store, MagicMock(), MagicMock(),
                                          self.args, openalex_hits=self.openalex_hits)
        self.assertEqual(result, "downloaded")
        mock_dl.assert_called_once()
        self.assertEqual(mock_dl.call_args.args[1], "http://example.com/landing")
        upsert_kwargs = self.store.upsert.call_args.kwargs
        self.assertEqual(upsert_kwargs["pdf_url"], "http://example.com/real.pdf")
        self.assertEqual(upsert_kwargs["status"], "downloaded")

    def test_failure_still_marks_oa_url_not_pdf(self):
        with patch.object(brc.rp, "download_with_landing_page_fallback",
                           return_value=(False, "http://example.com/landing")):
            result = brc.fetch_candidate(self.session, self.paper, self.store, MagicMock(), MagicMock(),
                                          self.args, openalex_hits=self.openalex_hits)
        self.assertEqual(result, "oa_url_not_pdf")


class TestLoadKnownDois(unittest.TestCase):
    """load_known_dois() must key off file_path (not the literal status
    string 'downloaded') so a filter_low_relevance_papers.py exclusion
    (status 'excluded_crank'/'excluded_offtopic', file_path still set --
    see that script's docstring) still counts as "already handled" here.
    Otherwise every bulk_retrieve_*.py script sharing this function would
    just re-download the exact paper that was deliberately pulled out."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "state.sqlite3"
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE papers (key TEXT PRIMARY KEY, title TEXT, authors TEXT, year INTEGER,
                         doi TEXT, status TEXT, oa_status TEXT, pdf_url TEXT, file_path TEXT, error TEXT,
                         updated_at TEXT)""")
        conn.executemany("INSERT INTO papers (key, doi, status, file_path) VALUES (?, ?, ?, ?)", [
            ("k1", "10.1/downloaded", "downloaded", "papers/a.pdf"),
            ("k2", "10.1/crank", "excluded_crank", "papers_excluded/crank/b.pdf"),
            ("k3", "10.1/offtopic", "excluded_offtopic", "papers_excluded/offtopic/c.pdf"),
            ("k4", "10.1/failed", "no_oa", None),
        ])
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()

    def test_downloaded_and_excluded_rows_both_counted_known(self):
        known = brc.load_known_dois(self.conn)
        self.assertEqual(known, {"10.1/downloaded", "10.1/crank", "10.1/offtopic"})

    def test_failed_with_no_file_path_not_counted_known(self):
        """A DOI that only ever failed for a real retrieval reason (no file
        ever obtained) must still be retried on a later run, unchanged."""
        known = brc.load_known_dois(self.conn)
        self.assertNotIn("10.1/failed", known)


if __name__ == "__main__":
    unittest.main()
