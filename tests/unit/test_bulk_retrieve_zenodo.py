#!/usr/bin/env python3
"""Unit tests for bulk_retrieve_zenodo.py -- parse_zenodo_item()'s filtering
(open-access + has-a-PDF-file, unlike bulk_retrieve_core.py's simpler
has-a-downloadUrl check, since Zenodo hosts datasets/software alongside real
papers under one record type), search_zenodo()'s pagination, and
fetch_candidate()'s resumability check (same shape as bulk_retrieve_core.py's,
even though dedup itself happens at harvest time here via known DOIs -- see
module docstring for why Zenodo's DOI reliability makes that safe unlike CORE)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bulk_retrieve_zenodo as bz  # noqa: E402


def _fake_response(body):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


def _item(title="A Paper", access_right="open", files=None, doi="10.5281/zenodo.123",
          creators=None, publication_date="2022-10-04", item_id=123, conceptrecid="122"):
    return {
        "id": item_id,
        "doi": doi,
        "conceptrecid": conceptrecid,
        "metadata": {
            "title": title,
            "access_right": access_right,
            "creators": creators if creators is not None else [{"name": "Jane Doe"}],
            "publication_date": publication_date,
        },
        "files": files if files is not None else [
            {"key": "paper.pdf", "links": {"self": "https://zenodo.org/api/records/123/files/paper.pdf/content"}}
        ],
    }


class TestParseZenodoItem(unittest.TestCase):
    def test_valid_open_access_item_with_pdf(self):
        paper = bz.parse_zenodo_item(_item())
        self.assertIsNotNone(paper)
        self.assertEqual(paper["title"], "A Paper")
        self.assertEqual(paper["authors"], ["Jane Doe"])
        self.assertEqual(paper["year"], 2022)
        self.assertEqual(paper["doi"], "10.5281/zenodo.123")
        self.assertEqual(paper["download_url"], "https://zenodo.org/api/records/123/files/paper.pdf/content")
        self.assertEqual(paper["zenodo_id"], 123)
        self.assertEqual(paper["conceptrecid"], "122")

    def test_missing_conceptrecid_falls_back_to_doi(self):
        """Regression test for a real, already-happened bug (2026-08-30): Zenodo mints a
        distinct doi/id per VERSION of one deposit, and its search index returns each version
        as a separate hit -- DOI-only dedup can't recognize two different real DOIs as the same
        underlying work. conceptrecid is the stable id shared across versions; this just checks
        the fallback when it's ever absent (not observed live, but cheap to guard)."""
        paper = bz.parse_zenodo_item(_item(conceptrecid=None))
        self.assertEqual(paper["conceptrecid"], paper["doi"])

    def test_missing_title_returns_none(self):
        item = _item(title="")
        self.assertIsNone(bz.parse_zenodo_item(item))

    def test_closed_access_returns_none(self):
        item = _item(access_right="closed")
        self.assertIsNone(bz.parse_zenodo_item(item))

    def test_restricted_access_returns_none(self):
        item = _item(access_right="restricted")
        self.assertIsNone(bz.parse_zenodo_item(item))

    def test_no_files_returns_none(self):
        item = _item(files=[])
        self.assertIsNone(bz.parse_zenodo_item(item))

    def test_non_pdf_files_only_returns_none(self):
        """A dataset deposit (zip/csv, no PDF) -- nothing this project can extract text from."""
        item = _item(files=[{"key": "data.zip", "links": {"self": "https://zenodo.org/.../data.zip/content"}}])
        self.assertIsNone(bz.parse_zenodo_item(item))

    def test_pdf_file_picked_among_multiple_files(self):
        item = _item(files=[
            {"key": "supplementary.zip", "links": {"self": "https://zenodo.org/.../supplementary.zip/content"}},
            {"key": "manuscript.PDF", "links": {"self": "https://zenodo.org/.../manuscript.PDF/content"}},
        ])
        paper = bz.parse_zenodo_item(item)
        self.assertIsNotNone(paper)
        self.assertEqual(paper["download_url"], "https://zenodo.org/.../manuscript.PDF/content")

    def test_missing_doi_still_parses(self):
        item = _item(doi=None)
        paper = bz.parse_zenodo_item(item)
        self.assertIsNotNone(paper)
        self.assertIsNone(paper["doi"])

    def test_unparseable_publication_date_gives_none_year(self):
        item = _item(publication_date="")
        paper = bz.parse_zenodo_item(item)
        self.assertIsNotNone(paper)
        self.assertIsNone(paper["year"])


