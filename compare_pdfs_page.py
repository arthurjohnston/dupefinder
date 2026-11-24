#!/usr/bin/env python3
"""Compare two PDFs and write the GI comparison page for them, with no database, DOI or General Index lookup.

The same page as the GI pages on the project site and in the emails (gi_pubpeer.publisher_page()), built
from just the two files: both PDFs are extracted as extract_papers.py does into a throwaway library,
measured as gi_verify.py measure does (10-word shingles, x-drop 3), and rendered. The summary bar gives
each document's share of its words inside a run shared with the other, and the matched words. Unlike a
GI page there's no DOI link and no General Index 5-gram evidence.

  default   the image page: the longest shared passage, then every shared passage, cropped from both
            PDFs with the shared words highlighted. One self-contained file (images inlined).
  --text    the text page, for emailing: the same passages as word-diffed text, or both documents end
            to end as one diff when more than 90% of either is shared. Much smaller.

Titles come from each PDF's metadata, or else its largest text on page 1; give --title-a/--title-b when
that's wrong. Authors aren't read from the PDF (bylines mix in superscripts and affiliations, too
unreliable to show), so a card has a byline only when --authors-a/--authors-b give one. Without years (--year-a/--year-b) the first PDF is paper A, on the left; with them, the
earlier one is, and the percentages are labelled earlier/later.

Usage: compare_pdfs_page.py A.pdf B.pdf [-o OUT.html] [--text] [--title-a T] [--title-b T]
                            [--year-a Y] [--year-b Y] [--authors-a "X; Y"] [--authors-b "X; Y"]
"""
import argparse
import html
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "general_index")]

import pymupdf as fitz  # noqa: E402

import compare_two_papers as ctp  # noqa: E402
import extract_papers as ep  # noqa: E402
import gi_pubpeer  # noqa: E402
import rank_paper_pairs as rpp  # noqa: E402
from write_dupe_reports_html import resolve_earlier_id  # noqa: E402
JUNK_TITLE = ("microsoft word", "untitled", ".doc", ".pdf", "powerpoint", "||", "vol.", "volume", "issue", "page ")


def pdf_title(path):
    """The PDF's metadata title if it looks like one, else the largest-font text on page 1 that isn't a
    running head (a line that's on page 2 as well) or journal furniture."""
    with fitz.open(path) as doc:
        meta = " ".join(((doc.metadata or {}).get("title") or "").split())
        if len(meta.split()) >= 3 and not any(j in meta.lower() for j in JUNK_TITLE):
            return meta
        if not doc.page_count:
            return path.stem
        page2 = {" ".join(ln.split()).lower() for ln in doc[1].get_text().splitlines()} \
            if doc.page_count > 1 else set()   # page 2's lines: a running head is one of them
        blocks = []   # [(line size, line text, mostly bold)] per text block of page 1, furniture left out
        for blk in doc[0].get_text("dict")["blocks"]:
            lines = []
            for line in blk.get("lines", []):
                spans = [sp for sp in line["spans"] if sp["text"].strip()]
                text = " ".join("".join(sp["text"] for sp in line["spans"]).split())
                if not spans or text.lower() in page2 or \
                        any(j in text.lower() for j in JUNK_TITLE):
                    continue
                # the line's main size: the one most of its characters are set in (not a superscript)
                size = max({round(sp["size"], 1) for sp in spans},
                           key=lambda z: sum(len(sp["text"]) for sp in spans if round(sp["size"], 1) == z))
                bold = sum(len(sp["text"]) for sp in spans if sp["flags"] & 16) * 2 > len(text)
                lines.append((size, text, bold))
            if lines:
                blocks.append(lines)
    sizes = sorted({ln[0] for blk in blocks for ln in blk}, reverse=True)
    # the largest size carrying at least three words (not a drop cap or a logo letter); the title is every
    # block holding text that size, so a title split over lines or blocks isn't cut up -- but only its lines
    # near that size, and bold if any of it is: a block can run on into the byline, sometimes at the same size
    top = next((sz for sz in sizes
                if len(" ".join(ln[1] for blk in blocks for ln in blk if ln[0] == sz).split()) >= 3), None)
    bold = any(ln[2] for blk in blocks for ln in blk if ln[0] == top)   # then the title is the bold text
    fits = lambda ln: ln[0] >= 0.85 * top and ln[2] == bold
    parts, started = [], False
    for ln in (ln for blk in blocks if any(ln[0] == top and ln[2] == bold for ln in blk) for ln in blk):
        if fits(ln):
            parts.append(ln[1])
            started = True
        elif started:
            break   # the title is one unbroken run of lines: what fits after a gap is a heading
    title = " ".join(parts)
    title = re.sub(r"^\d{1,4}\s+(?=\S)", "", title)   # a page number printed at the title's size
    return title[:300] or path.stem


