#!/usr/bin/env python3
"""Write a two-paper overlap page whose exhibits are images of the actual PDFs, with the
overlapping words highlighted on the page, instead of text diffs.

The default page format for the General Index pages (gi_write_pages.py; --text-pages there
for the old text diffs). The top of the
page -- heading, paper cards, --source-note (the General Index "Overlap evidence" block), summary
bar -- is that module's render_case(), unchanged; only the exhibits differ:

  1. the title/author area of each paper's first page, the two side by side;
  2. per shingle run (same find_shingle_matches() runs, same selection and display order as the
     text page's exhibits), a crop of each PDF around the passage, side by side, with every
     matched word highlighted (yellow; a word substituted or inserted/deleted inside the run in
     pink, the same distinction the text page draws).

The hard part is that a run is a span of WORD POSITIONS in the library's extracted text, and that
text is several transformations away from the PDF: extract_papers.py drops headers, footers and
short fragments, dehyphenates line-wrapped words, NFKC-folds ligatures and joins blocks into
paragraphs. Positions therefore don't map onto the PDF's own word list 1:1. So each side of a run
is located independently, by content: the run's words are normalised (lowercased, punctuation
stripped) and looked up in the PDF's own word stream (PyMuPDF page.get_text("words"), with
line-end hyphenation undone the way extract_papers.py undoes it) -- k-gram hits vote on an
offset, and difflib aligns the run against the window that offset points at (locate_sequence()).
Each placed word carries its own bounding box, which is what gets highlighted and cropped.

A run that can't be placed (no text layer, a scanned PDF, text the PDF renders differently from
the extraction) falls back to its text excerpt with a note, for that side only; a missing PDF
does the same for every exhibit on that side. Nothing about one run can stop the page being
written.

Every crop is zoomed from its own measured text size, so text reads at about the same size in
every image (--text-px), whatever each paper's font size or column width; an exhibit too wide to
show side by side at that size stacks its two sides full width instead (fit_scales(),
choose_layout()).

Images are written as separate JPEG files under OUT_DIR/img/<case>/ and referenced relatively
(--embed-images inlines them as data: URIs instead): a 50-exhibit page carries 100+ crops, and
separate files let the browser lazy-load them and let a reader open one full size.
--text-exhibits keeps only the title/byline images and shows the passages as text diffs instead
(general_index/gi_pubpeer.py's shareable pages).

    python3 write_dupe_reports_screenshots.py --library-db LIB --paper-ids 80,76 \\
        --out-dir general_index/data/screenshot_pages_trial --neutral --shingle-size 10 \\
        --x-drop 3 --case-number GI-02 --source-note NOTE --found-by ""
"""

import argparse
import base64
import difflib
import html
import math
import os
import re
import sqlite3
import unicodedata
from bisect import bisect_right
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from compare_two_papers import (DEFAULT_GAP_EXTEND, DEFAULT_MAX_GAP, DegenerateGappedPair,
                                 drop_furniture_runs, find_shingle_matches, load_paper_words)
from write_dupe_reports_html import (DEFAULT_MAX_SHINGLE_EXHIBITS, DEFAULT_MIN_SHINGLE_MATCHES,
                                     DEFAULT_SHINGLE_SIZE, render_case, resolve_earlier_id)

DEFAULT_TEXT_PX = 14       # target on-screen height of a word box (about a 12px font), in CSS px.
# Every crop is zoomed to this from its own measured text size, so a 9pt two-column paper and a
# 12pt single-column one read at the same size side by side -- see fit_scales()
COLUMN_PX = 512            # inner width of one .passage at the page's 1180px max width (1180 - 2x24
# page padding - 24 gap, halved, - 2x20 passage padding - border); a crop is sized as a share of it
WIDE_COLUMN_PX = 1090      # the same, for an exhibit whose two sides are stacked full width
STACK_BELOW = 0.8          # side by side, a crop that would show its text below this share of the
# target size (a full-page-width crop usually would) stacks the exhibit's two sides instead
DEFAULT_PIXEL_RATIO = 1.5  # image pixels per CSS px: sharper than 1:1 on a high-density screen
# without the file size of a full 2x render
MIN_SCALE, MAX_SCALE = 0.5, 4.0  # CSS px per PDF point; bounds a bad text-height measurement
DEFAULT_JPEG_QUALITY = 72  # text on white compresses well; below ~65 JPEG ringing shows round glyphs
DEFAULT_CONTEXT_PT = 26    # extra page height shown above and below the matched lines (about two
# lines of body text), so a crop shows where the passage sits rather than just the passage
CROP_PAD_PT = 8            # horizontal breathing room either side of the column
LOCATED_MIN = 0.8          # share of a run's words placed on the page to call that side located
PARTIAL_MIN = 0.3          # below this, the side falls back to its text excerpt
LOCATE_K = 3               # k-gram length that votes on where a run sits in the PDF
MAX_KGRAM_HITS = 40        # a k-gram occurring more often than this (running header, "of the
# same") says nothing about where a run is, and would flood the offset vote
REGION_GAP_PT = 150        # a vertical jump bigger than this within one column starts a new crop,
# so a figure sitting between two matched paragraphs isn't rendered as part of the exhibit
TITLE_BLOCK_MAX_PT = 150  # a text block taller than this isn't just a title (title_rect())
MATCH_RGB = (1.0, 0.86, 0.15)
DIFF_RGB = (1.0, 0.55, 0.55)


# --- pure helpers: tokens, location, highlight geometry -------------------------------------

def clean_pdf_text(text):
    """The character-level part of extract_papers.normalize_whitespace(), applied to one PDF word:
    ligatures folded, soft hyphens dropped, unicode hyphen variants made ASCII. Done before
    dehyphenation, in the same order extract_papers.py does it, so a wrapped word joins the
    same way on both sides."""
    text = unicodedata.normalize("NFKC", text).replace("\xad", "")
    return text.replace("‐", "-").replace("‑", "-")


