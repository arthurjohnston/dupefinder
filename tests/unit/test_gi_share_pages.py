"""general_index/gi_share_pages.py: a result page made into one standalone file."""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_share_pages as share  # noqa: E402

PAGE = """<title>T</title>
<style>body { color: red; }</style>
<div class="page">
<a href="img/GI-01/r01-a-1.jpg"><img src="img/GI-01/r01-a-1.jpg" width="2" height="2" style="width:50%" loading="lazy" alt="x"></a>
Paper A (left): <a href="../lib/papers/a.pdf">a.pdf</a>
<a href="https://doi.org/10.1/x">doi</a>
</div>
"""


class TestShareable(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "pages" / "img" / "GI-01").mkdir(parents=True)
        (self.dir / "pages" / "img" / "GI-01" / "r01-a-1.jpg").write_bytes(b"\xff\xd8jpeg")
        (self.dir / "lib" / "papers").mkdir(parents=True)
        (self.dir / "lib" / "papers" / "a.pdf").write_bytes(b"%PDF-1.4")

    def test_images_inlined_links_dropped_skeleton_added(self):
        out, notes = share.shareable(PAGE, self.dir / "pages")
        self.assertEqual(notes, [])
        self.assertTrue(out.startswith("<!doctype html>"))
        self.assertIn("<head>", out)
        self.assertLess(out.index("<style>"), out.index("</head>"))
        self.assertIn('src="data:image/jpeg;base64,' + base64.b64encode(b"\xff\xd8jpeg").decode(), out)
        self.assertNotIn("img/GI-01", out)
        self.assertNotIn('loading="lazy"', out)
        self.assertIn("a.pdf (not included)", out)
        self.assertNotIn("../lib", out)
        self.assertIn('href="https://doi.org/10.1/x"', out)

    def test_embed_pdfs(self):
        out, _ = share.shareable(PAGE, self.dir / "pages", embed_pdfs=True)
        self.assertIn('download="a.pdf" href="data:application/pdf;base64,' + base64.b64encode(b"%PDF-1.4").decode(),
                      out)

    def test_missing_image_reported(self):
        (self.dir / "pages" / "img" / "GI-01" / "r01-a-1.jpg").unlink()
        _, notes = share.shareable(PAGE, self.dir / "pages")
        self.assertEqual(notes, ["missing image img/GI-01/r01-a-1.jpg"])


if __name__ == "__main__":
    unittest.main()
