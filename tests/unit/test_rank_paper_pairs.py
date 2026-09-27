#!/usr/bin/env python3
"""Unit tests for rank_paper_pairs.py's wrong-PDF triage check.

The check exists because five pairs whose records held the wrong paper's PDF text sat at the top of
a real gapped backlog sweep, measuring 100%/100%. Its whole value is being decisive where the naive
"does this paper contain its own title" test is not -- roughly 27% of this corpus fails that test for
benign reasons -- so the tests below pin down both halves: it fires on the pair signature, and it
stays silent on the benign shapes.
"""

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import rank_paper_pairs as rpp  # noqa: E402
import write_dupe_reports_html as wdrh  # noqa: E402
from compare_two_papers import ShingleRun  # noqa: E402


def ctp_run(sa, ea, sb, eb):
    return ShingleRun(ea - sa, 0, 0, "", "", sa, ea, sb, eb, 0, 0)


def make_db(papers):
    """papers: {paper_id: (title, [paragraph texts])} -> an in-memory library.sqlite3 shape."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE paragraphs (paper_id INTEGER, para_index INTEGER, text TEXT)")
    for pid, (title, paras) in papers.items():
        conn.execute("INSERT INTO papers (id, title) VALUES (?, ?)", (pid, title))
        for i, txt in enumerate(paras):
            conn.execute("INSERT INTO paragraphs (paper_id, para_index, text) VALUES (?, ?, ?)",
                         (pid, i, txt))
    return conn


TITLE_A = "A Comparison of Popular Home Security Systems in Modern Dwellings"
TITLE_B = "Drug Prediction System Using Data Mining Techniques A Survey Report"


class TestWrongPdfSide(unittest.TestCase):
    def test_flags_the_side_holding_the_other_paper(self):
        conn = make_db({1: (TITLE_A, [TITLE_A, "body about door sensors"]),
                        2: (TITLE_B, [TITLE_A, "body about door sensors"])})
        self.assertEqual(rpp.wrong_pdf_side(conn, 1, 2), "B")

    def test_direction_is_reported_correctly_when_reversed(self):
        conn = make_db({1: (TITLE_A, [TITLE_B, "body about drug records"]),
                        2: (TITLE_B, [TITLE_B, "body about drug records"])})
        self.assertEqual(rpp.wrong_pdf_side(conn, 1, 2), "A")

    def test_silent_when_both_papers_carry_their_own_title(self):
        conn = make_db({1: (TITLE_A, [TITLE_A, "shared boilerplate"]),
                        2: (TITLE_B, [TITLE_B, "shared boilerplate"])})
        self.assertIsNone(rpp.wrong_pdf_side(conn, 1, 2))

    def test_silent_when_a_title_is_merely_unextractable(self):
        """The benign shape: neither paper's title survived extraction (image title, two-column
        space loss). Absence alone must not flag -- the other paper's title has to be present."""
        conn = make_db({1: (TITLE_A, ["mangled header", "body one"]),
                        2: (TITLE_B, ["mangled header", "body two"])})
        self.assertIsNone(rpp.wrong_pdf_side(conn, 1, 2))

    def test_silent_on_a_catalogue_added_prefix(self):
        """"Review of: X" in the metadata against "X" in the PDF is a benign mismatch, and the
        probe is a 40-char slice of the normalized title, so the prefix shifts it."""
        conn = make_db({1: ("Review of: " + TITLE_A, [TITLE_A, "body"]),
                        2: (TITLE_B, [TITLE_B, "body"])})
        self.assertIsNone(rpp.wrong_pdf_side(conn, 1, 2))

    def test_ignores_punctuation_case_and_html_entities(self):
        conn = make_db({1: ("AI Doctor &amp; Vision: A Study of Clinical Decision Support", 
                            ["AI DOCTOR & VISION - A STUDY OF CLINICAL DECISION SUPPORT", "body"]),
                        2: (TITLE_B, [TITLE_B, "body"])})
        self.assertIsNone(rpp.wrong_pdf_side(conn, 1, 2))

    def test_silent_when_a_title_is_too_short_to_probe(self):
        """A very short title gives a probe with no discriminating power, so the check declines to
        judge rather than guessing."""
        conn = make_db({1: ("AI", ["AI", "body"]), 2: (TITLE_B, [TITLE_B, "body"])})
        self.assertIsNone(rpp.wrong_pdf_side(conn, 1, 2))

    def test_silent_when_a_paper_has_no_paragraphs(self):
        conn = make_db({1: (TITLE_A, []), 2: (TITLE_B, [TITLE_B, "body"])})
        self.assertIsNone(rpp.wrong_pdf_side(conn, 1, 2))

    def test_silent_on_a_missing_paper_row(self):
        conn = make_db({1: (TITLE_A, [TITLE_A])})
        self.assertIsNone(rpp.wrong_pdf_side(conn, 1, 999))


class TestSpineIsTheOnlyOrderedStep(unittest.TestCase):
    """The ordered walk that the whole-document diff uses loses reordered material BY DESIGN, and
    that is a rendering limit, not a detection one. Pinned here so the asymmetry stays deliberate:
    the scan's own coverage is order-independent (see test_compare_two_papers.TestOrderIndependence)
    while the spine's is not, which is exactly why render_whole_document_diff() headlines the scan's
    figure and prints a caveat whenever the two diverge."""

    def _runs(self, pairs):
        """Minimal ShingleRun stand-ins: (length, para_a, para_b, text_a, text_b, sa, ea, sb, eb, subs, indels)."""
        return [ctp_run(a0, a1, b0, b1) for a0, a1, b0, b1 in pairs]

    def test_forward_runs_all_survive_the_spine(self):
        runs = self._runs([(0, 100, 0, 100), (200, 300, 200, 300), (400, 500, 400, 500)])
        spine, dropped = wdrh._wdd_spine(runs)
        self.assertEqual(len(spine), 3)
        self.assertEqual(dropped, [])

    def test_a_crossing_run_cannot_be_placed(self):
        """Run 2 is later in A but EARLIER in B -- the two cannot both sit in one reading order."""
        runs = self._runs([(0, 100, 400, 500), (200, 300, 0, 100)])
        spine, dropped = wdrh._wdd_spine(runs)
        self.assertEqual(len(spine), 1)
        self.assertEqual(len(dropped), 1)

    def test_the_spine_keeps_the_heaviest_chain(self):
        """Given a choice it maximises matched words, so the strongest evidence is what gets drawn."""
        runs = self._runs([(0, 10, 500, 510), (100, 400, 100, 400)])
        spine, _ = wdrh._wdd_spine(runs)
        self.assertEqual([(r[5], r[6]) for r in spine], [(100, 400)])


if __name__ == "__main__":
    unittest.main()
