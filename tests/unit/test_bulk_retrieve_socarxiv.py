#!/usr/bin/env python3
"""Unit tests for bulk_retrieve_socarxiv.py -- parse_socarxiv_item()'s
filtering and contributor-embed extraction, search_socarxiv()'s
links.next-based pagination, and fetch_candidate()'s two-hop resolution
(file-info lookup THEN download -- unlike CORE/Zenodo's single-hop shape,
see module docstring for why)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bulk_retrieve_socarxiv as bs  # noqa: E402


def _fake_response(body, status_code=200):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _item(title="A Preprint", preprint_id="abc12_v1", has_primary_file=True,
          contributor_names=("Jane Doe", "John Smith"), date_published="2022-10-04",
          preprint_doi="https://doi.org/10.31235/osf.io/abc12_v1"):
    relationships = {}
    if has_primary_file:
        relationships["primary_file"] = {
            "links": {"related": {"href": f"https://api.osf.io/v2/files/{preprint_id}file/"}}
        }
    contributors_data = [
        {"embeds": {"users": {"data": {"attributes": {"full_name": name}}}}}
        for name in contributor_names
    ]
    return {
        "id": preprint_id,
        "attributes": {"title": title, "date_published": date_published, "doi": None},
        "relationships": relationships,
        "embeds": {"contributors": {"data": contributors_data}},
        "links": {"preprint_doi": preprint_doi} if preprint_doi else {},
    }


class TestParseSocarxivItem(unittest.TestCase):
    def test_valid_item(self):
        paper = bs.parse_socarxiv_item(_item())
        self.assertIsNotNone(paper)
        self.assertEqual(paper["title"], "A Preprint")
        self.assertEqual(paper["authors"], ["Jane Doe", "John Smith"])
        self.assertEqual(paper["year"], 2022)
        self.assertEqual(paper["doi"], "10.31235/osf.io/abc12_v1")
        self.assertEqual(paper["preprint_id"], "abc12_v1")
        self.assertIn("abc12_v1", paper["file_info_url"])

    def test_missing_title_returns_none(self):
        item = _item(title="")
        self.assertIsNone(bs.parse_socarxiv_item(item))

    def test_no_primary_file_relationship_returns_none(self):
        item = _item(has_primary_file=False)
        self.assertIsNone(bs.parse_socarxiv_item(item))

    def test_no_contributors_gives_empty_authors_not_a_crash(self):
        item = _item(contributor_names=())
        paper = bs.parse_socarxiv_item(item)
        self.assertIsNotNone(paper)
        self.assertEqual(paper["authors"], [])

    def test_missing_doi_link_gives_none_doi(self):
        item = _item(preprint_doi=None)
        paper = bs.parse_socarxiv_item(item)
        self.assertIsNotNone(paper)
        self.assertIsNone(paper["doi"])

    def test_unparseable_date_gives_none_year(self):
        item = _item(date_published="")
        paper = bs.parse_socarxiv_item(item)
        self.assertIsNotNone(paper)
        self.assertIsNone(paper["year"])


class TestSearchSocarxiv(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.args = Mock(max_retries=4, timeout=30.0)

    def test_sends_query_provider_and_embed_params(self):
        body = {"data": [], "links": {}}
        with patch.object(bs.rp, "http_get", return_value=_fake_response(body)) as mock_get:
            list(bs.search_socarxiv(self.session, MagicMock(), "cultural anthropology", 500, self.args))
        params = mock_get.call_args.args[2]
        self.assertEqual(params["q"], "cultural anthropology")
        self.assertEqual(params["filter[provider]"], "socarxiv")
        self.assertEqual(params["embed"], "contributors")

    def test_follows_links_next(self):
        page1 = _fake_response({"data": [_item(preprint_id="p1")],
                                 "links": {"next": "https://api.osf.io/v2/preprints/?page=2"}})
        page2 = _fake_response({"data": [_item(preprint_id="p2")], "links": {}})
        with patch.object(bs.rp, "http_get", side_effect=[page1, page2]) as mock_get:
            results = list(bs.search_socarxiv(self.session, MagicMock(), "kw", 500, self.args))
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[1].args[1], "https://api.osf.io/v2/preprints/?page=2")
        self.assertEqual(len(results), 2)

    def test_no_next_link_stops_pagination(self):
        body = {"data": [_item()], "links": {}}
        with patch.object(bs.rp, "http_get", return_value=_fake_response(body)) as mock_get:
            list(bs.search_socarxiv(self.session, MagicMock(), "kw", 500, self.args))
        self.assertEqual(mock_get.call_count, 1)

    def test_empty_page_stops_pagination(self):
        body = {"data": [], "links": {"next": "https://api.osf.io/v2/preprints/?page=2"}}
        with patch.object(bs.rp, "http_get", return_value=_fake_response(body)) as mock_get:
            results = list(bs.search_socarxiv(self.session, MagicMock(), "kw", 500, self.args))
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(results, [])


class TestFetchCandidate(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.osf_limiter = MagicMock()
        self.download_limiter = MagicMock()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.args = Mock(outdir=self.tmpdir, max_retries=4, timeout=30.0)
        self.paper = {"title": "A Preprint", "authors": ["Jane Doe"], "year": 2022,
                      "doi": "10.31235/osf.io/abc12_v1", "file_info_url": "https://api.osf.io/v2/files/x/",
                      "preprint_id": "abc12_v1"}

    def test_skips_when_already_downloaded_and_file_exists(self):
        existing_file = self.tmpdir / "already-here.pdf"
        existing_file.write_bytes(b"%PDF-1.4 fake")
        store = Mock()
        store.get.return_value = {"status": "downloaded", "file_path": str(existing_file)}
        with patch.object(bs.rp, "http_get") as mock_http, patch.object(bs.rp, "download_pdf") as mock_dl:
            result = bs.fetch_candidate(self.session, self.paper, store, self.osf_limiter,
                                         self.download_limiter, self.args)
        self.assertEqual(result, "skipped")
        mock_http.assert_not_called()  # never even resolves the file-info URL
        mock_dl.assert_not_called()

    def test_resolves_file_info_then_downloads(self):
        store = Mock()
        store.get.return_value = None
        file_info_resp = _fake_response({"data": {"links": {"download": "https://osf.io/download/xyz/"}}})
        with patch.object(bs.rp, "http_get", return_value=file_info_resp) as mock_http, \
             patch.object(bs.rp, "download_pdf", return_value=True) as mock_dl:
            result = bs.fetch_candidate(self.session, self.paper, store, self.osf_limiter,
                                         self.download_limiter, self.args)
        self.assertEqual(result, "downloaded")
        mock_http.assert_called_once()
        mock_dl.assert_called_once_with(self.session, "https://osf.io/download/xyz/", mock_dl.call_args.args[2],
                                         self.download_limiter, self.args, bs.logger)
        self.assertEqual(store.upsert.call_args.kwargs["status"], "downloaded")

    def test_file_info_lookup_failure_recorded_as_error(self):
        store = Mock()
        store.get.return_value = None
        with patch.object(bs.rp, "http_get", return_value=_fake_response({}, status_code=404)), \
             patch.object(bs.rp, "download_pdf") as mock_dl:
            result = bs.fetch_candidate(self.session, self.paper, store, self.osf_limiter,
                                         self.download_limiter, self.args)
        self.assertEqual(result, "error")
        mock_dl.assert_not_called()
        self.assertEqual(store.upsert.call_args.kwargs["status"], "error")

    def test_missing_download_link_recorded_as_error(self):
        store = Mock()
        store.get.return_value = None
        file_info_resp = _fake_response({"data": {"links": {}}})  # no "download" key
        with patch.object(bs.rp, "http_get", return_value=file_info_resp), \
             patch.object(bs.rp, "download_pdf") as mock_dl:
            result = bs.fetch_candidate(self.session, self.paper, store, self.osf_limiter,
                                         self.download_limiter, self.args)
        self.assertEqual(result, "error")
        mock_dl.assert_not_called()

    def test_non_pdf_primary_file_becomes_oa_url_not_pdf(self):
        """The .docx-primary-file case from the module docstring: download_pdf()'s own
        magic-byte sniffing returns False, not an exception."""
        store = Mock()
        store.get.return_value = None
        file_info_resp = _fake_response({"data": {"links": {"download": "https://osf.io/download/xyz/"}}})
        with patch.object(bs.rp, "http_get", return_value=file_info_resp), \
             patch.object(bs.rp, "download_pdf", return_value=False):
            result = bs.fetch_candidate(self.session, self.paper, store, self.osf_limiter,
                                         self.download_limiter, self.args)
        self.assertEqual(result, "oa_url_not_pdf")


if __name__ == "__main__":
    unittest.main()
