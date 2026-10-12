"""general_index/gi_write_pages.py: the evidence block follows the page's left/right order.

The pages put the earlier paper on the left (else the lower library id); fulltext's own A/B order is
independent of that, and the first version labelled the Local PDF links and the A/B figures by
fulltext's order, so on about half the pages they were swapped (found on GI-76, 2026-10-11).
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_write_pages as gwp  # noqa: E402


def library(years):
    f = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    conn = sqlite3.connect(f.name)
    conn.execute("create table papers (id integer primary key, year integer)")
    conn.executemany("insert into papers values (?, ?)", years.items())
    conn.commit()
    conn.close()
    return f.name


class TestPageOrder(unittest.TestCase):
    def test_earlier_paper_left_whatever_the_ids(self):
        lib = library({5: 2016, 9: 2012})
        self.assertEqual(gwp.page_order(lib, 5, 9), (9, 5))
        self.assertEqual(gwp.page_order(lib, 9, 5), (9, 5))

    def test_same_year_lower_id_left(self):
        lib = library({5: 2017, 9: 2017})
        self.assertEqual(gwp.page_order(lib, 9, 5), (5, 9))


class TestOriented(unittest.TestCase):
    ROW = {"doi_a": "a", "doi_b": "b", "year_a": 2016, "year_b": 2012, "paper_a": 5, "paper_b": 9,
           "ft_pct_a": 90.0, "ft_pct_b": 95.0, "ft_verdict": "real overlap"}

    def test_swaps_when_b_is_left(self):
        v = gwp.oriented(self.ROW, 9)
        self.assertEqual((v["paper_a"], v["doi_a"], v["year_a"], v["ft_pct_a"]), (9, "b", 2012, 95.0))
        self.assertEqual((v["paper_b"], v["doi_b"], v["year_b"], v["ft_pct_b"]), (5, "a", 2016, 90.0))

    def test_unchanged_when_a_is_left(self):
        self.assertEqual(gwp.oriented(self.ROW, 5), self.ROW)


if __name__ == "__main__":
    unittest.main()
