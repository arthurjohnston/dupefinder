"""general_index/gi_pubpeer.py: the PubPeer links and the Markdown findings."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_pubpeer as pp  # noqa: E402


def fd(earlier_known=True, pct_later=62.0, byline="no byline overlap", cites=False, passages=2):
    return {"earlier_known": earlier_known,
            "first": {"title": "Old [paper]", "year": 2011, "doi": "10.1/old", "words": 5000, "pct": 40.0},
            "second": {"title": "New paper", "year": 2015, "doi": "10.2/new", "words": 4000, "pct": pct_later},
            "matched_words": 2480, "runs": 12, "byline": byline, "later_cites_earlier": cites,
            "passages": [{"words": 300 - i, "excerpt": f'passage "{i}" text'} for i in range(passages)]}


class TestLinks(unittest.TestCase):
    def test_pubpeer_url(self):
        self.assertEqual(pp.pubpeer_url("10.18203/2349-2902.isj20192387"),
                         "https://pubpeer.com/search?q=10.18203/2349-2902.isj20192387")
        self.assertIsNone(pp.pubpeer_url("gi:abc"))
        self.assertIsNone(pp.pubpeer_url(None))

    def test_excerpt(self):
        self.assertEqual(pp.excerpt("a b c"), "a b c")
        long = " ".join(str(i) for i in range(100))
        e = pp.excerpt(long, head=3, tail=2)
        self.assertEqual(e, "0 1 2 … 98 99")

    def test_clean_title(self):
        self.assertEqual(pp.clean_title('STUDY OF SKIN STAPLES AND SUTURES "Study of Skin Staples and Sut'),
                         "STUDY OF SKIN STAPLES AND SUTURES")
        self.assertEqual(pp.clean_title('On "quoted" words  here'), 'On "quoted" words here')


class TestMarkdown(unittest.TestCase):
    def test_later_known(self):
        md = pp.markdown(fd())
        self.assertIn("appears to be largely a duplicate of an earlier article", md)
        self.assertIn("[Old (paper)](https://doi.org/10.1/old) (2011; doi:10.1/old)", md)
        self.assertIn("About **62%** of this article's text", md)
        self.assertIn("That is 40% of the earlier article's text", md)
        self.assertIn("share no names", md)
        self.assertIn("does not appear to cite", md)
        self.assertIn("The 2 longest shared passages", md)
        self.assertIn("1. **300 words**: \"passage '0' text\"", md)

    def test_partial_reuse_and_unknowns(self):
        md = pp.markdown(fd(pct_later=20.0, byline="byline overlap: x", cites=None, passages=1))
        self.assertIn("appears to reuse substantial text from", md)
        self.assertNotIn("share no names", md)
        self.assertNotIn("cite", md.split("passage")[0])
        self.assertIn("The longest shared passage", md)

    def test_order_unknown_makes_no_direction_claim(self):
        md = pp.markdown(fd(earlier_known=False))
        self.assertIn("can't be told from the publication years", md)
        self.assertNotIn("duplicate of", md)
        self.assertNotIn("earlier article", md)


class TestPage(unittest.TestCase):
    def test_box_and_insert(self):
        box = pp.box_html(fd(), "x < y")
        self.assertIn("https://pubpeer.com/search?q=10.2/new", box)
        self.assertIn("x &lt; y", box)
        self.assertIn("ppCopy()", box)
        page = '<header>h</header><section class="exhibit title-shots">t</section>'
        out = pp.insert_box(page, "BOX")
        self.assertLess(out.index("BOX"), out.index("title-shots"))
        self.assertGreater(out.index("BOX"), out.index("</header>"))


if __name__ == "__main__":
    unittest.main()
