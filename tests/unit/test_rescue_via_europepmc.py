#!/usr/bin/env python3
"""Unit tests for rescue_via_europepmc.py -- query_europepmc_batch()'s
fullTextUrlList parsing and load_rescue_candidates()'s status/doi scoping."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import rescue_via_europepmc as rve  # noqa: E402


def _fake_response(body):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


class TestQueryEuropepmcBatch(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.args = Mock(max_retries=4, timeout=30.0)

    def test_parses_oa_pdf_url(self):
        body = {"resultList": {"result": [{
            "doi": "10.1155/2019/1258782",
            "fullTextUrlList": {"fullTextUrl": [
                {"availability": "Subscription required", "availabilityCode": "S", "documentStyle": "doi",
                 "url": "https://doi.org/10.1155/2019/1258782"},
                {"availability": "Open access", "availabilityCode": "OA", "documentStyle": "html",
                 "url": "https://europepmc.org/articles/PMC6942739"},
                {"availability": "Open access", "availabilityCode": "OA", "documentStyle": "pdf",
                 "url": "https://europepmc.org/articles/PMC6942739?pdf=render"},
            ]},
        }]}}
        with patch.object(rve.rp, "http_get", return_value=_fake_response(body)):
            result = rve.query_europepmc_batch(self.session, ["10.1155/2019/1258782"], Mock(), self.args)
        self.assertEqual(result, {"10.1155/2019/1258782": "https://europepmc.org/articles/PMC6942739?pdf=render"})

    def test_no_oa_pdf_entry_is_absent(self):
        body = {"resultList": {"result": [{
            "doi": "10.1155/2020/1111111",
            "fullTextUrlList": {"fullTextUrl": [
                {"availability": "Subscription required", "availabilityCode": "S", "documentStyle": "doi",
                 "url": "https://doi.org/10.1155/2020/1111111"},
            ]},
        }]}}
        with patch.object(rve.rp, "http_get", return_value=_fake_response(body)):
            result = rve.query_europepmc_batch(self.session, ["10.1155/2020/1111111"], Mock(), self.args)
        self.assertEqual(result, {})

    def test_doi_not_in_europepmc_is_absent(self):
        body = {"resultList": {"result": []}}
        with patch.object(rve.rp, "http_get", return_value=_fake_response(body)):
            result = rve.query_europepmc_batch(self.session, ["10.1155/2099/9999999"], Mock(), self.args)
        self.assertEqual(result, {})

    def test_non_200_degrades_to_empty(self):
        resp = Mock(status_code=500)
        with patch.object(rve.rp, "http_get", return_value=resp):
            result = rve.query_europepmc_batch(self.session, ["10.1155/2019/1258782"], Mock(), self.args)
        self.assertEqual(result, {})

    def test_retrieval_error_degrades_to_empty(self):
        with patch.object(rve.rp, "http_get", side_effect=rve.rp.RetrievalError("network down")):
            result = rve.query_europepmc_batch(self.session, ["10.1155/2019/1258782"], Mock(), self.args)
        self.assertEqual(result, {})


class TestLoadRescueCandidates(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "state.sqlite3"
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE papers (key TEXT PRIMARY KEY, title TEXT, authors TEXT, year INTEGER,
                         doi TEXT, status TEXT, oa_status TEXT, pdf_url TEXT, file_path TEXT, error TEXT,
                         updated_at TEXT)""")
        conn.executemany("INSERT INTO papers (key, doi, title, status) VALUES (?, ?, ?, ?)", [
            ("doi:10.1/a", "10.1/a", "Errored Paper", "error"),
            ("doi:10.1/b", "10.1/b", "No OA Paper", "no_oa"),
            ("doi:10.1/c", "10.1/c", "Downloaded Paper", "downloaded"),
            ("doi:10.1/d", "10.1/d", "Excluded Crank Paper", "excluded_crank"),
            ("title:e", None, "No DOI Paper", "error"),
        ])
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()

    def test_only_rescue_statuses_with_doi_returned(self):
        candidates = rve.load_rescue_candidates(self.conn, ("error", "no_oa", "oa_url_not_pdf"))
        keys = {c["key"] for c in candidates}
        self.assertEqual(keys, {"doi:10.1/a", "doi:10.1/b"})

    def test_downloaded_and_excluded_rows_never_touched(self):
        candidates = rve.load_rescue_candidates(self.conn, ("error", "no_oa", "oa_url_not_pdf"))
        keys = {c["key"] for c in candidates}
        self.assertNotIn("doi:10.1/c", keys)
        self.assertNotIn("doi:10.1/d", keys)

    def test_no_doi_rows_never_returned(self):
        candidates = rve.load_rescue_candidates(self.conn, ("error", "no_oa", "oa_url_not_pdf"))
        keys = {c["key"] for c in candidates}
        self.assertNotIn("title:e", keys)


if __name__ == "__main__":
    unittest.main()