def build_library(lib, pdfs, titles, years, authors):
    conn = ep.init_library_db(lib)
    conn.execute("CREATE TABLE IF NOT EXISTS paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, "
                 "para_index INTEGER, text TEXT)")
    ids = []
    for pdf, title, year, names in zip(pdfs, titles, years, authors):
        paragraphs, citations, _ = ep.extract_paper(pdf)
        if not paragraphs:
            raise SystemExit(f"{pdf}: no text extracted (a scanned PDF needs OCR first)")
        pid = ep.upsert_paper(conn, title, None, year, str(pdf))
        ep.replace_authors(conn, pid, names)
        ep.replace_citations(conn, pid, citations)
        conn.executemany("INSERT INTO paragraphs (paper_id, para_index, text) VALUES (?, ?, ?)",
                         [(pid, i, t) for i, t in enumerate(paragraphs)])
        ids.append(pid)
    conn.commit()
    conn.close()
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf_a", type=Path)
    ap.add_argument("pdf_b", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="output page (default: compare-<A>-<B>.html here)")
    ap.add_argument("--text", action="store_true", help="the text page, for emailing, instead of the image page")
    for side in ("a", "b"):
        ap.add_argument(f"--title-{side}")
        ap.add_argument(f"--year-{side}", type=int)
        ap.add_argument(f"--authors-{side}", default="", help="names separated by semicolons")
    args = ap.parse_args()

    pdfs = [p.resolve() for p in (args.pdf_a, args.pdf_b)]
    for p in pdfs:
        if not p.is_file():
            raise SystemExit(f"{p}: not found")
    out = (args.out or Path(f"compare-{pdfs[0].stem[:40]}-{pdfs[1].stem[:40]}.html")).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    titles = [args.title_a or pdf_title(pdfs[0]), args.title_b or pdf_title(pdfs[1])]
    authors = [[n.strip() for n in s.split(";") if n.strip()] for s in (args.authors_a, args.authors_b)]

    with tempfile.TemporaryDirectory() as tmp:
        lib = Path(tmp) / "library.sqlite3"
        a, b = build_library(lib, pdfs, titles, [args.year_a, args.year_b], authors)
        conn = sqlite3.connect(lib)
        stats = rpp.text_check(conn, (a, b), argparse.Namespace(
            min_doc_words=0, shingle_size=10, x_drop=3, gap_open=None, gap_extend=ctp.DEFAULT_GAP_EXTEND,
            max_gap=ctp.DEFAULT_MAX_GAP, strip_page_furniture=False))   # gi_verify.py measure's settings
        earlier = resolve_earlier_id(conn, a, b, [])
        conn.close()
        if not stats:
            raise SystemExit("no shared run of 10+ words: nothing to show")
        pcts = {a: stats["pct_a"], b: stats["pct_b"]}
        yrs = {a: args.year_a, b: args.year_b}
        first = earlier or a                  # the page's left paper: the earlier, else paper A
        second = b if first == a else a
        # as the GI pages: the later article first, when the order is known
        shown = ([(second, f"the later article ({yrs[second]})"), (first, f"the earlier ({yrs[first]})")]
                 if earlier else [(first, "paper A (left)"), (second, "paper B (right)")])
        bar = ('<span>shared text ' + " &middot; ".join(f"<strong>{pcts[pid]:.0f}%</strong> of {html.escape(name)}"
                                                         for pid, name in shown) + '</span> '
               f'<span>matched words <strong>{stats["matched_words"]:,}</strong></span> ')
        gi_pubpeer.overlap_html = lambda fd: bar   # in place of the GI version, which names the papers by DOI
        f = {"library": str(lib), "paper_a": a, "paper_b": b, "page_number": None}
        fd = {"earlier_known": earlier is not None, "data_extra": False,
              "first": {"pct": pcts[first]}, "second": {"pct": pcts[second]}}
        page, _, _ = gi_pubpeer.publisher_page(f, fd, images=not args.text)
    # the page writer puts the run count in <title>; a 2-4 word name is all a tab needs
    page = re.sub(r" \(\d+ shingle match(es)?\)</title>", "</title>", page, count=1)
    # no placeholder bylines or dates: the card shows authors/year only when given, else just the title
    page = page.replace('(authors unknown) &middot; ', '').replace(' &middot; n.d.</p>', '</p>')
    page = page.replace('<p class="meta">n.d.</p>', '')
    page = re.sub(r"<footer>Generated by .*?</footer>\s*", "", page, count=1, flags=re.S)   # names the GI scripts
    out.write_text(page.replace("\x00", ""), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): {stats['pct_a']:.0f}% of {pdfs[0].name} and "
          f"{stats['pct_b']:.0f}% of {pdfs[1].name} in shared runs, {stats['matched_words']:,} words, "
          f"longest run {stats['longest']} words")

if __name__ == "__main__":
    main()
