#!/usr/bin/env python3
"""Rank whole paper PAIRS by how much verbatim text they actually share, for triaging a
large unreviewed backlog -- LEAD_AGENT_PLAYBOOK.md's Phase 2.5, as a script.

One `potential_dupes` row is a single matched paragraph. Real whole-document overlap --
the shape every confirmed case in this project turned out to have -- shows up as *many*
rows between the same two papers, not as any one row with an unusually high similarity.
Reviewing rows one at a time can't see that, and a backlog of tens of thousands of rows
can't be read at all.

Two stages, cheap before expensive:

  1. **Rank by candidate-row count** (SQL, seconds): group the actionable backlog by paper
     pair and count rows. `potential_dupes` is already the output of LSH bucketing plus a
     cosine-similarity threshold, so a pair with many rows is a stronger signal than any
     single row's score -- and counting is free compared to re-deriving similarity.
  2. **Text-check the top --top-pairs** with compare_two_papers.py's exact word-shingle
     matcher across both complete documents, and re-rank by matched words. This is the
     number that separates a real case (thousands of words, runs in the hundreds) from
     ordinary noise (a few short runs), and it's what the summary sorts on.

Output is a triage list, not a verdict: REVIEWING.md's checks (byline read from the PDF,
each paper's content matching its own title, third-party-quotation check, citation check)
still have to happen before anything becomes a case. Pairs whose overlap is entirely one
journal's masthead will rank high here -- that's what --min-longest-run and reading the
top of the list are for.

    python3 rank_paper_pairs.py --library-db computer-ethics/library.sqlite3 \\
        --top-pairs 150 --out computer-ethics/pair_triage.txt
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import compare_two_papers as ctp

# The actionable slice: different papers, different authors, no pattern verdict, unreviewed.
# Same filter find_review_candidates.py and the playbook's Phase 3 partitioning use.
BACKLOG_WHERE = ("same_paper = 0 AND same_author = 0 AND ai_check IS NULL "
                  "AND status = 'unreviewed'")


def rank_by_row_count(conn, where, limit):
    """[(paper_id_lo, paper_id_hi, rows), ...] -- the cheap first-stage ranking.

    Grouped on MIN/MAX of the two ids, not on the columns as stored: potential_dupes does not
    normalize which paper lands in paper_id_1, so the same pair appears under both orderings
    and grouping by the raw columns reports (and re-compares) it twice."""
    return conn.execute(
        f"SELECT MIN(paper_id_1, paper_id_2) AS lo, MAX(paper_id_1, paper_id_2) AS hi, COUNT(*) AS n "
        f"FROM potential_dupes WHERE {where} GROUP BY lo, hi ORDER BY n DESC LIMIT ?",
        (limit,),
    ).fetchall()


def all_pairs_among(conn, paper_where, limit):
    """Every pair among the papers matching a predicate -- for asking "is this whole GROUP of
    papers built from one document?" rather than triaging an existing candidate backlog.

    How case 38 (one college's 143 papers in one publisher's journals) was found: that pattern
    never surfaces from potential_dupes ranking alone, because it isn't one suspicious pair, it's
    a population. Quadratic by construction, so --max-group caps the set; 143 papers (10,153
    pairs) took about 20 seconds.
    """
    ids = [r[0] for r in conn.execute(f"SELECT id FROM papers WHERE {paper_where} LIMIT ?", (limit,))]
    return [(a, b, 0) for i, a in enumerate(ids) for b in ids[i + 1:]], len(ids)


def paper_meta(conn, paper_id):
    row = conn.execute("SELECT title, year, doi FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not row:
        return {"title": "(not in papers table)", "year": None, "doi": None, "authors": ""}
    authors = ", ".join(a for (a,) in conn.execute(
        "SELECT a.name FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
        "WHERE pa.paper_id = ? ORDER BY pa.author_order", (paper_id,)))
    return {"title": row[0], "year": row[1], "doi": row[2], "authors": authors}


def text_check(conn, pair, args):
    words_a = ctp.load_paper_words(conn, pair[0])
    words_b = ctp.load_paper_words(conn, pair[1])
    if len(words_a) < args.min_doc_words or len(words_b) < args.min_doc_words:
        return None
    runs = ctp.find_shingle_matches(words_a, words_b, shingle_size=args.shingle_size, x_drop=args.x_drop)
    if not runs:
        return None
    covered_a, covered_b = set(), set()
    for r in runs:
        covered_a.update(range(r[5], r[6]))
        covered_b.update(range(r[7], r[8]))
    return {"runs": len(runs), "matched_words": len(covered_a), "longest": runs[0][0],
            "pct_a": 100 * len(covered_a) / len(words_a), "pct_b": 100 * len(covered_b) / len(words_b),
            "words_a": len(words_a), "words_b": len(words_b),
            "longest_text": " ".join(runs[0][3].split())[:300]}


def render(results, conn, args):
    lines = [f"Paper-pair triage -- {len(results)} pair(s) with >= {args.min_words} matched words",
             f"ranked by verbatim overlap across both complete documents "
             f"({args.shingle_size}-word exact matching, x-drop {args.x_drop})",
             "",
             "A high rank is a lead, not a finding: check the byline on each PDF, that each paper's",
             "content matches its own title, and whether the shared text is one journal's masthead",
             "or both papers quoting the same third source, before treating any of these as a case.",
             "",
             "The most common legitimate pattern near the top of this list is a doctoral thesis against",
             "its author's own published papers. It can look cross-author here whenever the corpus has no",
             "author list for one side (conference proceedings often deposit none), so check the bylines",
             "rather than trusting the authors printed below.",
             ""]
    for n, r in enumerate(results, 1):
        a, b = paper_meta(conn, r["paper_a"]), paper_meta(conn, r["paper_b"])
        lines += [
            "=" * 100,
            f"{n}. {r['matched_words']:,} matched words | {r['runs']} runs | longest {r['longest']:,} | "
            f"{r['pct_a']:.0f}% of A, {r['pct_b']:.0f}% of B | {r['candidate_rows']} candidate row(s)",
            f"   A [{r['paper_a']}] {a['title'][:95]}",
            f"       {a['authors'][:80] or '(no authors recorded)'} | {a['year'] or '?'} | {a['doi'] or 'no DOI'}",
            f"   B [{r['paper_b']}] {b['title'][:95]}",
            f"       {b['authors'][:80] or '(no authors recorded)'} | {b['year'] or '?'} | {b['doi'] or 'no DOI'}",
            f"   longest run: {r['longest_text'][:200]}",
            f"   python3 compare_two_papers.py --library-db {args.library_db} "
            f"--paper-a {r['paper_a']} --paper-b {r['paper_b']} --shingle-size {args.shingle_size} "
            f"--x-drop {args.x_drop}",
            "",
        ]
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--library-db", type=Path, required=True)
    p.add_argument("--top-pairs", type=int, default=150,
                    help="how many of the row-count-ranked pairs to text-check (stage 2)")
    p.add_argument("--min-words", type=int, default=300, help="minimum matched words to report a pair")
    p.add_argument("--min-longest-run", type=int, default=0,
                    help="also require a single run this long -- raise it to filter out pairs whose "
                         "overlap is many short masthead/citation fragments")
    p.add_argument("--min-doc-words", type=int, default=300, help="skip papers with less extracted text")
    p.add_argument("--shingle-size", type=int, default=10)
    p.add_argument("--x-drop", type=int, default=3)
    p.add_argument("--where", default=BACKLOG_WHERE, help="SQL predicate selecting the backlog to triage")
    p.add_argument("--paper-where", default=None,
                    help="instead of the potential_dupes backlog, compare EVERY pair among the papers "
                         "matching this predicate on the papers table, e.g. \"doi LIKE '10.64751%%'\" -- "
                         "quadratic, so keep the group small (see --max-group)")
    p.add_argument("--max-group", type=int, default=200, help="--paper-where: cap on papers in the group")
    p.add_argument("--out", type=Path, default=None, help="write the ranked report here (default: stdout)")
    p.add_argument("--json-out", type=Path, default=None, help="also write full results as JSON")
    return p.parse_args()


def main():
    args = parse_args()
    conn = sqlite3.connect(args.library_db, timeout=120)
    if args.paper_where:
        pairs, n_papers = all_pairs_among(conn, args.paper_where, args.max_group)
        print(f"{n_papers} paper(s) match -- {len(pairs)} pair(s) to text-check", file=sys.stderr)
    else:
        pairs = rank_by_row_count(conn, args.where, args.top_pairs)
        print(f"{len(pairs)} pair(s) to text-check (most candidate rows first; "
              f"top pair has {pairs[0][2] if pairs else 0})", file=sys.stderr)

    results, started = [], time.time()
    for i, (paper_a, paper_b, rows) in enumerate(pairs, 1):
        stats = text_check(conn, (paper_a, paper_b), args)
        if stats and stats["matched_words"] >= args.min_words and stats["longest"] >= args.min_longest_run:
            results.append({"paper_a": paper_a, "paper_b": paper_b, "candidate_rows": rows, **stats})
        if i % 25 == 0:
            print(f"  {i}/{len(pairs)} checked, {len(results)} kept ({time.time() - started:.0f}s)",
                  file=sys.stderr)
    results.sort(key=lambda r: -r["matched_words"])

    report = render(results, conn, args)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out} ({len(results)} pair(s))", file=sys.stderr)
    else:
        print(report)
    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
