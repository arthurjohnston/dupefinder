#!/usr/bin/env python3
"""Unit tests for sourcing/enrich_open_access.py's alt_urls() -- the pure
function that pulls "other" OA location URLs out of an OpenAlex work record,
distinct from open_access.oa_url."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "sourcing"))

import enrich_open_access as eoa  # noqa: E402


class TestAltUrls(unittest.TestCase):
    def test_returns_other_location_pdf_urls(self):
        work = {"locations": [
            {"pdf_url": "http://a.example.com/1.pdf"},
            {"pdf_url": "http://b.example.com/2.pdf"},
        ]}
        self.assertEqual(eoa.alt_urls(work, oa_url=None),
                          ["http://a.example.com/1.pdf", "http://b.example.com/2.pdf"])

    def test_excludes_the_already_known_oa_url(self):
        work = {"locations": [
            {"pdf_url": "http://a.example.com/1.pdf"},
            {"pdf_url": "http://b.example.com/2.pdf"},
        ]}
        self.assertEqual(eoa.alt_urls(work, oa_url="http://a.example.com/1.pdf"),
                          ["http://b.example.com/2.pdf"])

    def test_dedupes_repeated_locations(self):
        work = {"locations": [
            {"pdf_url": "http://a.example.com/1.pdf"},
            {"pdf_url": "http://a.example.com/1.pdf"},
            {"pdf_url": "http://b.example.com/2.pdf"},
        ]}
        self.assertEqual(eoa.alt_urls(work, oa_url=None),
                          ["http://a.example.com/1.pdf", "http://b.example.com/2.pdf"])

    def test_locations_with_no_pdf_url_are_skipped(self):
        # e.g. a landing-page-only location, same shape as the real SSRN/Berkeley
        # example that motivated this (see enrich_open_access.py's docstring).
        work = {"locations": [
            {"pdf_url": None, "landing_page_url": "http://example.com/landing"},
            {"pdf_url": "http://example.com/real.pdf"},
        ]}
        self.assertEqual(eoa.alt_urls(work, oa_url=None), ["http://example.com/real.pdf"])

    def test_no_locations_returns_empty_list(self):
        self.assertEqual(eoa.alt_urls({}, oa_url=None), [])
        self.assertEqual(eoa.alt_urls({"locations": []}, oa_url="http://example.com/x.pdf"), [])


if __name__ == "__main__":
    unittest.main()
