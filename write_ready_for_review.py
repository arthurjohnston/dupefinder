#!/usr/bin/env python3
"""Consolidate agent-reviewed candidates into one "ready for human review" directory,
per pair, with a matching .txt and .html file side by side.

Why this exists: this project accumulated several different, disagreeing attempts at
"here's the stuff worth a human's attention" (dupe_reports_html/, ai_dupes/,
filtered_dupe_reports_html/, final_decided_report_html/, confirmed_reports_html/) as
review methodology evolved, with no single directory anyone could point to as
authoritative. This script is the replacement: one query, one directory, regenerated
fresh each run, always reflecting the CURRENT database state rather than whatever
generated an earlier snapshot.

The query is deliberately `status='agent_decided_dupe'` by default, not
`ai_check='yes'` (write_dupe_reports.py's own selection) and not `status='confirmed'`
(write_dupe_reports_html.py's own default). `ai_check='yes'` is classify_dupes.py's
PROGRAMMATIC pre-filter -- pure regex/rule matching, no judgment about genuine-dupe vs.
boilerplate vs. citation at all (in practice the large majority of any real ai_check='yes'
backlog turns out to be boilerplate/citation once actually reviewed -- see todo.md).
`agent_decided_dupe` is a real reviewer's (agent, not yet human) considered verdict --
per REVIEWING.md, an agent is only supposed to press that verdict after running
compare_two_papers.py's exact word-shingle scan across the two COMPLETE documents to
confirm the extent, not just eyeballing the single paragraph pair that originally
surfaced the candidate. That makes it the right "has already survived a real check,
ready for a human's final look" tier -- `confirmed` is one tier further up (a human,
not just an agent, has looked), which is exactly the tier this script exists to feed.

Two independently-generated outputs per pair, both written to `--out-dir`, sharing the
same base filename (`<slug>.html` / `<slug>.txt`) so they sort next to each other:
  - `<slug>.html`: write_dupe_reports_html.py's render_case() -- word-shingle exhibits
    with matched-span highlighting, meant for visual/Artifact viewing.
  - `<slug>.txt`: compare_two_papers.py's full combined report -- BOTH of its
    independent methods (exact word-shingle matching AND sentence-level embedding
    similarity/paraphrase detection), run across the two complete documents. This is
    strictly more thorough than what the HTML page shows (the HTML is shingle-exhibits
    only, per its own module docstring) -- the .txt is the fuller evidence record,
    the .html is the fast visual skim.

Shared shingle-scan settings across both outputs default to this project's now-standard
10-word/--x-drop-3 combination (see computer-ethics/flagged_cases/README.md's own note
on why every case was re-run at this exact setting for comparability).

Idempotent/regenerate-in-place: re-running overwrites both files for every matching pair
every time -- cheap enough (a handful of seconds per pair, dominated by
compare_two_papers.py's sentence-transformer model load, which is NOT cached across
pairs within one run -- a real but accepted inefficiency at the pair counts this project
has seen so far; --skip-sentences avoids it entirely if the fuller check isn't wanted).

    python3 write_ready_for_review.py --library-db computer-ethics/library.sqlite3
    python3 write_ready_for_review.py --library-db anthropology/library.sqlite3 --skip-sentences
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import db
import compare_two_papers
from write_dupe_reports_html import render_case, DEFAULT_SHINGLE_SIZE, DEFAULT_MAX_SHINGLE_EXHIBITS

DEFAULT_STATUS = "agent_decided_dupe"
DEFAULT_X_DROP = 3  # this project's now-standard setting -- see module docstring


def run_compare_two_papers(argv):
    """Same run_with_argv() pattern run_pipeline.py uses for every other stage: patch
    sys.argv, call the module's own main() in-process, restore sys.argv after. Reuses
    compare_two_papers.py's own tested report-building/writing logic unchanged rather
    than duplicating it here."""
    old_argv = sys.argv
    sys.argv = argv
    try:
        compare_two_papers.main()
    finally:
        sys.argv = old_argv


def parse_args():
    parser = argparse.ArgumentParser(
        description="Write one matching .txt + .html pair per agent-reviewed candidate pair into "
                     "a single 'ready for human review' directory."
    )
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("ready_for_review"))
    parser.add_argument("--status", default=DEFAULT_STATUS,
                         help=f"potential_dupes.status to include (default {DEFAULT_STATUS!r} -- see "
                              f"module docstring for why this, not ai_check='yes' or status='confirmed')")
    parser.add_argument("--shingle-size", type=int, default=DEFAULT_SHINGLE_SIZE,
                         help=f"word n-gram size for both outputs' shingle scan (default {DEFAULT_SHINGLE_SIZE})")
    parser.add_argument("--x-drop", type=int, default=DEFAULT_X_DROP,
                         help=f"seed-and-extend tolerance for both outputs (default {DEFAULT_X_DROP}); "
                              f"pass a negative number to disable (e.g. -1) since argparse won't take None here")
    parser.add_argument("--mismatch-penalty", type=int, default=1)
    parser.add_argument("--max-shingle-exhibits", type=int, default=DEFAULT_MAX_SHINGLE_EXHIBITS,
                         help="passed through to render_case() for the .html side")
    parser.add_argument("--skip-sentences", action="store_true",
                         help="skip compare_two_papers.py's sentence-level/paraphrase method (and its "
                              "sentence-transformer model load) -- .txt becomes shingle-only, same "
                              "method as the .html, much faster at volume")
    return parser.parse_args()


def main():
    args = parse_args()
    x_drop = None if args.x_drop is not None and args.x_drop < 0 else args.x_drop
    conn = db.connect(args.library_db)
    conn.row_factory = sqlite3.Row
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # same_paper=0 matters here, not just cosmetic: a pair can be marked agent_decided_dupe and
    # LATER hit by a (p)apers-are-the-same correction (mark_same_paper()) -- that function
    # deliberately does NOT touch status when it fires (see its own docstring), so a stale
    # agent_decided_dupe row with same_paper=1 means "this pair turned out to be the same
    # underlying paper cataloged twice, not a real cross-document match" -- exactly what this
    # directory must not present as a finding.
    rows = conn.execute(
        "SELECT * FROM potential_dupes WHERE status=? AND same_paper=0", (args.status,)
    ).fetchall()
    groups = {}
    for r in rows:
        key = tuple(sorted((r["paper_id_1"], r["paper_id_2"])))
        groups.setdefault(key, []).append(r)

    print(f"{len(rows)} row(s) across {len(groups)} pair(s) with status={args.status!r}")
    for (pid1, pid2), group_rows in groups.items():
        slug, html_body, _shingle_total = render_case(
            conn, pid1, pid2, group_rows,
            shingle_size=args.shingle_size,
            max_shingle_exhibits=args.max_shingle_exhibits,
            mismatch_penalty=args.mismatch_penalty,
            x_drop=x_drop,
            library_db_path=args.library_db,
        )
        (args.out_dir / f"{slug}.html").write_text(html_body, encoding="utf-8")

        compare_argv = [
            "compare_two_papers.py",
            "--library-db", str(args.library_db),
            "--paper-a", str(pid1),
            "--paper-b", str(pid2),
            "--shingle-size", str(args.shingle_size),
            "--mismatch-penalty", str(args.mismatch_penalty),
            "--out", str(args.out_dir / f"{slug}.txt"),
        ]
        if x_drop is not None:
            compare_argv += ["--x-drop", str(x_drop)]
        if args.skip_sentences:
            compare_argv.append("--skip-sentences")
        run_compare_two_papers(compare_argv)

        print(f"  wrote {slug}.html + {slug}.txt ({len(group_rows)} candidate row(s))")

    conn.close()


if __name__ == "__main__":
    main()
