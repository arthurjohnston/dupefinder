#!/usr/bin/env python3
"""Unit tests for bulk_retrieve_core.py -- parse_core_item()'s filtering,
search_core()'s pagination/param construction, and fetch_candidate()'s
state.sqlite3-based resumability check (see that function's docstring for
why it differs from bulk_retrieve_crossref.py/bulk_retrieve_theses.py's
harvest-time known-DOI filter: most CORE candidates have no DOI to dedup on,
which is the entire reason this script exists)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bulk_retrieve_core as brcore  # noqa: E402


def _fake_response(body):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


class TestParseCoreItem(unittest.TestCase):
    def test_valid_item_with_no_doi(self):
        item = {
            "title": "A Thesis With No DOI At All",
            "authors": [{"name": "Jane Doe"}, {"name": "John Smith"}],
            "yearPublished": 2019,
            "doi": None,
            "downloadUrl": "https://core.ac.uk/download/12345.pdf",
            "id": 12345,
        }
        paper = brcore.parse_core_item(item)
        self.assertIsNotNone(paper)
        self.assertIsNone(paper["doi"])
        self.assertEqual(paper["title"], "A Thesis With No DOI At All")
        self.assertEqual(paper["authors"], ["Jane Doe", "John Smith"])
        self.assertEqual(paper["core_id"], 12345)

    def test_missing_title_returns_none(self):
        item = {"title": "", "downloadUrl": "https://core.ac.uk/download/1.pdf", "id": 1}
        self.assertIsNone(brcore.parse_core_item(item))

    def test_missing_download_url_returns_none(self):
        """A metadata-only (closed-access) CORE record -- nothing this project can ever fetch."""
        item = {"title": "Closed Access Paper", "downloadUrl": None, "id": 2}
        self.assertIsNone(brcore.parse_core_item(item))

    def test_doi_passed_through_when_present(self):
        item = {"title": "A Paper With A DOI", "downloadUrl": "https://core.ac.uk/download/3.pdf",
                 "id": 3, "doi": "10.1234/example"}
        paper = brcore.parse_core_item(item)
        self.assertEqual(paper["doi"], "10.1234/example")


class TestSearchCore(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.args = Mock(max_retries=4, timeout=30.0)

    def test_sends_query_and_pagination_params(self):
        with patch.object(brcore.rp, "http_get", return_value=_fake_response({"results": []})) as mock_get:
            list(brcore.search_core(self.session, MagicMock(), "computer ethics", 500, self.args))
        params = mock_get.call_args.args[2]
        self.assertEqual(params["q"], "computer ethics")
        self.assertEqual(params["offset"], 0)

    def test_stops_when_page_shorter_than_page_size(self):
        item = {"title": "T", "downloadUrl": "https://core.ac.uk/download/1.pdf", "id": 1}
        short_page = _fake_response({"results": [item]})  # 1 result, well under the 100-per-page size
        with patch.object(brcore.rp, "http_get", return_value=short_page) as mock_get:
            results = list(brcore.search_core(self.session, MagicMock(), "kw", 1000, self.args))
        self.assertEqual(mock_get.call_count, 1)  # didn't try a second page
        self.assertEqual(len(results), 1)

    def test_stops_at_max_results(self):
        item = {"title": "T", "downloadUrl": "https://core.ac.uk/download/1.pdf", "id": 1}
        full_page = _fake_response({"results": [item] * 100})
        with patch.object(brcore.rp, "http_get", return_value=full_page):
            results = list(brcore.search_core(self.session, MagicMock(), "kw", 150, self.args))
        # First page yields 100 (all valid, same core_id -- fine, parse_core_item doesn't dedup),
        # second page requested with offset=100 hits the same mock again (100 more) but the loop's
        # own `while offset < max_results` check stops issuing a third page beyond 150.
        self.assertLessEqual(len(results), 200)


class TestFetchCandidateResumability(unittest.TestCase):
    """fetch_candidate() must skip a candidate whose key already has a
    downloaded, still-on-disk file in state.sqlite3 -- without ever calling
    download_pdf() for it -- and must proceed to download otherwise."""

    def setUp(self):
        self.session = Mock()
        self.download_limiter = MagicMock()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.args = Mock(outdir=self.tmpdir, max_retries=4, timeout=30.0)
        self.paper = {"title": "A Thesis With No DOI", "authors": ["Jane Doe"], "year": 2020,
                      "doi": None, "download_url": "https://core.ac.uk/download/99.pdf", "core_id": 99}

    def test_skips_when_already_downloaded_and_file_exists(self):
        existing_file = self.tmpdir / "already-here.pdf"
        existing_file.write_bytes(b"%PDF-1.4 fake")
        store = Mock()
        store.get.return_value = {"status": "downloaded", "file_path": str(existing_file)}
        with patch.object(brcore.rp, "download_pdf") as mock_download:
            result = brcore.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "skipped")
        mock_download.assert_not_called()

    def test_downloads_when_not_already_present(self):
        store = Mock()
        store.get.return_value = None
        with patch.object(brcore.rp, "download_pdf", return_value=True) as mock_download:
            result = brcore.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "downloaded")
        mock_download.assert_called_once()
        store.upsert.assert_called_once()
        self.assertEqual(store.upsert.call_args.kwargs["status"], "downloaded")

    def test_skips_excluded_crank_unconditionally_even_if_file_missing(self):
        """A row filter_low_relevance_papers.py already excluded must never be
        re-fetched, even when its (relocated) file no longer exists -- unlike
        the ordinary status='downloaded'-but-file-gone case, which IS a real
        recovery and does redownload (see the next test down)."""
        store = Mock()
        store.get.return_value = {"status": "excluded_crank", "file_path": str(self.tmpdir / "gone.pdf")}
        with patch.object(brcore.rp, "download_pdf") as mock_download:
            result = brcore.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "skipped")
        mock_download.assert_not_called()

    def test_skips_excluded_offtopic_unconditionally(self):
        store = Mock()
        store.get.return_value = {"status": "excluded_offtopic", "file_path": None}
        with patch.object(brcore.rp, "download_pdf") as mock_download:
            result = brcore.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "skipped")
        mock_download.assert_not_called()

    def test_redownloads_when_stored_file_path_missing_on_disk(self):
        """A downloaded record whose file no longer exists on disk (deleted, moved) must not be
        treated as "already have it" -- same discipline as retrieve_papers.py's process_paper()."""
        store = Mock()
        store.get.return_value = {"status": "downloaded", "file_path": str(self.tmpdir / "gone.pdf")}
        with patch.object(brcore.rp, "download_pdf", return_value=True) as mock_download:
            result = brcore.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "downloaded")
        mock_download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
