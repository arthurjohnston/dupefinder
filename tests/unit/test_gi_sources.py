"""general_index/gi_sources.py: which sources reach the comparison page, the overview's arithmetic, and the
cases.json/index entry for a case with several sources."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_report_case as gi_report  # noqa: E402
import gi_sources  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gi_pubpeer import fd  # noqa: E402


def write_sources(d, sources, **top):
    (Path(d) / "sources.json").write_text(json.dumps({**top, "sources": sources}))


class TestLoad(unittest.TestCase):
    def test_only_text_sources_with_saved_text(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "source-1.txt").write_text("words")
            write_sources(d, [
                {"title": "Text", "kind": ["text", "tables"], "fulltext": "source-1.txt"},
                {"title": "Refs", "kind": ["references"]},
                {"title": "Not saved", "kind": "text", "fulltext": ""},
                {"title": "Missing file", "kind": "text", "fulltext": "source-4.txt"},
            ])
            text, other = gi_sources.load(d)
            self.assertEqual([(s["n"], s["title"]) for s in text], [(1, "Text")])
            self.assertEqual([s["n"] for s in other], [2, 3, 4])

    def test_no_sources_json(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gi_sources.load(d), ([], []))

    def test_text_file_paragraphs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "source-1.txt").write_text("First  paragraph\nwrapped here.\n\nok\n\nSecond one, three words.")
            paras, pdf = gi_sources.source_paragraphs(d, {"fulltext": "source-1.txt"})
            self.assertEqual(paras, ["First paragraph wrapped here.", "Second one, three words."])
            self.assertIsNone(pdf)


class TestOverview(unittest.TestCase):
    def measured(self, paired, s1, total=100):
        return {"words": total, "paired": {"covered": set(paired), "runs": 2, "longest": 20},
                1: {"covered": set(s1), "runs": 1, "longest": 15}}

    def test_union_disjoint(self):
        page = gi_sources.overview_html(fd(), [{"n": 1, "title": "Other review", "year": 2005, "cited": False}], [],
                                        self.measured(range(0, 20), range(50, 60)))
        self.assertIn("About <strong>30%</strong>", page)
        self.assertIn("30 of its\n    100 words", page)
        self.assertIn("2 earlier works, which don&rsquo;t overlap", page)
        self.assertIn("<strong>20%</strong>", page)
        self.assertIn("<strong>10%</strong>", page)

    def test_union_counts_shared_words_once(self):
        page = gi_sources.overview_html(fd(), [{"n": 1, "title": "Other review"}], [],
                                        self.measured(range(0, 20), range(15, 25)))
        self.assertIn("About <strong>25%</strong>", page)
        self.assertIn("5 of those words are in more than one of them", page)

    def test_unmeasured_sources_listed(self):
        page = gi_sources.overview_html(fd(), [{"n": 1, "title": "Other review"}],
                                        [{"n": 2, "title": "Cancer paper", "year": 2009, "kind": ["references"]}],
                                        self.measured(range(10), range(10, 15)))
        self.assertIn("Cancer paper (2009): references", page)

    def test_later_title_override(self):
        page = gi_sources.overview_html(fd(), [{"n": 1, "title": "x"}], [], self.measured(range(5), ()),
                                        later_title="As Printed")
        self.assertIn("<h1>As Printed (2015)", page)


def section(letter, title):
    return gi_sources._tag(f'<div class="lede"><div class="comparison"><div class="paper-card"><p class="role ">'
                           f'EARLIER / SOURCE</p><h2>{title}</h2></div><div class="connector">&harr;</div>'
                           f'<div class="paper-card"><p class="role later">LATER / FLAGGED</p><h2>New</h2></div>'
                           f'</div></div>', letter)


class TestCards(unittest.TestCase):
    def test_one_card_per_earlier_work_and_the_later_one(self):
        a, b = section("A", "Paired"), section("B", "Other review")
        box = gi_sources.cards_html([("A", a, "31%"), ("B", b, "4.5%")], gi_sources._card(a, "LATER / FLAGGED"), "35%")
        self.assertLess(box.index(">EARLIER / SOURCE A<"), box.index(">EARLIER / SOURCE B<"))
        self.assertLess(box.index(">EARLIER / SOURCE B<"), box.index(">LATER / FLAGGED<"))
        self.assertIn('<strong>4.5%</strong> of the later article &middot; <a href="#article-b">', box)
        self.assertIn("<strong>35%</strong> of its text is in these 2 earlier works", box)
        self.assertEqual(box.count('class="paper-card"'), 3)

    def test_no_box_without_cards(self):
        self.assertEqual(gi_sources.cards_html([("A", "<p>no card</p>", "31%")], None, "31%"), "")


class TestNoSources(unittest.TestCase):
    def test_page_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gi_sources.add_sources("<p>page</p>", {}, fd(), d), ("<p>page</p>", None))


class TestRecord(unittest.TestCase):
    def test_union_in_cases_json_and_index(self):
        with tempfile.TemporaryDirectory() as d:
            gi_report.record(d, "GI-874", {**fd(), "sources": {"sources": 4, "union_words": 1475, "union_pct": 40.8}})
            gi_report.record(d, "GI-80", fd())
            cases = json.loads((Path(d) / "cases.json").read_text())
            self.assertEqual(cases["GI-874"]["union_pct"], 40.8)
            self.assertNotIn("union_pct", cases["GI-80"])
            page = gi_report.write_index(d).read_text()
            self.assertIn("41% in 4 earlier works", page)
            self.assertEqual(page.count("earlier works</small>"), 1)


if __name__ == "__main__":
    unittest.main()
