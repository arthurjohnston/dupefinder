#!/usr/bin/env python3
"""Unit tests for compare_two_papers.py's shingle matching -- the exact matcher, the lockstep
X-drop extension, and the gapped extension that bridges insertions/deletions.

The gapped path is the reason this file exists: it is the one part of the matcher that can report
a run whose two sides are DIFFERENT LENGTHS, which every renderer downstream had to be taught
about, so its boundaries and its substitution/indel counts need pinning down.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import compare_two_papers as ctp  # noqa: E402


def words(text):
    """The (word, para_index) shape load_paper_words() returns, all in one paragraph."""
    return [(w, 0) for w in text.split()]


BASE = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"


class TestFindShingleMatchesExact(unittest.TestCase):
    def test_identical_documents_are_one_run(self):
        runs = ctp.find_shingle_matches(words(BASE), words(BASE), shingle_size=3)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].length, 12)
        self.assertEqual(runs[0].substitutions, 0)
        self.assertEqual(runs[0].indels, 0)

    def test_no_shared_shingle_is_no_runs(self):
        runs = ctp.find_shingle_matches(words(BASE), words("one two three four five"),
                                        shingle_size=3)
        self.assertEqual(runs, [])

    def test_run_shorter_than_shingle_size_is_not_reported(self):
        runs = ctp.find_shingle_matches(words("alpha bravo charlie"),
                                        words("zulu alpha bravo yankee"), shingle_size=3)
        self.assertEqual(runs, [])

    def test_substitution_splits_the_run_without_x_drop(self):
        b = BASE.replace("foxtrot", "GOLFO")
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3)
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(r.substitutions == 0 for r in runs))

    def test_runs_are_sorted_longest_first(self):
        b = "alpha bravo charlie XX echo foxtrot golf hotel india juliet kilo lima"
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3)
        self.assertEqual([r.length for r in runs], sorted((r.length for r in runs), reverse=True))

    def test_indices_point_back_into_the_word_lists(self):
        runs = ctp.find_shingle_matches(words(BASE), words("zulu " + BASE), shingle_size=3)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual((run.start_a, run.end_a), (0, 12))
        self.assertEqual((run.start_b, run.end_b), (1, 13))
        self.assertEqual(run.text_a, BASE)


class TestXDropExtension(unittest.TestCase):
    def test_bridges_an_isolated_substitution(self):
        b = BASE.replace("foxtrot", "GOLFO")
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=3)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].length, 12)
        self.assertEqual(runs[0].substitutions, 1)
        self.assertEqual(runs[0].indels, 0)

    def test_does_not_bridge_an_insertion_at_any_x_drop(self):
        """The documented limitation the gapped extension exists to remove: an inserted word
        desyncs the lockstep walk, and no x_drop value recovers it."""
        b = "alpha bravo charlie delta echo XX YY foxtrot golf hotel india juliet kilo lima"
        for x_drop in (3, 10, 50):
            runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=x_drop)
            self.assertEqual(len(runs), 2, f"x_drop={x_drop} should still split the run")

    def test_both_sides_stay_the_same_length(self):
        b = BASE.replace("foxtrot", "GOLFO")
        run = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=3)[0]
        self.assertEqual(run.end_a - run.start_a, run.end_b - run.start_b)


class TestGappedExtension(unittest.TestCase):
    def test_bridges_an_insertion(self):
        b = "alpha bravo charlie delta echo XX YY foxtrot golf hotel india juliet kilo lima"
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=8, gap_open=2)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].indels, 2)
        self.assertEqual(runs[0].substitutions, 0)
        self.assertEqual(runs[0].end_a - runs[0].start_a, 12)
        self.assertEqual(runs[0].end_b - runs[0].start_b, 14)

    def test_bridges_a_deletion(self):
        b = "alpha bravo charlie delta echo hotel india juliet kilo lima"
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=8, gap_open=2)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].indels, 2)
        self.assertEqual(runs[0].end_a - runs[0].start_a, 12)
        self.assertEqual(runs[0].end_b - runs[0].start_b, 10)

    def test_counts_substitutions_and_indels_together(self):
        b = "alpha bravo charlie ZULU echo XX foxtrot golf hotel india juliet kilo lima"
        run = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=8,
                                       gap_open=2)[0]
        self.assertEqual(run.substitutions, 1)
        self.assertEqual(run.indels, 1)

    def test_gap_longer_than_max_gap_is_not_bridged(self):
        filler = " ".join(f"x{i}" for i in range(15))
        b = f"alpha bravo charlie delta echo {filler} foxtrot golf hotel india juliet kilo lima"
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=8,
                                        gap_open=2, max_gap=10)
        self.assertEqual(len(runs), 2)

    def test_raising_max_gap_bridges_a_longer_gap(self):
        """With enough matching text after it to repay the gap's cost. A 15-word gap is charged
        gap_open + 14 * gap_extend = 16, so the tail has to be longer than that -- BASE's own
        7-word tail cannot pay for it, which is what test_a_long_gap_needs_matches_to_pay_for_it
        pins down."""
        tail = " ".join(f"t{i}" for i in range(25))
        long_base = f"{BASE} {tail}"
        filler = " ".join(f"x{i}" for i in range(15))
        b = f"alpha bravo charlie delta echo {filler} foxtrot golf hotel india juliet kilo lima {tail}"
        runs = ctp.find_shingle_matches(words(long_base), words(b), shingle_size=3, x_drop=20,
                                        gap_open=2, max_gap=20)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].indels, 15)

    def test_a_long_gap_needs_matches_to_pay_for_it(self):
        """max_gap permitting a gap is necessary but not sufficient: the affine cost still has to
        be earned back by matches beyond it, or the extension trims back and the run stays split.
        This is the property that stops the gapped extension from stitching two genuinely separate
        passages together across unrelated text."""
        filler = " ".join(f"x{i}" for i in range(15))
        b = f"alpha bravo charlie delta echo {filler} foxtrot golf hotel india juliet kilo lima"
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=20,
                                        gap_open=2, max_gap=20)
        self.assertEqual(len(runs), 2)

    def test_the_default_band_bridges_a_mid_sized_gap(self):
        """Pins the DEFAULT_MAX_GAP choice: a 15-word insertion is within the default band, so with
        enough matching text beyond it to repay the cost it comes back as ONE run. At the old
        default of 10 this stayed split, which is what made real pages report the same passage in
        several pieces."""
        tail = " ".join(f"t{i}" for i in range(25))
        filler = " ".join(f"x{i}" for i in range(15))
        a = words(f"{BASE} {tail}")
        b = words(f"alpha bravo charlie delta echo {filler} foxtrot golf hotel india juliet kilo lima {tail}")
        runs = ctp.find_shingle_matches(a, b, shingle_size=3, x_drop=20, gap_open=2)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].indels, 15)

    def test_identical_documents_are_unaffected(self):
        runs = ctp.find_shingle_matches(words(BASE), words(BASE), shingle_size=3, x_drop=8,
                                        gap_open=2)
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0].substitutions, runs[0].indels), (0, 0))

    def test_unrelated_documents_stay_unmatched(self):
        runs = ctp.find_shingle_matches(words(BASE), words("one two three four five six"),
                                        shingle_size=3, x_drop=8, gap_open=2)
        self.assertEqual(runs, [])

    def test_a_high_gap_open_behaves_like_lockstep(self):
        """Charging more for a gap than the surrounding matches can repay should leave the run
        split, i.e. the parameter really is the knob it claims to be."""
        b = "alpha bravo charlie delta echo XX YY foxtrot golf hotel india juliet kilo lima"
        runs = ctp.find_shingle_matches(words(BASE), words(b), shingle_size=3, x_drop=8,
                                        gap_open=50)
        self.assertEqual(len(runs), 2)


class TestOrderIndependence(unittest.TestCase):
    """Detection must not depend on WHERE in each document the shared text sits.

    This is load-bearing and easy to break by accident: find_shingle_matches() indexes every shingle
    of A in a dict and looks up each of B's, so relocated material is found wherever it moved to. A
    real case in this corpus depends on it -- one paper's CONCLUSION (word 6702) reappears in the
    other's ABSTRACT (word 210). write_dupe_reports_html.py's whole-document view is the only thing
    in the project that imposes an order, for layout, and it reports what that costs; see
    test_rank_paper_pairs / _wdd_spine. If these tests ever fail, reordered reuse has stopped being
    detected, not merely stopped being drawn in reading order."""

    def _blocks(self, n=6, per=14):
        return [" ".join(f"b{b}w{i}" for i in range(per)) for b in range(n)]

    def _words(self, blocks):
        return [(w, i) for i, blk in enumerate(blocks) for w in blk.split()]

    def _coverage(self, wa, wb):
        runs = ctp.find_shingle_matches(wa, wb, shingle_size=10, x_drop=8, gap_open=2)
        covered = set()
        for r in runs:
            covered.update(range(r.start_a, r.end_a))
        return len(covered) / len(wa)

    def test_identical_order_is_fully_covered(self):
        b = self._blocks()
        self.assertEqual(self._coverage(self._words(b), self._words(b)), 1.0)

    def test_reversed_blocks_are_still_fully_covered(self):
        b = self._blocks()
        self.assertEqual(self._coverage(self._words(b), self._words(list(reversed(b)))), 1.0)

    def test_shuffled_blocks_are_still_fully_covered(self):
        b = self._blocks()
        shuffled = [b[3], b[0], b[5], b[1], b[4], b[2]]
        self.assertEqual(self._coverage(self._words(b), self._words(shuffled)), 1.0)

    def test_a_block_moved_to_the_far_end_is_still_found(self):
        """The shape of the real case: material from one end of A appears at the other end of B."""
        b = self._blocks()
        moved = b[1:] + [b[0]]
        self.assertEqual(self._coverage(self._words(b), self._words(moved)), 1.0)


class TestExtendGappedBoundaries(unittest.TestCase):
    def test_never_returns_a_worse_boundary(self):
        a = ["alpha", "bravo", "charlie"]
        b = ["zulu", "yankee", "xray"]
        self.assertEqual(ctp._extend_gapped(a, b, 0, 0, 1, 1, 8, 2, 1, 10), (0, 0))

    def test_stops_at_the_end_of_a_document(self):
        a = b = ["alpha", "bravo"]
        self.assertEqual(ctp._extend_gapped(a, b, 0, 0, 1, 1, 8, 2, 1, 10), (2, 2))

    def test_backward_direction_mirrors_forward(self):
        a = b = ["alpha", "bravo", "charlie"]
        self.assertEqual(ctp._extend_gapped(a, b, 3, 3, -1, 1, 8, 2, 1, 10), (0, 0))


class TestAlignmentCounts(unittest.TestCase):
    def test_identical_slices_have_no_edits(self):
        a = b = ["one", "two", "three"]
        subs, indels, blocks = ctp._alignment_counts(a, b, 0, 3, 0, 3)
        self.assertEqual((subs, indels), (0, 0))
        self.assertEqual(blocks, [(0, 0, 3)])

    def test_substitution_counted_once(self):
        subs, indels, _ = ctp._alignment_counts(["one", "two", "three"], ["one", "TWO", "three"],
                                                 0, 3, 0, 3)
        self.assertEqual((subs, indels), (1, 0))

    def test_insertion_counted_as_indel(self):
        subs, indels, _ = ctp._alignment_counts(["one", "three"], ["one", "two", "three"],
                                                 0, 2, 0, 3)
        self.assertEqual((subs, indels), (0, 1))

    def test_blocks_are_diagonal_segments_offset_from_the_slice_start(self):
        a = ["x", "one", "two", "three"]
        b = ["y", "z", "one", "INS", "two", "three"]
        _, _, blocks = ctp._alignment_counts(a, b, 1, 4, 2, 6)
        self.assertEqual(blocks, [(0, 0, 1), (1, 2, 2)])


class TestStripPageFurniture(unittest.TestCase):
    """Real masthead/footer strings from Zestera papers, merged into body text the way PyMuPDF
    extraction leaves them -- and the reference-list lookalikes that must survive."""

    def strip(self, text):
        return " ".join(ctp.strip_page_furniture(text).split())

    def test_masthead_with_footer_and_dates(self):
        self.assertEqual(self.strip(
            "the model AMERICAN JOURNAL OF MANAGEMENT AND IOT MEDICAL COMPUTING Peer Reviewed, "
            "Referred & Indexed Journal E-ISSN: 3069-0110 Vol.5, No.2(2026) www.ajmimc.com 234 "
            "Received: 28-02-2026 | Accepted: 01-04-2026 | Published: 09-04-2026 | was trained"),
            "the model was trained")

    def test_misspelled_masthead_with_volume_stamp(self):
        self.assertEqual(self.strip(
            "results Peer Reviewed, Rferred & Indexed Journal E-ISSN:3069-0102 VOL.6, NO. 2(2026) "
            "433 show"), "results show")

    def test_publisher_site_masthead(self):
        self.assertEqual(self.strip(
            "a International Journal of AI Electronics and Nexus Energy Peer Reviewed, Referred & "
            "Indexed Journal ISSN: 3070-0515 www.zesterapublications.com Original Research Paper b"),
            "a b")

    def test_standalone_page_footers(self):
        self.assertEqual(self.strip("a Vol.5, No.2(2026) www.ajmimc.com 258 b"), "a b")
        self.assertEqual(self.strip("a www.ijpams.com IJPAMS| 145 b"), "a b")
        self.assertEqual(self.strip("a forecast 2026, Vol 2 Issue 2 | 36 b"), "a forecast b")
        self.assertEqual(self.strip("a IJDIM, 2026, 5 (2(1)), 585-591 | 585 b"), "a b")

    def test_reference_list_entries_survive(self):
        for text in ['"Africa, Vol. 1, No. 78 (2008), pp. 136 152."',
                     "ACM Transactions on Intelligent Systems and Technology, Vol. 9, No. 4, Article 39",
                     "Engineering (IJSRCSEIT), ISSN : 2456-3307 , Volume 5 Issue 2, pp. 360-364",
                     "published in the International Journal of Data Science and IOT Management System"]:
            self.assertEqual(self.strip(text), " ".join(text.split()))

    def test_load_paper_words_strips_only_when_asked(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE paragraphs (paper_id INTEGER, para_index INTEGER, text TEXT)")
        conn.execute("INSERT INTO paragraphs VALUES (1, 0, 'a www.ajmimc.com 4 b')")
        self.assertEqual([w for w, _ in ctp.load_paper_words(conn, 1)], ["a", "www.ajmimc.com", "4", "b"])
        self.assertEqual([w for w, _ in ctp.load_paper_words(conn, 1, strip_furniture=True)], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
