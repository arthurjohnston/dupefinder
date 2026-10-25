#!/usr/bin/env python3
"""Unit tests for write_dupe_reports_screenshots.py's pure parts: the PDF word stream, locating a
run's words in it, and turning placed words into highlight rectangles and crop clips.

Locating is the step everything else depends on and the one most likely to go quietly wrong (a
run highlighted in the wrong place reads as evidence), so most of the cases here are the ways
the PDF's word list differs from the library's extracted text: dropped running headers,
line-end hyphenation, words the two split at different places, the same boilerplate twice.
No PDF is opened; word tuples are built by hand in PyMuPDF's get_text("words") shape.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import write_dupe_reports_screenshots as wds  # noqa: E402
from write_dupe_reports_screenshots import Highlight  # noqa: E402

TEXT = ("the quick survey of renal outcomes found that most patients recovered within two weeks "
        "although a minority needed dialysis for longer periods")


def norms(text):
    return [wds.norm_token(w) for w in text.split() if wds.norm_token(w)]


def word(x0, y0, text, block=0, line=0, wno=0, w=30, h=10):
    """One PyMuPDF word tuple: (x0, y0, x1, y1, text, block, line, word)."""
    return (x0, y0, x0 + w, y0 + h, text, block, line, wno)


class TestNormalisation(unittest.TestCase):
    def test_punctuation_and_case_are_ignored(self):
        self.assertEqual(wds.norm_token("(2017),"), "2017")
        self.assertEqual(wds.norm_token("Patients."), "patients")

    def test_pure_punctuation_is_empty(self):
        self.assertEqual(wds.norm_token("–"), "")
        self.assertEqual(wds.norm_token("="), "")

    def test_ligature_folds(self):
        self.assertEqual(wds.norm_token("signiﬁcant"), "significant")

    def test_clean_pdf_text_unifies_hyphens_and_drops_soft_hyphen(self):
        self.assertEqual(wds.clean_pdf_text("self‐report"), "self-report")
        self.assertEqual(wds.clean_pdf_text("comput\xading"), "computing")


class TestBuildPdfTokens(unittest.TestCase):
    def test_line_end_hyphen_joins_next_line_in_same_block(self):
        page = [word(10, 10, "renal", 0, 0), word(50, 10, "out-", 0, 0),
                word(10, 22, "comes", 0, 1), word(50, 22, "found", 0, 1)]
        toks = wds.build_pdf_tokens([page])
        self.assertEqual([t.norm for t in toks], ["renal", "outcomes", "found"])
        self.assertEqual(len(toks[1].parts), 2)          # one box per printed half
        self.assertEqual([p[0] for p in toks[1].parts], [0, 1])

    def test_hyphen_not_joined_across_blocks_or_mid_line(self):
        page = [word(10, 10, "well-", 0, 0), word(50, 10, "known", 1, 0),
                word(10, 30, "x-", 2, 0), word(50, 30, "ray", 2, 0)]
        self.assertEqual([t.norm for t in wds.build_pdf_tokens([page])],
                         ["well", "known", "x", "ray"])

    def test_punctuation_only_words_dropped_and_pages_numbered(self):
        toks = wds.build_pdf_tokens([[word(0, 0, "alpha")], [word(0, 0, "–"), word(40, 0, "beta")]])
        self.assertEqual([(t.norm, t.page) for t in toks], [("alpha", 0), ("beta", 1)])


class TestLocateSequence(unittest.TestCase):
    def test_exact_run_inside_longer_text(self):
        hay = norms("unrelated opening words here " + TEXT + " and some closing words")
        needle = norms(TEXT)
        pairs = wds.locate_sequence(needle, hay)
        self.assertEqual(len(pairs), len(needle))
        self.assertEqual(pairs[0], (0, 4))
        self.assertEqual(pairs[-1], (len(needle) - 1, 4 + len(needle) - 1))

    def test_dropped_running_header_is_skipped_not_matched(self):
        # The PDF has a header in the middle of the passage that the library text dropped.
        words = TEXT.split()
        hay = norms(" ".join(words[:8] + ["Journal", "of", "Imaginary", "Medicine", "Vol", "3"]
                             + words[8:]))
        pairs = wds.locate_sequence(norms(TEXT), hay)
        self.assertEqual(len(pairs), len(words))
        hay_js = {j for _, j in pairs}
        self.assertFalse(hay_js & set(range(8, 14)))      # no header word was claimed

    def test_words_split_differently_still_placed(self):
        # library: "p = 0.001)," -- PDF: one word "p=0.001),"
        needle = norms("interval 2.42 p = 0.001), (2) white cell count high")
        hay = norms("interval 2.42 p=0.001), (2) white cell count high")
        pairs = wds.locate_sequence(needle, hay)
        self.assertEqual(len(pairs), len(needle))
        self.assertEqual([j for i, j in pairs if needle[i] in ("p", "0001")], [2, 2])

    def test_nothing_shared_is_empty(self):
        self.assertEqual(wds.locate_sequence(norms(TEXT), norms("entirely different words " * 5)), [])
        self.assertEqual(wds.locate_sequence([], norms(TEXT)), [])
        self.assertEqual(wds.locate_sequence(norms(TEXT), []), [])

    def test_repeated_boilerplate_prefers_expected_position(self):
        filler = " ".join(f"filler{i}" for i in range(200))
        hay = norms(f"{TEXT} {filler} {TEXT}")
        n = len(norms(TEXT))
        second = n + 200
        pairs = wds.locate_sequence(norms(TEXT), hay, expected_start=second)
        self.assertEqual(pairs[0][1], second)
        pairs = wds.locate_sequence(norms(TEXT), hay, expected_start=0)
        self.assertEqual(pairs[0][1], 0)

    def test_stray_single_word_match_is_dropped(self):
        # The needle's last word "dialysis" also appears alone further on; only the real run counts.
        needle = norms("alpha bravo charlie delta echo foxtrot golf hotel zulu dialysis")
        hay = norms("alpha bravo charlie delta echo foxtrot golf hotel x y z w dialysis")
        pairs = wds.locate_sequence(needle, hay)
        self.assertEqual([i for i, _ in pairs], list(range(8)))

    def test_resplit(self):
        self.assertEqual(wds._resplit(["p", "0001"], ["p0001"], 5, 9), [(5, 9), (6, 9)])
        self.assertEqual(wds._resplit(["ab"], ["a", "b"], 0, 0), [(0, 0)])
        self.assertEqual(wds._resplit(["ab"], ["ac"], 0, 0), [])


def toks_from(page_words):
    return wds.build_pdf_tokens([page_words])


class TestLineHighlights(unittest.TestCase):
    def setUp(self):
        self.toks = toks_from([word(10, 10, "a", 0, 0), word(50, 10, "b", 0, 0),
                               word(90, 10, "c", 0, 0), word(10, 22, "d", 0, 1),
                               word(50, 22, "e", 0, 1)])

    def test_consecutive_words_on_a_line_merge(self):
        hl = wds.line_highlights(self.toks, [(0, False), (1, False), (2, False), (3, False)])
        self.assertEqual(len(hl), 2)
        self.assertEqual(hl[0].rect, (10, 10, 120, 20))
        self.assertEqual(hl[1].rect, (10, 22, 40, 32))

    def test_unplaced_word_breaks_the_stretch(self):
        hl = wds.line_highlights(self.toks, [(0, False), (2, False)])
        self.assertEqual([h.rect for h in hl], [(10, 10, 40, 20), (90, 10, 120, 20)])

    def test_diff_word_gets_its_own_rect(self):
        hl = wds.line_highlights(self.toks, [(0, False), (1, True), (2, False)])
        self.assertEqual([h.diff for h in hl], [False, True, False])

    def test_hyphenated_word_highlights_both_halves(self):
        toks = toks_from([word(10, 10, "out-", 0, 0), word(10, 22, "comes", 0, 1)])
        hl = wds.line_highlights(toks, [(0, False)])
        self.assertEqual([(h.line, h.rect) for h in hl], [(0, (10, 10, 40, 20)), (1, (10, 22, 40, 32))])


def hl(page, block, x0, y0, x1, y1, line=0):
    return Highlight(page, block, line, (x0, y0, x1, y1), False)


class TestGroupRegions(unittest.TestCase):
    LEFT, RIGHT = (50, 50, 290, 700), (310, 50, 550, 700)

    def test_wrap_within_a_column_is_one_region(self):
        # Starts mid-line at the right of the column, wraps to a short stretch at the left: the
        # two highlights barely overlap, but they're one block, so one crop.
        ext = {(0, 1): self.LEFT}
        regs = wds.group_regions([hl(0, 1, 200, 100, 290, 110), hl(0, 1, 50, 112, 90, 122, 1)], ext)
        self.assertEqual(len(regs), 1)

    def test_column_change_and_page_change_split(self):
        ext = {(0, 1): self.LEFT, (0, 2): self.RIGHT, (1, 3): self.LEFT}
        regs = wds.group_regions([hl(0, 1, 50, 600, 290, 690), hl(0, 2, 310, 50, 550, 200),
                                  hl(1, 3, 50, 60, 290, 120)], ext)
        self.assertEqual([r["page"] for r in regs], [0, 0, 1])

    def test_next_paragraph_in_same_column_stays(self):
        ext = {(0, 1): (50, 100, 290, 200), (0, 2): (50, 210, 290, 300)}
        regs = wds.group_regions([hl(0, 1, 50, 100, 290, 200), hl(0, 2, 50, 210, 290, 300)], ext)
        self.assertEqual(len(regs), 1)

    def test_big_vertical_gap_splits(self):
        ext = {(0, 1): (50, 100, 290, 150), (0, 2): (50, 400, 290, 450)}
        regs = wds.group_regions([hl(0, 1, 50, 100, 290, 150), hl(0, 2, 50, 400, 290, 450)], ext)
        self.assertEqual(len(regs), 2)


class TestCropGeometry(unittest.TestCase):
    PAGE = (0, 0, 600, 800)

    def test_crop_widens_to_block_and_adds_context(self):
        region = {"rect": (120, 300, 200, 320), "items": [hl(0, 4, 120, 300, 200, 320)]}
        clip = wds.crop_rect(region, self.PAGE, {(0, 4): (60, 280, 290, 400)}, context=20, pad=5)
        self.assertEqual(clip, (55, 280, 295, 340))

    def test_crop_clamps_to_page(self):
        region = {"rect": (2, 5, 598, 795), "items": [hl(0, 0, 2, 5, 598, 795)]}
        self.assertEqual(wds.crop_rect(region, self.PAGE, {}, context=20, pad=8), self.PAGE)

    def test_block_extents(self):
        toks = toks_from([word(10, 10, "a", 3, 0), word(100, 40, "b", 3, 1)])
        self.assertEqual(wds.block_extents(toks), {(0, 3): (10, 10, 130, 50)})

    def test_snap_moves_edges_off_cut_lines(self):
        words = [(50, 95, 100, 107), (50, 305, 100, 317), (400, 95, 450, 107)]
        clip = wds.snap_clip((40, 100, 200, 310), words, keep=(50, 150, 200, 250))
        self.assertEqual(clip, (40, 107, 200, 305))

    def test_snap_never_cuts_into_keep(self):
        clip = wds.snap_clip((40, 100, 200, 310), [(50, 95, 100, 160)], keep=(50, 120, 200, 250))
        self.assertEqual(clip[1], 120)


class TestTitleRect(unittest.TestCase):
    def setUp(self):
        self.page_rects = [(0, 0, 600, 800), (0, 0, 600, 800)]
        p0 = [word(100, 60, "Journal", 0, 0), word(140, 60, "masthead", 0, 0),
              word(100, 120, "Renal", 1, 0, w=60, h=20), word(170, 120, "Outcomes", 1, 0, w=90, h=20),
              word(100, 145, "After", 1, 1, w=60, h=20), word(170, 145, "Snakebite", 1, 1, w=90, h=20),
              word(100, 190, "Jane", 2, 0), word(140, 190, "Quillfeather,", 2, 0, w=80),
              word(230, 190, "Omar", 2, 0), word(270, 190, "Brightwater", 2, 0, w=200),
              word(100, 600, "Body", 3, 0)]
        self.toks = wds.build_pdf_tokens([p0, [word(100, 100, "Quillfeather", 0, 0)]])

    def test_title_and_byline_in_clip(self):
        page, box, clip = wds.title_rect(self.toks, "Renal Outcomes after Snakebite",
                                         ["Quillfeather, Jane", "Omar Brightwater"],
                                         self.page_rects, context=10)
        self.assertEqual(page, 0)
        self.assertEqual(box[1], 120)                     # starts at the title, not the masthead
        self.assertEqual(box[3], 200)                     # down to the byline
        self.assertEqual(box[2], 470)                     # the whole byline block wide
        self.assertEqual(clip, (90, 110, 480, 210))

    def test_unplaceable_title_is_none(self):
        self.assertIsNone(wds.title_rect(self.toks, "A Completely Different Heading Altogether",
                                         [], self.page_rects))
        self.assertIsNone(wds.title_rect([], "Renal Outcomes", [], self.page_rects))

    def test_run_together_title_still_clips_from_its_first_line(self):
        # only "after Snakebite" matches (second line); the clip still starts at the title's first line
        page, box, _ = wds.title_rect(self.toks, "RenalOutcomes after Snakebite", [], self.page_rects)
        self.assertEqual((page, box[1]), (0, 120))

    def test_title_markup_ignored(self):
        _, box, _ = wds.title_rect(self.toks, "<i>Renal</i> Outcomes after Snakebite", [], self.page_rects)
        self.assertEqual(box[1], 120)


class TestTitleHtml(unittest.TestCase):
    def test_markup(self):
        from write_dupe_reports_html import title_html, title_text
        self.assertEqual(title_html("Resistant <i>S. aureus</i> &amp; <b>x</b> a<b"),
                         "Resistant <i>S. aureus</i> &amp; x a&lt;b")
        self.assertEqual(title_html("<script>alert(1)</script>"), "alert(1)")
        self.assertEqual(title_text("Resistant <i>S. aureus</i> &amp; milk"), "Resistant S. aureus & milk")


class TestZoom(unittest.TestCase):
    """The zoom exists so both sides of an exhibit show text at the same size, whatever each
    paper's own font size; these pin that down, and that fitting the column never breaks it."""

    def test_text_height_is_the_median_word_box(self):
        rects = [(0, 0, 10, 10), (0, 0, 10, 10), (0, 0, 10, 30), (0, 0, 10, 9)]
        self.assertEqual(wds.text_height_pt(rects), 10)
        self.assertIsNone(wds.text_height_pt([]))

    def test_different_font_sizes_reach_the_same_target(self):
        (s1, s2), shrink = wds.fit_scales([(10.0, 200), (12.5, 200)], target_px=15, column_px=500)
        self.assertEqual(shrink, 1.0)
        self.assertAlmostEqual(10.0 * s1, 15)
        self.assertAlmostEqual(12.5 * s2, 15)

    def test_too_wide_shrinks_both_sides_equally(self):
        (s1, s2), shrink = wds.fit_scales([(10.0, 600), (10.0, 100)], target_px=15, column_px=450)
        self.assertAlmostEqual(shrink, 0.5)
        self.assertAlmostEqual(600 * s1, 450)            # the wide one now fits exactly
        self.assertAlmostEqual(10.0 * s1, 10.0 * s2)     # and text is still equal on both

    def test_fallback_side_has_no_scale(self):
        scales, _ = wds.fit_scales([(None, None), (10.0, 200)], target_px=15, column_px=500)
        self.assertIsNone(scales[0])
        self.assertAlmostEqual(scales[1], 1.5)

    def test_layout_stacks_only_when_side_by_side_would_shrink_too_far(self):
        fits = [(10.0, 300), (10.0, 300)]                # 14px target -> 420px, fits 512
        _, col, stacked = wds.choose_layout(fits, target_px=14)
        self.assertEqual((col, stacked), (wds.COLUMN_PX, False))
        wide = [(10.0, 540), (10.0, 300)]                # 756px -> shrink 0.68, below 0.8
        scales, col, stacked = wds.choose_layout(wide, target_px=14)
        self.assertEqual((col, stacked), (wds.WIDE_COLUMN_PX, True))
        self.assertAlmostEqual(10.0 * scales[0], 14)     # full size again once stacked


