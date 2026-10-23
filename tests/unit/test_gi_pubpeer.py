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
        self.assertEqual(pp.clean_title("Resistant <i>Staphylococcus aureus</i> in milk &amp; <b>meat</b>"),
                         "Resistant *Staphylococcus aureus* in milk & meat")


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

    def test_images_side_by_side(self):
        imgs = [{"kind": "titles", "name": "GI-1-titles.jpg"},
                {"kind": "passage", "n": 1, "name": "GI-1-passage-1.jpg", "words": 81, "file": "passage-1.jpg",
                 "left": {"doi": "10.1/old", "pages": [3], "text": "old wording here"},
                 "right": {"doi": "10.2/NEW", "pages": [4, 5], "text": "new wording here"}}]
        md = pp.markdown({**fd(passages=5), "images": imgs, "site_url": "https://x.example/GI-1.html"})
        self.assertIn("Titles and author lists as printed (earlier article left, this article right):\n\n"
                      "[Image: GI-1-titles.jpg]", md)
        self.assertIn("**Passage 1**, 81 words (earlier article PDF p. 3, this article PDF p. 4-5).", md)
        self.assertIn('- Earlier article: "old wording here"\n\n- This article: "new wording here"\n\n'
                      '[Image: GI-1-passage-1.jpg]', md)
        self.assertNotIn("longest shared passages", md)   # the images replace the text extracts
        self.assertTrue(md.endswith("[Image: GI-1-passage-1.jpg]"))   # no link to the project site
        self.assertIn("The 5 longest shared passages", pp.markdown(fd(passages=5)))   # no images: extracts
        self.assertIn("The 10 longest", pp.markdown(fd(passages=30)))
        self.assertNotIn("[Image", pp.markdown(fd()))

    def test_findings_strongest_first(self):
        f = {**fd(pct_later=20.0), "data_extra": ["Table 1 has the same means."], "extra": ["A typo."]}
        md = pp.markdown(f)
        self.assertIn("appears to report the same results data as an earlier article", md)
        order = [md.index(t) for t in ("Table 1 has", "A typo", "About **20%**", "share no names")]
        self.assertEqual(order, sorted(order))

    def test_order_unknown_makes_no_direction_claim(self):
        md = pp.markdown(fd(earlier_known=False))
        self.assertIn("can't be told from the publication years", md)
        self.assertNotIn("duplicate of", md)
        self.assertNotIn("earlier article", md)


class TestWholeDocument(unittest.TestCase):
    def test_threshold(self):
        self.assertFalse(pp.whole_document(fd(pct_later=90.0)))
        self.assertTrue(pp.whole_document(fd(pct_later=90.5)))
        self.assertTrue(pp.whole_document({**fd(), "first": {**fd()["first"], "pct": 99.0}}))
        self.assertFalse(pp.whole_document(fd(pct_later=None)))


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
        self.assertNotIn("<img", box)

    def test_box_with_images(self):
        jpg = b"\xff\xd8\xff\xd9"
        box = pp.box_html(fd(), "md", [{"name": "GI-1-titles.jpg", "jpeg": jpg},
                                       {"name": "GI-1-passage-1.jpg", "jpeg": jpg}])
        self.assertIn('download="GI-1-titles.jpg" href="data:image/jpeg;base64,', box)
        self.assertLess(box.index("GI-1-titles.jpg"), box.index("GI-1-passage-1.jpg"))
        self.assertEqual(box.count('<img class="pp-shot"'), 2)

    def test_overlap_in_summary_bar(self):
        page = pp.add_overlap('<div class="summary-bar"><span>runs</span></div>', fd())
        self.assertIn("shared text <strong>62%</strong> of the later article (2015) &middot; "
                      "<strong>40%</strong> of the earlier (2011)", page)
        self.assertIn("matched words <strong>2,480</strong>", page)
        self.assertLess(page.index("shared text"), page.index("runs"))


if __name__ == "__main__":
    unittest.main()
