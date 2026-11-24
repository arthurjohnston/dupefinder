#!/usr/bin/env python3
"""Measure the text two documents share: runs of N+ identical consecutive words.

    python3 -I shared_runs.py PAPER SOURCE [--n 10] [--gap 3] [--show 15]

PAPER is the document being checked, SOURCE the candidate it may have copied from. Each can be
.pdf (needs `pdftotext` on PATH), .html/.htm/.xml (tags stripped) or plain text.

Prints the share of PAPER's words that sit inside a shared run (the headline figure), the same for
SOURCE, the run count, the longest run, and the longest runs with their position in PAPER. Runs
within --gap differing words of each other in BOTH documents are merged into one near-verbatim run
(a swapped or dropped word doesn't split a copied passage); --gap 0 counts exact runs only.

Standard library only. The figure is a floor: reworded copying isn't counted.
"""
import argparse
import html
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def read_text(path):
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".pdf":
        out = subprocess.run(["pdftotext", "-q", str(p), "-"], capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"pdftotext failed on {p}")
        text = out.stdout
    else:
        text = p.read_text(errors="replace")
        if suf in (".html", ".htm", ".xml"):
            parser = _Text()
            parser.feed(text)
            text = " ".join(parser.parts)
    text = html.unescape(text)
    text = re.sub(r"-\s*\n\s*(?=[a-z])", "", text)        # rejoin words hyphenated across lines
    return text


def words(text):
    toks = WORD.findall(text)
    return toks, [t.lower() for t in toks]


def exact_runs(a, b, n):
    """Maximal runs of identical words, as (start_a, start_b, length)."""
    index = {}
    for i in range(len(a) - n + 1):
        index.setdefault(tuple(a[i:i + n]), []).append(i)
    runs, seen = [], set()
    for j in range(len(b) - n + 1):
        for i in index.get(tuple(b[j:j + n]), ()):
            if (i - 1, j - 1) in seen or (i > 0 and j > 0 and a[i - 1] == b[j - 1]):
                seen.add((i, j))
                continue                                # inside a run already found
            k = n
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            runs.append((i, j, k))
            seen.add((i, j))
    return runs


def merge(runs, gap):
    """Join runs that follow each other within `gap` words in both documents."""
    if gap <= 0:
        return [(i, j, k, k) for i, j, k in runs]
    out = []
    for i, j, k in sorted(runs):
        if out:
            si, sj, la, lb = out[-1]
            da, db = i - (si + la), j - (sj + lb)
            if 0 <= da <= gap and 0 <= db <= gap:
                out[-1] = (si, sj, i + k - si, j + k - sj)
                continue
        out.append((i, j, k, k))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper")
    ap.add_argument("source")
    ap.add_argument("--n", type=int, default=10, help="minimum run length in words (default 10)")
    ap.add_argument("--gap", type=int, default=3, help="merge runs this close in both documents (default 3)")
    ap.add_argument("--show", type=int, default=15, help="how many of the longest runs to print")
    args = ap.parse_args()

    a_tok, a = words(read_text(args.paper))
    b_tok, b = words(read_text(args.source))
    if not a or not b:
        sys.exit("one of the documents has no extractable text")
    runs = merge(exact_runs(a, b, args.n), args.gap)
    cov_a = set(); cov_b = set()
    for i, j, la, lb in runs:
        cov_a.update(range(i, i + la)); cov_b.update(range(j, j + lb))
    print(f"paper  {len(a):>7} words, {len(cov_a):>6} in shared runs = {100 * len(cov_a) / len(a):.1f}%")
    print(f"source {len(b):>7} words, {len(cov_b):>6} in shared runs = {100 * len(cov_b) / len(b):.1f}%")
    print(f"{len(runs)} runs of {args.n}+ words (gap {args.gap}), longest {max((r[2] for r in runs), default=0)} words")
    for i, j, la, lb in sorted(runs, key=lambda r: -r[2])[:args.show]:
        snippet = " ".join(a_tok[i:i + min(la, 24)])
        print(f"  paper word {i:>6} ({100 * i / len(a):3.0f}% in), source word {j:>6}, {la:>4} words: {snippet}"
              + (" ..." if la > 24 else ""))


if __name__ == "__main__":
    main()