class TestPagesLabel(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(wds._pages_label([2]), "PDF p. 3")
        self.assertEqual(wds._pages_label([4, 2, 3]), "PDF pp. 3&ndash;5")
        self.assertEqual(wds._pages_label([]), "")


class TestSelectPassages(unittest.TestCase):
    def run_(self, text, a, b):
        from compare_two_papers import find_shingle_matches  # noqa: F401  (ShingleRun lives there)
        from types import SimpleNamespace
        n = len(text.split())
        return SimpleNamespace(text_a=text, text_b=text, length=n, start_a=a, end_a=a + n, start_b=b, end_b=b + n)

    def test_longest_readable_no_repeats(self):
        prose = "the milk was collected from farms in the area and taken to the laboratory for analysis"
        table = "Mean 4.81 (1.00) 4.60 (1.03) 4.93 (0.97) balance 4.87 (0.93) 4.91 (0.89) 4.87 (0.95) x y"
        runs = [self.run_(table, 0, 0),                                   # longest, but a table row
                self.run_(prose + " today", 100, 100),
                self.run_(prose + " today", 300, 400),                    # the same sentence printed again
                self.run_("a different sentence about the results of the study", 500, 600),
                self.run_("words overlapping the second run on side a only here", 105, 900)]
        chosen = wds.select_passages(runs, 5)
        self.assertEqual([(r.start_a, r.start_b) for r in chosen], [(100, 100), (500, 600)])
        self.assertEqual(len(wds.select_passages(runs, 1)), 1)
        self.assertFalse(wds.readable_run(table))
        self.assertTrue(wds.readable_run("in 2010 the 28 samples were taken"))


class TestComposeShot(unittest.TestCase):
    """compose_shot(): each side's crops stacked, the two sides next to each other."""

    @staticmethod
    def jpeg(w, h):
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
        pix.clear_with(200)
        return pix.tobytes("jpeg")

    def test_layout(self):
        import tempfile
        kept = [("r01-a-1", self.jpeg(100, 40), 100, 40), ("r01-a-2", self.jpeg(80, 30), 80, 30),
                ("r01-b-1", self.jpeg(120, 50), 120, 50)]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x.jpg"
            w, h = wds.compose_shot(kept, ("EARLIER", "LATER"), out, gap=10, pad=5, label_px=20)
            self.assertTrue(out.read_bytes().startswith(b"\xff\xd8"))
        self.assertEqual(w, 5 + 100 + 10 + 120 + 5)
        self.assertEqual(h, 5 * 2 + 20 + 5 + (40 + 5 + 30))  # the taller (stacked) side

    def test_narrow_crop_widens_to_its_label(self):
        # GI-110: a narrow left crop let its "EARLIER (..., PDF p. 3)" label run into the right one's.
        import pymupdf
        import tempfile
        label = "EARLIER (2005, doi:10.4103/0019-5359.18965), PDF p. 3"
        label_w = pymupdf.get_text_length(label, fontname="helv", fontsize=15)
        kept = [("r01-a-1", self.jpeg(40, 40), 40, 40), ("r01-b-1", self.jpeg(120, 50), 120, 50)]
        with tempfile.TemporaryDirectory() as d:
            w, _ = wds.compose_shot(kept, (label, "LATER"), Path(d) / "x.jpg", gap=10, pad=5, label_px=20)
        self.assertGreaterEqual(w, 5 + label_w + 10 + 120 + 5)

    def test_one_side_and_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            w, _ = wds.compose_shot([("r01-b-1", self.jpeg(60, 20), 60, 20)], ("A", "B"), Path(d) / "x.jpg",
                                    gap=10, pad=5)
            self.assertEqual(w, 5 + 60 + 5)
            self.assertIsNone(wds.compose_shot([], ("A", "B"), Path(d) / "y.jpg"))


class TestRotatedPage(unittest.TestCase):
    """GI-110's earlier PDF is /Rotate 270: get_text() boxes come back unrotated, so every crop of
    it was cut from the wrong place until page_words() moved them into the displayed page."""

    def setUp(self):
        import pymupdf
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=595, height=842)
        self.page.set_rotation(270)
        # Reads left to right at (100, 100) on the page as displayed.
        self.page.insert_text(pymupdf.Point(100, 100) * self.page.derotation_matrix,
                              "hello wide world", fontsize=12, rotate=270)

    def test_words_are_in_displayed_coordinates(self):
        words = wds.page_words(self.page)
        self.assertEqual([w[4] for w in words], ["hello", "wide", "world"])
        x0, y0, x1, y1 = words[0][:4]
        self.assertAlmostEqual(x0, 100, delta=1)
        self.assertLess(y0, 100)
        self.assertGreater(y1, 100)
        self.assertGreater(x1 - x0, y1 - y0)          # horizontal, not sideways
        self.assertLess(words[0][2], words[1][0])     # and in reading order along x

    def test_highlight_lands_on_the_word(self):
        import pymupdf
        rect = pymupdf.Rect(wds.page_words(self.page)[0][:4])
        a = self.page.add_highlight_annot(rect.quad * self.page.derotation_matrix)
        shown = a.rect * self.page.rotation_matrix
        self.assertGreater((shown & rect).get_area(), 0.8 * rect.get_area())


if __name__ == "__main__":
    unittest.main()