def norm_token(word):
    """Matching form of one word: NFKC, lowercase, letters and digits only. Applied identically
    to the library's \\S+ tokens and to the PDF's words, so punctuation the two attach
    differently ("(2017)," vs "(2017),") can't stop a match. "" means the word is pure
    punctuation (a dash, a bullet) and takes no part in matching."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", word).lower() if ch.isalnum())


class PdfToken(NamedTuple):
    norm: str
    page: int
    block: int
    parts: tuple  # ((line_no, (x0, y0, x1, y1)), ...) -- two parts for a dehyphenated word


def build_pdf_tokens(pages):
    """Flatten per-page PyMuPDF word tuples (x0, y0, x1, y1, text, block, line, word) into one
    reading-order token stream, joining a line-end "comput-" with the next line's "ing" the way
    extract_papers.dehyphenate_and_join() does (same block, letter before the hyphen), and
    dropping pure-punctuation words. `pages` is a list of word lists, indexed by page number."""
    raw = [(pno, w[5], w[6], (w[0], w[1], w[2], w[3]), clean_pdf_text(w[4]))
           for pno, words in enumerate(pages) for w in words]
    tokens = []
    i = 0
    while i < len(raw):
        pno, blk, ln, rect, text = raw[i]
        parts = [(ln, rect)]
        while (text.endswith("-") and len(text) > 1 and text[-2].isalpha() and i + 1 < len(raw)
               and raw[i + 1][0] == pno and raw[i + 1][1] == blk and raw[i + 1][2] != ln):
            i += 1
            _, _, ln, rect2, nxt = raw[i]
            text = text[:-1] + nxt
            parts.append((ln, rect2))
        norm = norm_token(text)
        if norm:
            tokens.append(PdfToken(norm, pno, blk, tuple(parts)))
        i += 1
    return tokens


def build_kgram_index(norms, k=LOCATE_K):
    index = {}
    for j in range(len(norms) - k + 1):
        index.setdefault(tuple(norms[j:j + k]), []).append(j)
    return index


def locate_sequence(needle, hay, index=None, k=LOCATE_K, expected_start=None,
                    max_hits=MAX_KGRAM_HITS):
    """Where does the word sequence `needle` sit in `hay` (both lists of norm_token() strings)?
    Returns [(needle_i, hay_j), ...] for every needle word placed, in order -- possibly empty.

    Two steps. First, every k-gram of the needle found in the hay votes for an offset
    (hay_j - needle_i); the densest band of offsets, `width` wide, is where the run is. The band
    is generous (a quarter of the run) because the PDF has text the library dropped -- running
    headers, page numbers, footnotes -- so the offset drifts as a long run crosses pages. When
    several bands score within 10% of the best (the same boilerplate twice), the one nearest
    `expected_start` wins, if the caller knows roughly where the run should be. Second, difflib
    aligns the needle against the hay window that band points at. Its matching blocks are the
    placed words; a lone one-word block is kept only when matched blocks sit within two words of
    it on both sides, since a stray one-word match ("the") elsewhere in the window is noise.
    A replaced stretch whose words concatenate to the same string on both sides is placed too:
    PyMuPDF's word list and its block text don't always agree on where a space is, so the
    library can have "p = 0.001)," where the PDF has the one word "p=0.001),". Several needle
    words can then map to one hay word, so a hay index may repeat in the result.
    `index` is build_kgram_index(hay, k), passed in when the same hay is searched repeatedly."""
    n = len(needle)
    if not n or not hay:
        return []
    k = max(1, min(k, n))
    if index is None:
        index = build_kgram_index(hay, k)
    offsets = []
    for i in range(n - k + 1):
        hits = index.get(tuple(needle[i:i + k]))
        if hits and len(hits) <= max_hits:
            offsets.extend(j - i for j in hits)
    if not offsets:
        return []
    offsets.sort()
    width = max(20, n // 4 + 10)
    bands = []
    for lo in range(len(offsets)):
        hi = bisect_right(offsets, offsets[lo] + width)
        bands.append((hi - lo, lo, hi))
    best = max(c for c, _, _ in bands)
    good = [b for b in bands if b[0] >= 0.9 * best]
    if expected_start is not None:
        def dist(b):
            mid = (offsets[b[1]] + offsets[b[2] - 1]) / 2
            return abs(mid - expected_start)
        _, lo, hi = min(good, key=lambda b: (dist(b), -b[0]))
    else:
        _, lo, hi = max(good, key=lambda b: b[0])
    w_lo = max(0, offsets[lo] - 5)
    w_hi = min(len(hay), offsets[hi - 1] + n + 5)
    if w_hi <= w_lo:
        return []
    window = hay[w_lo:w_hi]
    sm = difflib.SequenceMatcher(None, needle, window, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    pairs = []
    for idx, b in enumerate(blocks):
        if b.size == 1:
            prev = blocks[idx - 1] if idx else None
            nxt = blocks[idx + 1] if idx + 1 < len(blocks) else None
            near_prev = prev and b.a - (prev.a + prev.size) <= 2 and b.b - (prev.b + prev.size) <= 2
            near_next = nxt and nxt.a - (b.a + 1) <= 2 and nxt.b - (b.b + 1) <= 2
            if not (near_prev and near_next):
                continue
        pairs.extend((b.a + t, w_lo + b.b + t) for t in range(b.size))
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace" and i2 - i1 <= 8 and j2 - j1 <= 8:
            pairs.extend((i, w_lo + j) for i, j in _resplit(needle[i1:i2], window[j1:j2], i1, j1))
    return sorted(pairs)


def _resplit(needle_part, hay_part, i0, j0):
    """Pair up two word lists that are the same characters split at different places, by
    character offset: each needle word maps to the hay word its first character falls in.
    [] when the concatenations differ (a genuinely different stretch)."""
    if "".join(needle_part) != "".join(hay_part):
        return []
    starts, pos = [], 0
    for w in hay_part:
        starts.append(pos)
        pos += len(w)
    out, pos = [], 0
    for i, w in enumerate(needle_part):
        out.append((i0 + i, j0 + bisect_right(starts, pos) - 1))
        pos += len(w)
    return out


class Highlight(NamedTuple):
    page: int
    block: int
    line: int
    rect: tuple   # (x0, y0, x1, y1), PDF points
    diff: bool    # a substituted/inserted/deleted word inside the run, not an identical one


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def line_highlights(tokens, placed):
    """One rectangle per stretch of consecutive placed words on one line, instead of one per
    word -- fewer, wider marks read as a highlighted line, the way a highlighter pen would.
    `placed` is [(hay_j, is_diff), ...] in hay order, each hay_j once. A stretch breaks at a line change, at a
    switch between identical and diff words, and at any unplaced word, so a word the run doesn't
    contain is never painted over."""
    out = []
    prev_j = None
    for j, diff in placed:
        tok = tokens[j]
        for n_part, (line, rect) in enumerate(tok.parts):
            last = out[-1] if out else None
            contiguous = (n_part > 0) or (prev_j is not None and j == prev_j + 1)
            if (last and contiguous and last.page == tok.page and last.block == tok.block
                    and last.line == line and last.diff == diff):
                out[-1] = last._replace(rect=_union(last.rect, rect))
            else:
                out.append(Highlight(tok.page, tok.block, line, tuple(rect), diff))
        prev_j = j
    return out


def _x_overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0])


def group_regions(highlights, extents=None, gap_pt=REGION_GAP_PT):
    """Split a run's highlights, in reading order, into crop regions: a new region starts on a
    page change, on a column change, or after a vertical jump of more than `gap_pt`. Each region
    becomes one image, so a run crossing a page or column break is shown as stacked crops in
    reading order rather than one crop of everything in between.

    Columns are compared by the width of the BLOCK each highlight sits in (`extents`, from
    block_extents()), not by the highlight itself: a passage that starts mid-line on the right
    and wraps to a short first stretch on the left is one column, though the two highlights
    barely overlap horizontally. A highlight's block counts as a different column when it shares
    less than 30% of the narrower width with the region's blocks, or when it starts above the
    line before it (reading order has gone back up the page: the next column)."""
    extents = extents or {}
    regions = []
    for h in highlights:
        col = extents.get((h.page, h.block), h.rect)
        cur = regions[-1] if regions else None
        if cur:
            ext = cur["cols"]
            last = cur["items"][-1].rect
            narrower = max(1.0, min(col[2] - col[0], ext[2] - ext[0]))
            same_block = (h.page, h.block) == (cur["items"][-1].page, cur["items"][-1].block)
            new_column = (not same_block
                          and (_x_overlap(col, ext) < 0.3 * narrower or h.rect[3] < last[1] - 2))
            if h.page != cur["page"] or new_column or h.rect[1] - cur["rect"][3] > gap_pt:
                cur = None
        if cur is None:
            regions.append({"page": h.page, "rect": h.rect, "cols": col, "items": [h]})
        else:
            cur["items"].append(h)
            cur["rect"] = _union(cur["rect"], h.rect)
            cur["cols"] = _union(cur["cols"], col)
    return regions


def block_extents(tokens):
    """(page, block) -> the union rect of every word in that block, i.e. the paragraph's own
    column width -- what a crop is widened to, so it shows the column and not just the words."""
    ext = {}
    for t in tokens:
        for _, rect in t.parts:
            key = (t.page, t.block)
            ext[key] = _union(ext[key], rect) if key in ext else tuple(rect)
    return ext


def crop_rect(region, page_rect, extents, context=DEFAULT_CONTEXT_PT, pad=CROP_PAD_PT):
    """Page-space clip for one region: as wide as the blocks its highlights sit in (plus `pad`),
    `context` points taller than the highlights above and below, clamped to the page."""
    x0, y0, x1, y1 = region["rect"]
    for h in region["items"]:
        b = extents.get((h.page, h.block))
        if b:
            x0, x1 = min(x0, b[0]), max(x1, b[2])
    return (max(page_rect[0], x0 - pad), max(page_rect[1], y0 - context),
            min(page_rect[2], x1 + pad), min(page_rect[3], y1 + context))


def snap_clip(clip, word_rects, keep):
    """Move a clip's top and bottom edges off any line of text they would slice through
    horizontally -- a half-line at the edge of a crop reads as damage. An edge cutting a word's
    box moves inward, past that line, but never inside `keep` (the area that must stay whole:
    the highlights, or the title block). `word_rects` is every word box on the page."""
    x0, y0, x1, y1 = clip
    for r in word_rects:
        if r[2] <= x0 or r[0] >= x1:
            continue
        if r[1] < y0 < r[3]:
            y0 = min(r[3], keep[1])
        if r[1] < y1 < r[3]:
            y1 = max(r[1], keep[3])
    return (x0, y0, x1, y1)


def title_rect(tokens, title, authors, page_rects, max_pages=3, context=24, author_band=220):
    """Clip for a paper's title-and-byline area: find the title's words among the first
    `max_pages` pages (a repository cover sheet can push the real first page to page 2), then
    extend down to the lowest author surname found within `author_band` points below it.
    Returns (page, text_rect, clip) or None when the title can't be placed (the catalogue title differs
    from the printed one, or there's no text layer) -- the caller then shows the top of page 1."""
    head = [t for t in tokens if t.page < max_pages]
    # markup tags out first: "<i>Staphylococcus" would otherwise match as "istaphylococcus"
    needle = [n for n in (norm_token(w) for w in re.sub(r"<[^>]+>", " ", title or "").split()) if n]
    pairs = locate_sequence(needle, [t.norm for t in head], k=2)
    if not needle or len(pairs) < 0.6 * len(needle):
        return None
    page = Counter(head[j].page for _, j in pairs).most_common(1)[0][0]
    rect = None
    for _, j in pairs:
        if head[j].page == page:
            for _, r in head[j].parts:
                rect = _union(rect, r) if rect else tuple(r)
    blocks = {head[j].block for _, j in pairs if head[j].page == page}
    # The whole of the title's own blocks: a catalogue title with words run together
    # ("Antibiotic-resistanceStaphylococcus aureusisolated ...") only matches from the first intact word
    # on, which put the clip's top edge partway down the printed title. Skipped for a block too tall to be
    # a title (one that runs on into the abstract), which would swallow the page.
    for blk in blocks:
        rs = [r for t in head if t.page == page and t.block == blk for _, r in t.parts]
        top, bottom = min(r[1] for r in rs), max(r[3] for r in rs)
        if bottom - top <= TITLE_BLOCK_MAX_PT:
            for r in rs:
                rect = _union(rect, r)
    surnames = {n for a in authors[:12] for n in (norm_token(w) for w in a.replace(",", " ").split())
                if len(n) >= 3}
    for t in head:
        if t.page == page and t.norm in surnames:
            for _, r in t.parts:
                if rect[1] <= r[1] <= rect[3] + author_band:
                    rect = _union(rect, r)
                    blocks.add(t.block)
    # Widen to the whole blocks involved, so a byline running on past the last surname matched
    # isn't cut off at the side.
    for t in head:
        if t.page == page and t.block in blocks:
            for _, r in t.parts:
                if r[1] >= rect[1] - 1 and r[3] <= rect[3] + 1:
                    rect = (min(rect[0], r[0]), rect[1], max(rect[2], r[2]), rect[3])
    pr = page_rects[page]
    return page, rect, (max(pr[0], rect[0] - context), max(pr[1], rect[1] - context),
                        min(pr[2], rect[2] + context), min(pr[3], rect[3] + context))


