"""write_dupe_reports_html.py's text exhibits: runs close together in both papers merged into one exhibit
(group_runs()), and a few words of context around each instead of whole paragraphs (_context_bounds(),
render_side())."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import write_dupe_reports_html as wdr  # noqa: E402
from compare_two_papers import ShingleRun  # noqa: E402


def run(sa, ea, sb, eb):
    return ShingleRun(ea - sa, 0, 0, "x " * (ea - sa), "x " * (eb - sb), sa, ea, sb, eb, 0)


def words(paras):
    """[(word, para)] from a list of paragraph word counts, words named p<para>w<i>."""
    return [(f"p{p}w{i}", p) for p, n in enumerate(paras) for i in range(n)]


class TestGroupRuns(unittest.TestCase):
    def test_close_in_both_papers_merge(self):
        g = wdr.group_runs([run(100, 120, 500, 520), run(0, 20, 10, 30), run(40, 60, 45, 65)], gap=30)
        self.assertEqual([[r.start_a for r in grp] for grp in g], [[0, 40], [100]])

    def test_close_in_one_paper_only_stays_apart(self):
        g = wdr.group_runs([run(0, 20, 0, 20), run(25, 45, 200, 220)], gap=30)
        self.assertEqual(len(g), 2)

    def test_overlap_or_reorder_stays_apart(self):
        self.assertEqual(len(wdr.group_runs([run(0, 20, 50, 70), run(25, 45, 10, 30)], gap=30)), 2)
        self.assertEqual(len(wdr.group_runs([run(0, 20, 0, 20), run(15, 35, 15, 35)], gap=30)), 2)

    def test_gap_zero_is_one_per_run(self):
        self.assertEqual(len(wdr.group_runs([run(0, 20, 0, 20), run(21, 41, 21, 41)], gap=0)), 2)


class TestContext(unittest.TestCase):
    def test_words_either_side_within_the_paragraph(self):
        w = words([100, 100])            # paragraph 0: positions 0-99, paragraph 1: 100-199
        self.assertEqual(wdr._context_bounds(w, 50, 60, context_words=20), (30, 80))
        self.assertEqual(wdr._context_bounds(w, 5, 15, context_words=20), (0, 35))      # paragraph start
        self.assertEqual(wdr._context_bounds(w, 90, 110, context_words=20), (70, 130))  # spans the break

    def test_zero_gives_whole_paragraphs(self):
        w = words([10, 10, 10, 10, 10, 10])
        self.assertEqual(wdr._context_bounds(w, 25, 27, context_words=0, context_paras=2), (0, 50))

    def test_render_side(self):
        w = words([100])
        out, label, text = wdr.render_side(w, [(50, 55, set()), (60, 65, {1})], context_words=3)
        self.assertTrue(out.startswith("&hellip; p0w47 p0w48 p0w49 <mark class=\"match\">p0w50"))
        self.assertIn('</mark> p0w55 p0w56 p0w57 p0w58 p0w59 <mark class="match">p0w60</mark> '
                      '<mark class="match-diff">p0w61</mark> <mark class="match">p0w62', out)
        self.assertTrue(out.endswith("p0w65 p0w66 p0w67 &hellip;"))
        self.assertEqual(label, "0")
        self.assertEqual(len(text.split()), 21)


class TestExhibits(unittest.TestCase):
    def test_merged_exhibit_from_a_library(self):
        """Two runs 5 words apart in both papers make one exhibit, the context trimmed to the paragraph."""
        shared1 = " ".join(f"alpha{i}" for i in range(15))
        shared2 = " ".join(f"beta{i}" for i in range(15))
        filler = " ".join(f"other{i}" for i in range(60))
        db = sqlite3.connect(":memory:")
        db.execute("create table paragraphs (paper_id, para_index, text)")
        db.execute("insert into paragraphs values (1, 0, ?)", (f"{filler} {shared1} one two three four five {shared2} {filler}",))
        db.execute("insert into paragraphs values (2, 0, ?)", (f"{shared1} six seven eight nine ten {shared2}",))
        ex, total, _ = wdr.render_shingle_exhibits(db, 1, 2, 1, 10, 50, context_words=20, merge_gap=30)
        self.assertEqual((total, len(ex)), (2, 1))
        self.assertIn("2 runs, <b>30 words</b> matched, in passages of 35 words (earlier) and 35 (later)", ex[0])
        self.assertIn("one two three four five", ex[0])
        self.assertNotIn("other30", ex[0])          # trimmed: 20 filler words each side (40-59 before, 0-19 after)
        self.assertIn("other40", ex[0])
        apart, total, _ = wdr.render_shingle_exhibits(db, 1, 2, 1, 10, 50, context_words=20, merge_gap=0)
        self.assertEqual(len(apart), 2)


if __name__ == "__main__":
    unittest.main()
