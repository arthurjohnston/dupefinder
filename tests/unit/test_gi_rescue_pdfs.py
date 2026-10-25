#!/usr/bin/env python3
"""Unit tests for gi_rescue_pdfs.py's archives step: pmc_text()'s parsing of a PMC article page (furniture
stripped, references kept), write_text_pdf() round-tripping through title_on_pages(), and wayback_copy()
keeping only a snapshot that is a PDF of the paper."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "general_index"))
sys.path.insert(0, str(ROOT))

import gi_rescue_pdfs as grp  # noqa: E402

PAGE = """<html><nav><p>Site menu</p></nav><article>
<h1>Associations of <i>sit-up</i> ability with sarcopenia</h1>
<ul><li>Takashi Abe</li><li>Search in PMC</li><li>Author information</li></ul>
<h2>Abstract</h2><p>To test the hypothesis that sit-up performance &amp; sarcopenia are linked.</p>
<h2>References</h2><li>Baxter RE: Improvement in sit-up performance. [ DOI ] [ PubMed ] [ Google Scholar ]</li>
</article></html>"""


class TestPmcText(unittest.TestCase):
    def test_title_and_blocks(self):
        title, blocks = grp.pmc_text(PAGE)
        self.assertEqual(title, "Associations of sit-up ability with sarcopenia")
        texts = [x for _, x in blocks]
        self.assertIn("Takashi Abe", texts)
        self.assertIn("To test the hypothesis that sit-up performance & sarcopenia are linked.", texts)
        self.assertIn("Baxter RE: Improvement in sit-up performance.", texts)

    def test_furniture_dropped(self):
        texts = [x for _, x in grp.pmc_text(PAGE)[1]]
        for chrome in ("Search in PMC", "Author information", "Site menu"):
            self.assertNotIn(chrome, texts)
        self.assertFalse(any("Google Scholar" in x for x in texts))


class TestTextPdf(unittest.TestCase):
    def test_round_trip(self):
        title, blocks = grp.pmc_text(PAGE)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "x-pmc.pdf"
            grp.write_text_pdf(dest, title, blocks * 200)       # several pages
            self.assertTrue(dest.exists())
            self.assertTrue(grp.title_on_pages(dest, title))
            self.assertFalse(grp.title_on_pages(dest, "Rechargeable sodium cobalt oxide batteries"))


class TestWaybackCopy(unittest.TestCase):
    def setUp(self):
        self.args = Mock(max_tries=6, max_retries=0, timeout=10)
        self.log = Mock()

    def _lookup(self, status="200"):
        resp = Mock(status_code=200)
        resp.json.return_value = {"archived_snapshots": {"closest": {
            "available": True, "status": status, "timestamp": "20200101000000"}}}
        return resp

    def test_kept_when_title_matches(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.object(grp.rp, "http_get", return_value=self._lookup()), \
                patch.object(grp.rp, "download_pdf", return_value=True) as dl, \
                patch.object(grp, "title_on_pages", return_value=True):
            got = grp.wayback_copy(None, None, self.args, self.log, ["https://x.org/a.pdf"], Path(d) / "a.pdf", "T")
        self.assertEqual(got, "https://web.archive.org/web/20200101000000id_/https://x.org/a.pdf")
        self.assertEqual(dl.call_args[0][1], got)

    def test_wrong_paper_discarded(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.object(grp.rp, "http_get", return_value=self._lookup()), \
                patch.object(grp.rp, "download_pdf", return_value=True), \
                patch.object(grp, "title_on_pages", return_value=False):
            dest = Path(d) / "a.pdf"
            dest.write_bytes(b"%PDF-")
            self.assertIsNone(grp.wayback_copy(None, None, self.args, self.log, ["https://x.org/a.pdf"], dest, "T"))
            self.assertFalse(dest.exists())

    def test_non_200_snapshot_skipped(self):
        with patch.object(grp.rp, "http_get", return_value=self._lookup("403")), \
                patch.object(grp.rp, "download_pdf") as dl:
            self.assertIsNone(grp.wayback_copy(None, None, self.args, self.log, ["https://x.org/a.pdf"],
                                               Path("/nonexistent/a.pdf"), "T"))
        dl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