def text_height_pt(rects):
    """Median word-box height of `rects` -- the crop's typical text size in PDF points, robust to
    the odd heading or superscript among them. None when there are no words to measure."""
    hs = sorted(r[3] - r[1] for r in rects if r[3] > r[1])
    return hs[len(hs) // 2] if hs else None


def fit_scales(sides, target_px=DEFAULT_TEXT_PX, column_px=COLUMN_PX):
    """Display scale (CSS px per PDF point) for each side of one exhibit, so the text in every
    crop shows at about `target_px` regardless of the paper's own font size or how wide the crop
    is. `sides` is [(text_height_pt or None, widest_crop_pt or None), ...]; a side with nothing to
    measure (a text fallback) gets None.

    Each side is first scaled to the target on its own. If that makes either side's widest crop
    wider than the column, BOTH are shrunk by the same factor until it fits -- so the two sides
    stay the same text size as each other, which is the point, even where the page can't show
    them at the full target size (a full-width single-column crop usually can't).
    Returns (scales, shrink): `shrink` is that common factor, 1.0 when everything fit."""
    scales = []
    for text_h, _ in sides:
        scales.append(None if not text_h
                      else min(MAX_SCALE, max(MIN_SCALE, target_px / text_h)))
    widths = [sc * w for sc, (_, w) in zip(scales, sides) if sc and w]
    shrink = min(1.0, column_px / max(widths)) if widths else 1.0
    return [sc * shrink if sc else None for sc in scales], shrink


def choose_layout(sides, target_px=DEFAULT_TEXT_PX):
    """Side by side when both sides fit their half-width column at (nearly) the target text
    size; otherwise stacked, each side full width, which keeps a wide crop's text from shrinking
    to unreadable. Returns (scales, column_px, stacked)."""
    scales, shrink = fit_scales(sides, target_px, COLUMN_PX)
    if shrink >= STACK_BELOW:
        return scales, COLUMN_PX, False
    scales, _ = fit_scales(sides, target_px, WIDE_COLUMN_PX)
    return scales, WIDE_COLUMN_PX, True


# --- PDF access and image output -----------------------------------------------------------

def paper_pdf_path(conn, library_db, paper_id):
    """papers.file_path, which is usually relative to the library DB's own folder (as
    gi_write_pages.local_pdf() resolves it), sometimes to the working directory."""
    row = conn.execute("SELECT file_path FROM papers WHERE id=?", (paper_id,)).fetchone()
    if not row or not row[0]:
        return None
    p = Path(row[0])
    for cand in ([p] if p.is_absolute() else [Path(library_db).parent / p, Path.cwd() / p]):
        if cand.exists():
            return cand
    return None


BLOATED_BOX = 2.0   # word boxes this many times taller than the font size are the font's metrics, not the text


def page_words(page):
    """PyMuPDF's word tuples for one page. A word box is normally as tall as the font's ascender to
    descender, but some fonts declare absurd ones -- GI-85's later PDF sets 8-9pt text in 50pt-tall boxes,
    which made fit_scales() shrink that side fivefold and drew each highlight over five lines. On a page
    whose boxes run over BLOATED_BOX times the font size, the boxes are taken from the font size instead.

    Boxes are returned in the page as displayed (`page.rect`, what get_pixmap()'s clip takes). get_text()
    gives them unrotated, so on a /Rotate page (GI-110's earlier PDF: 270, landscape spreads) they'd be
    sideways and every crop taken from the wrong place; ImageWriter.render() maps highlights back."""
    import pymupdf
    m = page.rotation_matrix

    def displayed(ws):
        return ws if not page.rotation else [tuple(pymupdf.Rect(w[:4]) * m) + tuple(w[4:]) for w in ws]
    words = displayed(page.get_text("words"))
    pymupdf.TOOLS.set_small_glyph_heights(True)
    try:
        small = displayed(page.get_text("words"))
    finally:
        pymupdf.TOOLS.set_small_glyph_heights(False)
    if not words or len(small) != len(words):
        return words

    def median_h(ws):
        return sorted(w[3] - w[1] for w in ws)[len(ws) // 2]
    return small if median_h(words) > BLOATED_BOX * max(0.1, median_h(small)) else words


class PdfSide:
    """One paper's PDF, opened once per page: its token stream, k-gram index and block extents,
    plus `error` (why there's nothing to show) when the PDF is missing or has no text layer."""

    def __init__(self, path):
        self.path, self.doc, self.error = path, None, None
        self.tokens, self.index, self.extents = [], {}, {}
        if path is None:
            self.error = "no local PDF for this paper"
            return
        import pymupdf
        try:
            self.doc = pymupdf.open(path)
            pages = [page_words(page) for page in self.doc]
        except Exception as exc:  # a damaged PDF must not take the page down with it
            self.doc, self.error = None, f"PDF could not be read ({exc.__class__.__name__})"
            return
        self.tokens = build_pdf_tokens(pages)
        if not self.tokens:
            self.error = "PDF has no text layer (scanned?)"
            return
        self.norms = [t.norm for t in self.tokens]
        self.index = build_kgram_index(self.norms)
        self.extents = block_extents(self.tokens)
        self.page_rects = [tuple(p.rect) for p in self.doc]
        self.page_words = {}
        for t in self.tokens:
            self.page_words.setdefault(t.page, []).extend(r for _, r in t.parts)


class ImageWriter:
    """Renders page clips to JPEG, either as files under `img_dir` (returned as paths relative to
    the page) or as data: URIs. Highlights are drawn as real PDF highlight annotations on the
    in-memory document -- multiply-blended by MuPDF, so the glyphs stay crisp under the colour --
    and deleted straight after the render, so they never leak into another crop of that page.
    A clip is rendered at `scale` CSS px per point times `pixel_ratio`, and returned with its
    CSS size, so the page can show it at exactly the zoom fit_scales() chose."""

    def __init__(self, out_dir, img_dir, quality, embed, pixel_ratio=DEFAULT_PIXEL_RATIO):
        self.out_dir, self.img_dir = Path(out_dir), Path(img_dir)
        self.quality, self.embed, self.pixel_ratio = quality, embed, pixel_ratio
        self.bytes_written = 0
        self.count = 0
        self.kept = None  # a list: every render is also appended as (name, jpeg bytes, width, height)
        if not embed:
            self.img_dir.mkdir(parents=True, exist_ok=True)
            for old in self.img_dir.glob("*.jpg"):
                old.unlink()

    def render(self, side, page_no, clip, highlights, name, scale):
        import pymupdf
        page = side.doc[page_no]
        annots = []
        for h in highlights:
            # Annotations take unrotated coordinates; derotating the quad (not the rect) keeps its
            # corners in reading order, which is what the highlight's shape is drawn from.
            a = page.add_highlight_annot(pymupdf.Rect(h.rect).quad * page.derotation_matrix)
            a.set_colors(stroke=DIFF_RGB if h.diff else MATCH_RGB)
            a.update()
            annots.append(a)
        zoom = scale * self.pixel_ratio
        try:
            pix = page.get_pixmap(clip=pymupdf.Rect(clip), matrix=pymupdf.Matrix(zoom, zoom),
                                  alpha=False)
        finally:
            for a in annots:
                page.delete_annot(a)
        data = pix.tobytes("jpeg", jpg_quality=self.quality)
        self.bytes_written += len(data)
        self.count += 1
        if self.kept is not None:
            self.kept.append((name, data, pix.width, pix.height))
        if self.embed:
            src = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        else:
            path = self.img_dir / f"{name}.jpg"
            path.write_bytes(data)
            src = os.path.relpath(path, self.out_dir)
        return src, pix.width, pix.height, (clip[2] - clip[0]) * scale


def _img_html(src, w, h, css_w, alt, embed, column_px=COLUMN_PX):
    """`width`/`height` are the pixel size (so the browser reserves the right aspect ratio before
    the lazy image loads); the displayed width is a share of the column rather than a fixed px
    value, so on a narrower screen every crop shrinks by the same factor and text stays equal."""
    pct = min(100.0, 100.0 * css_w / column_px)
    img = (f'<img src="{html.escape(src, quote=True)}" width="{w}" height="{h}" '
           f'style="width:{pct:.1f}%" loading="lazy" alt="{html.escape(alt, quote=True)}">')
    return img if embed else f'<a href="{html.escape(src, quote=True)}">{img}</a>'


def _pages_label(pages):
    pages = sorted(set(p + 1 for p in pages))
    if not pages:
        return ""
    return f"PDF p. {pages[0]}" if len(pages) == 1 else f"PDF pp. {pages[0]}&ndash;{pages[-1]}"


EXTRA_STYLE = """
.shots { display: flex; flex-direction: column; gap: 0.35rem; }
.shots a { display: contents; }
.shots img { display: block; max-width: 100%; height: auto; border: 1px solid var(--border);
  background: #fff; }
.shot-break { font-family: var(--font-mono); font-size: 0.7rem; color: var(--ink-muted);
  margin: 0.15rem 0; text-align: center; }
.locate-note { font-size: 0.8rem; color: var(--ink-muted); margin: 0.55rem 0 0;
  border-left: 2px solid var(--border); padding-left: 0.6rem; }
.passage .shot-text { font-size: 0.92rem; margin-top: 0.5rem; }
.title-shots { margin-bottom: 2.75rem; }
.passage-pair.stacked { grid-template-columns: 1fr; gap: 1rem; }
.omitted-note { font-family: var(--font-mono); font-size: 0.8rem; color: var(--ink-muted);
  text-align: center; margin: 0 0 2.75rem; }
"""


def _fallback_html(reason, match_words):
    text = html.escape(" ".join(match_words))
    return (f'<p class="locate-note">{html.escape(reason)} &mdash; showing the extracted '
            f'text instead.</p><p class="shot-text"><mark class="match">{text}</mark></p>')


class ScreenshotExhibits:
    """Drop-in for write_dupe_reports_html.render_shingle_exhibits() (same call signature and
    return shape, handed to render_case() as `exhibit_renderer`), producing image exhibits.
    Also records per-run location outcomes in `self.outcomes` for the console summary.

    Each exhibit is built in two passes: plan both sides (locate, group into regions, compute
    clips and the text size inside them), then pick one zoom per side with fit_scales() so the
    two show text at the same size, then render."""

    def __init__(self, sides, writer, case_key, context_pt=DEFAULT_CONTEXT_PT,
                 text_px=DEFAULT_TEXT_PX):
        self.sides, self.writer, self.case_key = sides, writer, case_key
        self.context_pt, self.text_px = context_pt, text_px
        self.outcomes = []

    def _plan_side(self, side, words, start, match_words, diff_idx):
        """Locate one side of one run and lay out its crops. Returns a dict with `status`
        ('located', 'partial' or 'missing'), and either `html` (a text fallback) or `crops`
        [(page, clip, highlights), ...], `share` and `text_h` (median word height, points)."""
        if side.error:
            return {"status": "missing", "html": _fallback_html(side.error, match_words)}
        needle, needle_src = [], []
        for k, w in enumerate(match_words):
            n = norm_token(w)
            if n:
                needle.append(n)
                needle_src.append(k)
        expected = start / max(1, len(words)) * len(side.norms)
        try:
            pairs = locate_sequence(needle, side.norms, side.index, expected_start=expected)
        except Exception as exc:
            return {"status": "missing",
                    "html": _fallback_html(f"locating failed ({exc.__class__.__name__})", match_words)}
        share = len(pairs) / max(1, len(needle))
        if share < PARTIAL_MIN:
            return {"status": "missing", "html": _fallback_html(
                "This run could not be found in the PDF's text layer", match_words)}
        placed = {}
        for i, j in pairs:
            placed[j] = placed.get(j, False) or needle_src[i] in diff_idx
        placed = sorted(placed.items())
        crops = []
        for reg in group_regions(line_highlights(side.tokens, placed), side.extents):
            clip = crop_rect(reg, side.page_rects[reg["page"]], side.extents, context=self.context_pt)
            clip = snap_clip(clip, side.page_words.get(reg["page"], []), reg["rect"])
            crops.append((reg["page"], clip, reg["items"]))
        # Measured on the placed words themselves, one box per word: a line highlight's height is
        # its line's, so this is the size of the text being compared, not of the page's headings.
        text_h = text_height_pt([r for j, _ in placed for _, r in side.tokens[j].parts])
        return {"status": "located" if share >= LOCATED_MIN else "partial", "crops": crops,
                "share": share, "text_h": text_h}

    def _emit_side(self, side, plan, scale, name, role, match_words, column_px=COLUMN_PX):
        """HTML for a planned side at display scale `scale`. Returns (html, pages)."""
        if "html" in plan:
            return plan["html"], []
        shots, pages, prev = [], [], None
        for n_reg, (page_no, clip, items) in enumerate(plan["crops"], 1):
            try:
                src, w, h, css_w = self.writer.render(side, page_no, clip, items,
                                                      f"{name}-{n_reg}", scale)
            except Exception as exc:
                plan["status"] = "missing"
                return _fallback_html(f"rendering failed ({exc.__class__.__name__})", match_words), []
            if prev is not None:
                shots.append(f'<p class="shot-break">&darr; continues '
                             f'{"on PDF p. " + str(page_no + 1) if page_no != prev else "in the next column"}'
                             f'</p>')
            shots.append(_img_html(src, w, h, css_w, f"{role}, PDF page {page_no + 1}",
                                   self.writer.embed, column_px))
            pages.append(page_no)
            prev = page_no
        note = ""
        if plan["status"] == "partial":
            note = (f'<p class="locate-note">Only {plan["share"]:.0%} of this run\'s words could be '
                    f'placed on the page; the rest are not highlighted.</p>')
        return f'<div class="shots">{"".join(shots)}</div>{note}', pages

    def __call__(self, conn, paper_id_1, paper_id_2, earlier_id, shingle_size, max_exhibits,
                 mismatch_penalty=1, x_drop=None, gap_open=None, gap_extend=DEFAULT_GAP_EXTEND,
                 max_gap=DEFAULT_MAX_GAP, drop_furniture=False, runs=None):
        """`runs`: render these runs (already chosen, longest first) instead of scanning."""
        words_1 = load_paper_words(conn, paper_id_1)
        words_2 = load_paper_words(conn, paper_id_2)
        if runs is None:
            runs = find_shingle_matches(words_1, words_2, shingle_size=shingle_size,
                                         mismatch_penalty=mismatch_penalty, x_drop=x_drop,
                                         gap_open=gap_open, gap_extend=gap_extend, max_gap=max_gap)
        furniture = []
        if drop_furniture:
            runs, furniture = drop_furniture_runs(runs, words_1, words_2, shingle_size)
        # Same selection (longest N) and display order (the later/flagged paper's reading order)
        # as render_shingle_exhibits(), so the two pages' "match N" refer to the same runs.
        later_is_paper_2 = (earlier_id != paper_id_2)
        shown = sorted(runs[:max_exhibits], key=lambda r: r[7] if later_is_paper_2 else r[5])
        swap = earlier_id == paper_id_2
        exhibits = []
        for i, run in enumerate(shown, 1):
            match_1, match_2 = run.text_a.split(), run.text_b.split()
            # Which words differ between the two sides, per side -- position-aligned while the
            # run is lockstep, difflib-aligned once an indel makes the sides different lengths
            # (the same rule, for the same reason, as render_shingle_exhibits()).
            if len(match_1) == len(match_2):
                diff_1 = diff_2 = {k for k, (a, b) in enumerate(zip(match_1, match_2))
                                   if a.lower() != b.lower()}
            else:
                diff_1, diff_2 = set(), set()
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                        None, [w.lower() for w in match_1], [w.lower() for w in match_2],
                        autojunk=False).get_opcodes():
                    if tag != "equal":
                        diff_1.update(range(i1, i2))
                        diff_2.update(range(j1, j2))
            left = (paper_id_1, words_1, run.start_a, match_1, diff_1)
            right = (paper_id_2, words_2, run.start_b, match_2, diff_2)
            if swap:
                left, right = right, left
            earlier_label = "EARLIER" if earlier_id else "PAPER A"
            later_label = "LATER" if earlier_id else "PAPER B"
            sides = ((self.sides[left[0]], left), (self.sides[right[0]], right))
            plans = [self._plan_side(side, words, s, mw, dset)
                     for side, (_, words, s, mw, dset) in sides]
            scales, column_px, stacked = choose_layout(
                [(pl.get("text_h"), max((c[2] - c[0] for _, c, _ in pl["crops"]), default=None)
                  if "crops" in pl else None) for pl in plans], target_px=self.text_px)
            cells, self.pages = [], []
            for tag, role, (side, (_, _, _, mw, _)), plan, scale in zip(
                    "ab", (earlier_label, later_label), sides, plans, scales):
                body, pages = self._emit_side(side, plan, scale, f"r{i:02d}-{tag}", role, mw,
                                              column_px)
                self.pages.append(pages)   # PDF page indexes, left side then right: for passage_shots()
                cls = "earlier" if tag == "a" else "later"
                cells.append(f'<div class="passage {cls}"><p class="label"><span>{role}</span>'
                             f'<span class="idx">{_pages_label(pages)}</span></p>{body}</div>')
            self.outcomes.append(tuple(pl["status"] for pl in plans))
            subs, indels = run.substitutions, run.indels
            exhibits.append(f"""
<section class="exhibit">
  <div class="exhibit-head">
    <h3>Shingle match {i} of {len(shown)}</h3>
    <div class="metrics"><span>{"near-exact run" if subs or indels else "exact run"} <b>{run.length} words</b></span>
      {f'<span>substitutions <b>{subs}</b></span>' if subs else ""}{f'<span>inserted/deleted <b>{indels}</b> ({run.end_a - run.start_a}w A vs {run.end_b - run.start_b}w B)</span>' if indels else ""}</div>
  </div>
  <div class="passage-pair{" stacked" if stacked else ""}">
    {cells[0]}
    {cells[1]}
  </div>
</section>""")
        omitted = len(runs) - len(shown)
        if exhibits and omitted > 0:
            # Appended to the last exhibit rather than added as its own list entry, so
            # render_case()'s "top N by length shown" count stays the number of exhibits.
            exhibits[-1] += (f'\n<p class="omitted-note">{omitted} shorter run'
                             f'{"s" if omitted != 1 else ""} not shown (--max-exhibits '
                             f'{max_exhibits}).</p>')
        return exhibits, len(runs), len(furniture)


def render_title_shots(conn, sides, writer, paper_id_1, paper_id_2, earlier_id,
                       text_px=DEFAULT_TEXT_PX):
    """The side-by-side title/byline images that sit above the exhibits, in the same left/right
    order as the paper cards. A title that can't be placed falls back to the top 40% of the
    first page (labelled as such); a side with no usable PDF says why. Zoomed the same way as
    the exhibits, measured on every word inside the clip (mostly byline and affiliation text,
    so the much larger title type doesn't set the scale)."""
    from write_dupe_reports_html import paper_authors
    order = [paper_id_2, paper_id_1] if earlier_id == paper_id_2 else [paper_id_1, paper_id_2]
    labels = ("EARLIER", "LATER") if earlier_id else ("PAPER A", "PAPER B")
    plans = []
    for pid in order:
        side = sides[pid]
        if side.error:
            plans.append(None)
            continue
        title = conn.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()[0]
        found = title_rect(side.tokens, title, paper_authors(conn, pid), side.page_rects)
        note = ""
        if found:
            page_no, text_box, clip = found
            clip = snap_clip(clip, side.page_words.get(page_no, []), text_box)
        else:
            page_no = 0
            pr = side.page_rects[0]
            clip = (pr[0], pr[1], pr[2], pr[1] + 0.4 * (pr[3] - pr[1]))
            note = ('<p class="locate-note">The catalogue title wasn\'t found on the page, so '
                    'this is the top of the first page.</p>')
        inside = [r for r in side.page_words.get(page_no, [])
                  if r[0] >= clip[0] and r[2] <= clip[2] and r[1] >= clip[1] and r[3] <= clip[3]]
        plans.append((page_no, clip, note, text_height_pt(inside)))
    scales, column_px, stacked = choose_layout(
        [(pl[3], pl[1][2] - pl[1][0]) if pl else (None, None) for pl in plans], target_px=text_px)
    cells = []
    for tag, pid, label, plan, scale in zip("ab", order, labels, plans, scales):
        side = sides[pid]
        cls = "earlier" if tag == "a" else "later"
        if plan is None:
            cells.append(f'<div class="passage {cls}"><p class="label"><span>{label}</span></p>'
                         f'<p class="locate-note">{html.escape(side.error)}.</p></div>')
            continue
        page_no, clip, note, _ = plan
        try:
            src, w, h, css_w = writer.render(side, page_no, clip, [], f"title-{tag}",
                                             scale or 1.0)
            shot = _img_html(src, w, h, css_w, f"{label} title and authors", writer.embed,
                             column_px)
        except Exception as exc:
            shot, note = "", f'<p class="locate-note">Rendering failed ({exc.__class__.__name__}).</p>'
        cells.append(f'<div class="passage {cls}"><p class="label"><span>{label}</span>'
                     f'<span class="idx">PDF p. {page_no + 1}</span></p>'
                     f'<div class="shots">{shot}</div>{note}</div>')
    return f"""
<section class="exhibit title-shots">
  <div class="exhibit-head"><h3>Title and authors, as printed</h3></div>
  <div class="passage-pair{" stacked" if stacked else ""}">
    {cells[0]}
    {cells[1]}
  </div>
</section>"""


def compose_shot(kept, labels, out_path, gap=24, pad=12, label_px=22, quality=DEFAULT_JPEG_QUALITY):
    """One JPEG of one exhibit: each side's crops stacked top to bottom, the two sides next to each other
    (left = `labels[0]`), a label over each. `kept` is ImageWriter.kept for that exhibit: names r01-a-N /
    r01-b-N. Composed on a PyMuPDF page (the crops placed at their pixel size, then rendered 1:1), so it
    needs nothing beyond what renders the crops. Returns the image's (width, height), or None if neither
    side has an image."""
    import pymupdf
    cols = [[k for k in kept if k[0].split("-")[1] == tag] for tag in "ab"]
    if not any(cols):
        return None
    # A column is at least as wide as its label, or a narrow crop's label runs into the other side's.
    col_w = [max([w for _, _, w, _ in c] + [math.ceil(pymupdf.get_text_length(
                 label, fontname="helv", fontsize=label_px * 0.75))]) if c else 0
             for c, label in zip(cols, labels)]
    col_h = [sum(h for _, _, _, h in c) + pad * max(0, len(c) - 1) for c in cols]
    width = pad * 2 + col_w[0] + col_w[1] + (gap if all(col_w) else 0)
    height = pad * 2 + label_px + pad + max(col_h)
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    x = pad
    for col, w, label in zip(cols, col_w, labels):
        if not col:
            continue
        page.insert_text((x, pad + label_px * 0.8), label, fontsize=label_px * 0.75, fontname="helv")
        y = pad * 2 + label_px
        for _, data, iw, ih in col:
            page.insert_image(pymupdf.Rect(x, y, x + iw, y + ih), stream=data)
            y += ih + pad
        x += w + gap
    pix = page.get_pixmap(alpha=False)
    Path(out_path).write_bytes(pix.tobytes("jpeg", jpg_quality=quality))
    return pix.width, pix.height


def longest_shot(conn, sides, writer, case_key, pid1, pid2, earlier_id, args, out_path):
    """Render the longest run's two PDF crops (as an image exhibit would show them) and write them as one
    JPEG to `out_path`, for attaching to a PubPeer comment. Returns compose_shot()'s size, or None."""
    exhibits = ScreenshotExhibits(sides, writer, case_key, context_pt=args.context_pt, text_px=args.text_px)
    writer.kept = []
    try:
        exhibits(conn, pid1, pid2, earlier_id, args.shingle_size, 1, mismatch_penalty=args.mismatch_penalty,
                 x_drop=args.x_drop, gap_open=args.gap_open, gap_extend=args.gap_extend, max_gap=args.max_gap,
                 drop_furniture=args.drop_furniture)
        roles = ("EARLIER", "LATER") if earlier_id else ("PAPER A", "PAPER B")
        order = (pid2, pid1) if earlier_id == pid2 else (pid1, pid2)
        labels = []
        for role, pid in zip(roles, order):
            row = conn.execute("SELECT year, doi FROM papers WHERE id=?", (pid,)).fetchone()
            facts = [str(x) for x in (row[0], row[1] and f"doi:{row[1]}") if x] if row else []
            labels.append(role + (f" ({', '.join(facts)})" if facts else ""))
        return compose_shot(writer.kept, labels, out_path)
    finally:
        writer.kept = None


NUMERIC_SHARE = 0.5   # a run with more than this share of words containing digits is a table row


def readable_run(text):
    """False for a run that is mostly numbers (a table run together), which makes a poor exhibit."""
    words = text.split()
    return bool(words) and sum(any(c.isdigit() for c in w) for w in words) / len(words) <= NUMERIC_SHARE


def _norm_text(text):
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())