class TestSearchZenodo(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.args = Mock(max_retries=4, timeout=30.0)

    def test_sends_query_and_pagination_params(self):
        body = {"hits": {"hits": []}}
        with patch.object(bz.rp, "http_get", return_value=_fake_response(body)) as mock_get:
            list(bz.search_zenodo(self.session, MagicMock(), "cultural anthropology", 500, 25,
                                   self.args, "publication"))
        params = mock_get.call_args.args[2]
        self.assertEqual(params["q"], "cultural anthropology")
        self.assertEqual(params["page"], 1)
        self.assertEqual(params["type"], "publication")

    def test_resource_type_omitted_when_falsy(self):
        body = {"hits": {"hits": []}}
        with patch.object(bz.rp, "http_get", return_value=_fake_response(body)) as mock_get:
            list(bz.search_zenodo(self.session, MagicMock(), "kw", 500, 25, self.args, ""))
        params = mock_get.call_args.args[2]
        self.assertNotIn("type", params)

    def test_stops_when_page_shorter_than_page_size(self):
        body = {"hits": {"hits": [_item()]}}  # 1 result, well under page_size
        with patch.object(bz.rp, "http_get", return_value=_fake_response(body)) as mock_get:
            results = list(bz.search_zenodo(self.session, MagicMock(), "kw", 1000, 25, self.args, ""))
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(len(results), 1)

    def test_empty_first_page_yields_nothing(self):
        body = {"hits": {"hits": []}}
        with patch.object(bz.rp, "http_get", return_value=_fake_response(body)):
            results = list(bz.search_zenodo(self.session, MagicMock(), "kw", 1000, 25, self.args, ""))
        self.assertEqual(results, [])


class TestFetchCandidateResumability(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.download_limiter = MagicMock()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.args = Mock(outdir=self.tmpdir, max_retries=4, timeout=30.0)
        self.paper = {"title": "A Zenodo Paper", "authors": ["Jane Doe"], "year": 2022,
                      "doi": "10.5281/zenodo.99", "download_url": "https://zenodo.org/.../99/content",
                      "zenodo_id": 99}

    def test_skips_when_already_downloaded_and_file_exists(self):
        existing_file = self.tmpdir / "already-here.pdf"
        existing_file.write_bytes(b"%PDF-1.4 fake")
        store = Mock()
        store.get.return_value = {"status": "downloaded", "file_path": str(existing_file)}
        with patch.object(bz.rp, "download_pdf") as mock_download:
            result = bz.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "skipped")
        mock_download.assert_not_called()

    def test_downloads_when_not_already_present(self):
        store = Mock()
        store.get.return_value = None
        with patch.object(bz.rp, "download_pdf", return_value=True) as mock_download:
            result = bz.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "downloaded")
        mock_download.assert_called_once()
        self.assertEqual(store.upsert.call_args.kwargs["status"], "downloaded")

    def test_skips_excluded_crank_unconditionally(self):
        store = Mock()
        store.get.return_value = {"status": "excluded_crank", "file_path": None}
        with patch.object(bz.rp, "download_pdf") as mock_download:
            result = bz.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "skipped")
        mock_download.assert_not_called()

    def test_redownloads_when_stored_file_path_missing_on_disk(self):
        store = Mock()
        store.get.return_value = {"status": "downloaded", "file_path": str(self.tmpdir / "gone.pdf")}
        with patch.object(bz.rp, "download_pdf", return_value=True) as mock_download:
            result = bz.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "downloaded")
        mock_download.assert_called_once()

    def test_download_error_recorded_as_error_status(self):
        store = Mock()
        store.get.return_value = None
        with patch.object(bz.rp, "download_pdf", side_effect=bz.rp.RetrievalError("network error")):
            result = bz.fetch_candidate(self.session, self.paper, store, self.download_limiter, self.args)
        self.assertEqual(result, "error")
        self.assertEqual(store.upsert.call_args.kwargs["status"], "error")


if __name__ == "__main__":
    unittest.main()
