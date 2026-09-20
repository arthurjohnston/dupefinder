#!/usr/bin/env python3
"""Markdown comparison reports for the cases already decided to be worth reporting.

batch_compare_papers.py writes a plain-text comparison for every pair in a
potential_dupes selection -- hundreds of files, most of which turn out to be nothing.
This is the other end of the funnel: it runs the same exact word-shingle comparison, but
only over the pairs that a human already wrote up as a case under <corpus>/flagged_cases/,
and emits markdown instead of plain text -- so a report can be pasted into an email, a
PubPeer comment or an issue without reformatting, and reads properly in any markdown
viewer.

Pairs come from each case's own WRITEUP.md: its `compare_two_papers.py --paper-a N
--paper-b M` reproduction command, or, for the few write-ups that don't carry one, the
DOIs in its metadata table resolved against library.sqlite3. A case whose papers aren't
both in the corpus database is skipped and named in the summary rather than silently
dropped.

Each report carries both papers' metadata with DOI links, the overlap totals, and the
longest matched runs quoted from both sides -- the evidence a recipient needs to check the
claim without access to this project. `_index.md` ranks every case by matched words.

Output is a local working artifact, like dupe_comparisons/: it quotes substantial passages
from both papers, which this project has no redistribution rights to, so the generated
directory stays gitignored with the rest of the corpus and is not something to publish
as-is.

    python3 write_case_reports_md.py --corpus computer-ethics --corpus anthropology
"""

import argparse
import re
import sqlite3
from pathlib import Path

import compare_two_papers as ctp

PAIR_RE = re.compile(r"--paper-a\s+(\d+)\s+--paper-b\s+(\d+)")
# A case the project itself pulled (case 22's "**RETRACTED ... this is NOT a duplicate**") must not
# be handed to anyone as a report. Deliberately keyed on the bold self-retraction marker, NOT on the
# word "retracted": several live cases mention a journal retracting one of the papers, which is
# evidence for the case, not against it.
WITHDRAWN_RE = re.compile(r"^\s*>?\s*\*\*RETRACTED\b", re.M)
# The corpus database's author list comes from Crossref and is simply empty for some of these
# venues (14 of 50 reports had a blank byline on one side). The case write-up's own metadata
# table carries the byline as printed on the PDF, which is the better source anyway -- see
# REVIEWING.md on never trusting the database's author list over the paper itself.
WRITEUP_AUTHOR_ROW_RE = re.compile(r"^\|\s*Authors?\s*\|(.+)\|\s*$", re.M | re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s)\]|>]+?)(?=[.,)\]]*(?:\s|$))")
DEFAULT_SHINGLE = 10
DEFAULT_X_DROP = 3
DEFAULT_MAX_EXHIBITS = 12
EXCERPT_CHARS = 1200  # per side, per run -- enough to show the passage, not the whole paper


def slug(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower())).strip("-")