def select_passages(runs, n):
    """Up to n runs for PubPeer images: the longest first, skipping table rows (readable_run()), a run whose
    text repeats one already chosen (a sentence a paper prints several times gives one run per repeat), and
    a run overlapping a chosen one's span on either side."""
    chosen = []
    for r in sorted(runs, key=lambda r: -max(r.length, len(r.text_b.split()))):
        if len(chosen) >= n:
            break
        if not readable_run(r.text_b):
            continue
        if any(_norm_text(r.text_b) == _norm_text(c.text_b)
               or (r.start_a < c.end_a and c.start_a < r.end_a)
               or (r.start_b < c.end_b and c.start_b < r.end_b) for c in chosen):
            continue
        chosen.append(r)
    return chosen


def _side_label(conn, role, pid, pages=()):
    row = conn.execute("SELECT year, doi FROM papers WHERE id=?", (pid,)).fetchone()
    facts = [str(x) for x in (row[0], row[1] and f"doi:{row[1]}") if x] if row else []
    where = "" if not pages else f", PDF p. {pages[0]}" if len(pages) == 1 else f", PDF pp. {pages[0]}-{pages[-1]}"
    return role + (f" ({', '.join(facts)})" if facts else "") + where   # pages 1-based


def passage_shots(conn, sides, writer, case_key, pid1, pid2, earlier_id, args, out_dir, n):
    """One side-by-side JPEG per select_passages() run (earlier/PAPER A left), out_dir/passage-K.jpg, and
    out_dir/passages.json describing them: [{file, words, left: {doi, pages, text}, right: {...}}], pages
    1-based. A run neither PDF could place is left out. Returns that list."""
    import json
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    words_1, words_2 = load_paper_words(conn, pid1), load_paper_words(conn, pid2)
    runs = find_shingle_matches(words_1, words_2, shingle_size=args.shingle_size,
                                mismatch_penalty=args.mismatch_penalty, x_drop=args.x_drop,
                                gap_open=args.gap_open, gap_extend=args.gap_extend, max_gap=args.max_gap)
    if args.drop_furniture:
        runs, _ = drop_furniture_runs(runs, words_1, words_2, args.shingle_size)
    exhibits = ScreenshotExhibits(sides, writer, case_key, context_pt=args.context_pt, text_px=args.text_px)
    roles = ("EARLIER", "LATER") if earlier_id else ("PAPER A", "PAPER B")
    swap = earlier_id == pid2
    order = (pid2, pid1) if swap else (pid1, pid2)
    doi = {pid: (conn.execute("SELECT doi FROM papers WHERE id=?", (pid,)).fetchone() or [None])[0]
           for pid in order}
    out = []
    for run in select_passages(runs, n):
        writer.kept = []
        try:
            exhibits(conn, pid1, pid2, earlier_id, args.shingle_size, 1, runs=[run])
            pages = [[p + 1 for p in sorted(set(ps))] for ps in exhibits.pages]
            name = f"passage-{len(out) + 1}.jpg"
            labels = [_side_label(conn, role, pid, pg) for role, pid, pg in zip(roles, order, pages)]
            if not compose_shot(writer.kept, labels, out_dir / name):
                continue
        finally:
            writer.kept = None
        texts = (run.text_b, run.text_a) if swap else (run.text_a, run.text_b)
        out.append({"file": name, "words": max(run.length, len(run.text_b.split())),
                    **{key: {"doi": doi[pid], "pages": pg, "text": t}
                       for key, pid, pg, t in zip(("left", "right"), order, pages, texts)}})
    (out_dir / "passages.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def title_shot(conn, sides, writer, pid1, pid2, earlier_id, args, out_path):
    """Both papers' title and byline, as printed, side by side in one JPEG (earlier left). Returns its size,
    or None."""
    writer.kept = []
    try:
        render_title_shots(conn, sides, writer, pid1, pid2, earlier_id, text_px=args.text_px)
        roles = ("EARLIER", "LATER") if earlier_id else ("PAPER A", "PAPER B")
        order = (pid2, pid1) if earlier_id == pid2 else (pid1, pid2)
        return compose_shot(writer.kept, [_side_label(conn, r, pid) for r, pid in zip(roles, order)], out_path)
    finally:
        writer.kept = None


def parse_args():
    p = argparse.ArgumentParser(description="Write a two-paper overlap page with PDF screenshots "
                                            "as exhibits (see the module docstring).")
    p.add_argument("--library-db", type=Path, default=Path("library.sqlite3"),
                   help="opened read-only")
    p.add_argument("--paper-ids", required=True, help="'A,B' paper_id pair")
    p.add_argument("--out-dir", type=Path, default=Path("dupe_reports_screenshots"))
    p.add_argument("--title", help="short (2-4 word) <title> for the page")
    p.add_argument("--source-note", help="HTML snippet shown under the dek, as in write_dupe_reports_html.py")
    p.add_argument("--found-by", default=None, help="as in write_dupe_reports_html.py")
    p.add_argument("--classification", help="as in write_dupe_reports_html.py (ignored with --neutral)")
    p.add_argument("--case-number", help="prefixed onto the output filename, and names the image folder")
    p.add_argument("--neutral", action="store_true", help="as in write_dupe_reports_html.py")
    p.add_argument("--shingle-size", type=int, default=DEFAULT_SHINGLE_SIZE)
    p.add_argument("--x-drop", type=int, default=None)
    p.add_argument("--mismatch-penalty", type=int, default=1)
    p.add_argument("--gap-open", type=int, default=None)
    p.add_argument("--gap-extend", type=int, default=DEFAULT_GAP_EXTEND)
    p.add_argument("--max-gap", type=int, default=DEFAULT_MAX_GAP)
    p.add_argument("--drop-furniture", action="store_true", help="as in write_dupe_reports_html.py")
    p.add_argument("--max-exhibits", "--max-shingle-exhibits", dest="max_exhibits", type=int,
                   default=DEFAULT_MAX_SHINGLE_EXHIBITS,
                   help=f"cap on exhibits, keeping the longest runs (default: no cap, every run is shown); "
                        f"the page says how many were left out")
    p.add_argument("--min-shingle-matches", type=int, default=DEFAULT_MIN_SHINGLE_MATCHES,
                   help="write nothing if the scan found fewer runs than this, as in write_dupe_reports_html.py")
    p.add_argument("--no-shingle-count-in-title", action="store_true")
    p.add_argument("--text-px", type=float, default=DEFAULT_TEXT_PX,
                   help=f"on-screen height of a line of text in every crop, in CSS px (default "
                        f"{DEFAULT_TEXT_PX}); each crop is zoomed to this from its own text size, "
                        f"shrunk together with the other side only when a crop won't fit the column")
    p.add_argument("--pixel-ratio", type=float, default=DEFAULT_PIXEL_RATIO,
                   help=f"image pixels per CSS px (default {DEFAULT_PIXEL_RATIO}; 2 is sharper on "
                        f"a high-density screen, at about 1.8x the file size)")
    p.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    p.add_argument("--context-pt", type=float, default=DEFAULT_CONTEXT_PT,
                   help=f"page height shown above/below the matched lines, in points (default {DEFAULT_CONTEXT_PT})")
    p.add_argument("--embed-images", action="store_true",
                   help="inline the images as data: URIs (one self-contained file) instead of "
                        "writing them under OUT_DIR/img/")
    p.add_argument("--text-exhibits", action="store_true",
                   help="keep the title/byline images but show the shared passages as "
                        "write_dupe_reports_html.py's text diffs: a much smaller page (with "
                        "--embed-images, one file of a few hundred KB) for sharing, e.g. on PubPeer")
    p.add_argument("--whole-document", action="store_true",
                   help="with --text-exhibits: both papers end to end as one interleaved diff "
                        "(write_dupe_reports_html.py --whole-document) instead of one exhibit per run")
    p.add_argument("--no-title-shots", action="store_true",
                   help="leave out the title/byline images (gi_pubpeer.py --publisher: the publisher has the articles)")
    p.add_argument("--passage-shots", type=Path,
                   help="also write --passage-count passages (the longest of running text, no repeats) as "
                        "side-by-side JPEGs, passage-K.jpg, plus passages.json, into this folder (for PubPeer)")
    p.add_argument("--passage-count", type=int, default=4)
    p.add_argument("--title-shot", type=Path,
                   help="also write both papers' title and byline, side by side, as one JPEG to this path")
    p.add_argument("--longest-shot", type=Path,
                   help="also write the longest run's two PDF crops, side by side, as one JPEG to this path "
                        "(for attaching to a PubPeer comment)")
    return p.parse_args()


def main():
    args = parse_args()
    if args.gap_open is not None and args.x_drop is None:
        raise SystemExit("--gap-open needs --x-drop as well (see write_dupe_reports_html.py).")
    try:
        os.nice(10)  # rendering is CPU-bound and this machine has other work to do
    except OSError:
        pass
    conn = sqlite3.connect(f"file:{args.library_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # Sorted, as write_dupe_reports_html.py groups --paper-ids, so both pages name and orient
    # the pair the same way.
    pid1, pid2 = sorted(int(x) for x in args.paper_ids.split(","))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    case_key = args.case_number or f"{pid1}-{pid2}"
    sides = {pid: PdfSide(paper_pdf_path(conn, args.library_db, pid)) for pid in (pid1, pid2)}
    writer = ImageWriter(args.out_dir, args.out_dir / "img" / case_key, args.jpeg_quality,
                         args.embed_images, pixel_ratio=args.pixel_ratio)
    earlier_id = resolve_earlier_id(conn, pid1, pid2, [])
    exhibits = ScreenshotExhibits(sides, writer, case_key, context_pt=args.context_pt,
                                  text_px=args.text_px)
    try:
        slug, body, total = render_case(
            conn, pid1, pid2, [], source_note=args.source_note, found_by=args.found_by,
            short_title=args.title, shingle_size=args.shingle_size,
            max_shingle_exhibits=args.max_exhibits, classification=args.classification,
            mismatch_penalty=args.mismatch_penalty, x_drop=args.x_drop,
            include_shingle_count_in_title=not args.no_shingle_count_in_title,
            neutral=args.neutral, gap_open=args.gap_open, gap_extend=args.gap_extend,
            max_gap=args.max_gap, drop_furniture=args.drop_furniture,
            exhibit_renderer=None if args.text_exhibits else exhibits,
            whole_document=args.whole_document and args.text_exhibits,
            pre_exhibits_html="" if args.no_title_shots else render_title_shots(
                conn, sides, writer, pid1, pid2, earlier_id, text_px=args.text_px),
            extra_style=EXTRA_STYLE, generator="write_dupe_reports_screenshots.py",
        )
    except DegenerateGappedPair as exc:
        raise SystemExit(f"--gap-open refused -- {exc}")
    if total < args.min_shingle_matches:
        print(f"skipped {pid1}/{pid2}: {total} shingle runs, below --min-shingle-matches "
              f"{args.min_shingle_matches}")
        return
    filename = f"{args.case_number}-{slug}.html" if args.case_number else f"{slug}.html"
    out_path = args.out_dir / filename
    # NULs from a PDF's extracted text are invalid in HTML (gi_write_pages.py strips them too)
    out_path.write_text(body.replace("\x00", ""), encoding="utf-8")
    if args.longest_shot:
        size = longest_shot(conn, sides, writer, case_key, pid1, pid2, earlier_id, args, args.longest_shot)
        print(f"wrote {args.longest_shot}: {size[0]}x{size[1]}" if size else
              "no longest-run image: neither PDF could be used")
    if args.passage_shots:
        shots = passage_shots(conn, sides, writer, case_key, pid1, pid2, earlier_id, args, args.passage_shots,
                              args.passage_count)
        print(f"wrote {len(shots)} passage image(s) to {args.passage_shots}")
    if args.title_shot:
        size = title_shot(conn, sides, writer, pid1, pid2, earlier_id, args, args.title_shot)
        print(f"wrote {args.title_shot}" if size else "no title image: neither PDF could be used")
    if args.text_exhibits:
        print(f"wrote {out_path}: {total} runs as text; {writer.count} title images, "
              f"{writer.bytes_written / 1e6:.1f} MB")
        return
    counts = Counter(s for pair in exhibits.outcomes for s in pair)
    both = sum(1 for a, b in exhibits.outcomes if a == b == "located")
    print(f"wrote {out_path}: {len(exhibits.outcomes)} exhibits of {total} runs; "
          f"both sides located {both}, sides located/partial/missing "
          f"{counts['located']}/{counts['partial']}/{counts['missing']}; "
          f"{writer.count} images, {writer.bytes_written / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