def load_paper(conn, paper_id):
    row = conn.execute("SELECT id, title, year, doi FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not row:
        return None
    authors = [a for (a,) in conn.execute(
        "SELECT a.name FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
        "WHERE pa.paper_id = ? ORDER BY pa.author_order", (paper_id,))]
    return {"id": row[0], "title": row[1], "year": row[2], "doi": row[3], "authors": authors}


def case_pairs(writeup_path, conn):
    """[(paper_a, paper_b), ...] for one case: ids from its reproduction command, else its
    DOIs resolved against the corpus database."""
    text = writeup_path.read_text(encoding="utf-8", errors="replace")
    pairs = [(int(a), int(b)) for a, b in PAIR_RE.findall(text)]
    if pairs:
        return list(dict.fromkeys(pairs))
    ids = []
    for doi in dict.fromkeys(DOI_RE.findall(text)):
        row = conn.execute("SELECT id FROM papers WHERE lower(doi) = lower(?)", (doi.rstrip(").,"),)).fetchone()
        if row and row[0] not in ids:
            ids.append(row[0])
    return [(ids[0], ids[1])] if len(ids) >= 2 else []


def writeup_bylines(writeup_path):
    """(paper_a_byline, paper_b_byline) from the write-up's own metadata table, or (None, None)."""
    match = WRITEUP_AUTHOR_ROW_RE.search(writeup_path.read_text(encoding="utf-8", errors="replace"))
    if not match:
        return None, None
    cells = [c.strip() for c in match.group(1).split("|")]
    return (cells[0] or None, cells[1] or None) if len(cells) == 2 else (None, None)


def paper_block(paper, label, byline=None):
    doi = paper["doi"]
    link = f"[{doi}](https://doi.org/{doi})" if doi else "(no DOI on file)"
    authors = ", ".join(paper["authors"]) or byline or "(no authors recorded)"
    if not paper["authors"] and byline:
        authors += "  *(byline as printed on the paper; absent from the corpus metadata)*"
    return (f"**{label}** — {paper['title']}  \n"
            f"{authors}  \n"
            f"{paper['year'] or 'year unknown'} · {link} · corpus id {paper['id']}\n")


def render(case_name, writeup_rel, paper_a, paper_b, runs, words_a, words_b, args, bylines=(None, None)):
    covered_a, covered_b = set(), set()
    for r in runs:
        covered_a.update(range(r[5], r[6]))
        covered_b.update(range(r[7], r[8]))
    total = len(covered_a)
    pct_a = 100 * len(covered_a) / max(1, len(words_a))
    pct_b = 100 * len(covered_b) / max(1, len(words_b))
    longest = runs[0][0] if runs else 0

    out = [f"# {case_name}", "",
           f"Case write-up: `{writeup_rel}`", "",
           "## The two papers", "",
           paper_block(paper_a, "Paper A", bylines[0]), "",
           paper_block(paper_b, "Paper B", bylines[1]), "",
           "## Overlap", "",
           "| | |", "|---|---|",
           f"| Matched runs (>= {args.shingle_size} consecutive words) | {len(runs)} |",
           f"| Total matched words | {total:,} |",
           f"| Longest single run | {longest:,} words |",
           f"| Share of Paper A | {pct_a:.1f}% of its {len(words_a):,} extracted words |",
           f"| Share of Paper B | {pct_b:.1f}% of its {len(words_b):,} extracted words |",
           "",
           f"Matching is exact, case-insensitive, over a flat word stream, with tolerance for "
           f"isolated substituted words (x-drop {args.x_drop}). It does not detect paraphrase, so "
           f"these totals are a floor on the real overlap, not a ceiling.",
           ""]

    if runs:
        out += ["## Longest matched passages", "",
                f"Showing the {min(len(runs), args.max_exhibits)} longest of {len(runs)} runs, "
                f"quoted from both papers. Text is truncated at {EXCERPT_CHARS} characters per side.", ""]
        for n, r in enumerate(runs[:args.max_exhibits], 1):
            length, para_a, para_b, text_a, text_b = r[0], r[1], r[2], r[3], r[4]
            subs = r[9]
            kind = "exact" if not subs else f"near-exact, {subs} substituted word(s)"
            out += [f"### Run {n} — {length:,} words ({kind})", "",
                    f"*Paper A, paragraph {para_a}:*", "",
                    "> " + text_a[:EXCERPT_CHARS].replace("\n", " ") + ("…" if len(text_a) > EXCERPT_CHARS else ""),
                    "",
                    f"*Paper B, paragraph {para_b}:*", "",
                    "> " + text_b[:EXCERPT_CHARS].replace("\n", " ") + ("…" if len(text_b) > EXCERPT_CHARS else ""),
                    ""]
    else:
        out += ["## Longest matched passages", "", "No runs at this threshold.", ""]

    out += ["## Reproducing this", "",
            "```", f"python3 compare_two_papers.py --library-db {args.library_db_shown} \\",
            f"    --paper-a {paper_a['id']} --paper-b {paper_b['id']} "
            f"--shingle-size {args.shingle_size} --x-drop {args.x_drop}", "```", ""]
    return "\n".join(out), {"case": case_name, "matched_words": total, "runs": len(runs),
                            "longest": longest, "pct_a": pct_a, "pct_b": pct_b}


def process_corpus(corpus, args):
    cases_dir = Path(corpus) / "flagged_cases"
    library = Path(corpus) / "library.sqlite3"
    if not cases_dir.is_dir() or not library.exists():
        print(f"{corpus}: no flagged_cases/ or library.sqlite3 -- skipped")
        return []
    out_dir = Path(corpus) / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(library, timeout=120)
    args.library_db_shown = f"{corpus}/library.sqlite3"

    written, skipped = [], []
    for writeup in sorted(cases_dir.glob("*/WRITEUP.md")):
        case_name = writeup.parent.name
        if WITHDRAWN_RE.search(writeup.read_text(encoding="utf-8", errors="replace")):
            skipped.append((case_name, "write-up retracted by this project -- not a duplicate, do not report"))
            continue
        pairs = case_pairs(writeup, conn)
        if not pairs:
            skipped.append((case_name, "no paper ids or resolvable DOIs in the write-up"))
            continue
        for n, (id_a, id_b) in enumerate(pairs, 1):
            paper_a, paper_b = load_paper(conn, id_a), load_paper(conn, id_b)
            if not paper_a or not paper_b:
                skipped.append((case_name, f"paper {id_a if not paper_a else id_b} not in {library}"))
                continue
            words_a = ctp.load_paper_words(conn, id_a)
            words_b = ctp.load_paper_words(conn, id_b)
            if not words_a or not words_b:
                skipped.append((case_name, f"no extracted text for paper {id_a if not words_a else id_b}"))
                continue
            runs = ctp.find_shingle_matches(words_a, words_b, shingle_size=args.shingle_size,
                                             x_drop=args.x_drop)
            suffix = "" if len(pairs) == 1 else f"-pair{n}"
            body, stats = render(case_name, str(writeup), paper_a, paper_b, runs, words_a, words_b, args,
                                  bylines=writeup_bylines(writeup))
            path = out_dir / f"{slug(case_name)}{suffix}.md"
            path.write_text(body, encoding="utf-8")
            stats["path"] = path
            written.append(stats)
            print(f"  {path}  ({stats['matched_words']:,} matched words)")

    written.sort(key=lambda s: -s["matched_words"])
    index = [f"# Cases decided to be reported — {corpus}", "",
             f"{len(written)} comparison report(s), ranked by total matched words. "
             f"Each case's own analysis lives in `{corpus}/flagged_cases/<case>/WRITEUP.md`.", "",
             "| Case | Matched words | Runs | Longest run | Share of A | Share of B |",
             "|---|---|---|---|---|---|"]
    for s in written:
        index.append(f"| [{s['case']}]({s['path'].name}) | {s['matched_words']:,} | {s['runs']} | "
                      f"{s['longest']:,} | {s['pct_a']:.1f}% | {s['pct_b']:.1f}% |")
    if skipped:
        index += ["", "## Not generated", ""]
        index += [f"- **{name}** — {why}" for name, why in skipped]
    (out_dir / "_index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"{corpus}: {len(written)} report(s), {len(skipped)} skipped -> {out_dir}/_index.md")
    return written


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", dest="corpora", action="append", default=None,
                    help="corpus directory holding flagged_cases/ and library.sqlite3 (repeatable)")
    p.add_argument("--out-dir", default="case_reports_md", help="written inside each corpus directory")
    p.add_argument("--shingle-size", type=int, default=DEFAULT_SHINGLE)
    p.add_argument("--x-drop", type=int, default=DEFAULT_X_DROP)
    p.add_argument("--max-exhibits", type=int, default=DEFAULT_MAX_EXHIBITS,
                    help="longest matched runs quoted per report")
    args = p.parse_args()
    args.corpora = args.corpora or ["computer-ethics", "anthropology"]
    return args


def main():
    args = parse_args()
    total = 0
    for corpus in args.corpora:
        total += len(process_corpus(corpus, args))
    print(f"\n{total} report(s) written across {len(args.corpora)} corpus/corpora")


if __name__ == "__main__":
    main()
