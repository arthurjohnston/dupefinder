#!/usr/bin/env python3
"""Surface the most promising still-unclassified potential_dupes rows for a
human/AI review pass -- built for the 22,034-row backlog left over after the
2026-08-24 LSH bucket-saturation fix (todo.md post-mortem 4/5) generated far
more candidates than classify_dupes.py's fixed pattern library could resolve
in one pass.

Filter, deliberately narrow rather than "show me everything":
  - `ai_check IS NULL` -- classify_dupes.py hasn't already resolved it either
    way (skips rows it's already called `yes`/`no` on).
  - `same_paper = 0` -- cross-paper only, explicit rather than relying on
    `same_author IS NULL` implying it (true today -- build_dupe_candidates.py
    only ever sets same_author for same_paper=0 rows -- but stated directly
    so this query's intent doesn't depend on remembering that).
  - `same_author = 0` -- cross-author only. same_author = 1 rows are
    self-reuse, already auto-classified `ai_check='yes'` by
    classify_dupes.py's own rule and don't need a human look.
  - `similarity < 0.99 AND lcs_ratio < 0.99 AND ngram_jaccard < 0.99` --
    excludes near-exact-duplicate paragraphs on purpose. Those are
    overwhelmingly the corpus-wide boilerplate pattern documented in
    post-mortem 5 (license blocks, disclosure statements, citation
    self-references) -- reused so verbatim it hits every metric at once.
    Requiring all three below 0.99 selects for "genuinely similar text with
    at least some actual variation" -- exactly the profile real paraphrased/
    edited copying has, as opposed to a verbatim-reused boilerplate block.

Two ranking modes (`--sort`):
  - `lcs` (default): ranked by lcs_ratio desc, similarity as tiebreak -- the
    strongest single "this was copied" signal per text_overlap.py's own
    docstring (longest verbatim shared word run). Surfaces contiguous
    copying first. Three rounds of this mode (todo.md post-mortem 7) resolved
    500 candidates via new classify_dupes.py patterns before yield dropped
    off sharply (461 -> 39 in one round) -- what's left is increasingly NOT
    contiguous copying, which is exactly what this ranking is worst at
    surfacing (a paragraph with scattered matching phrases but no one long
    run scores low lcs_ratio and sinks to the bottom of this ranking,
    regardless of how much total overlap it actually has).
  - `gap`: ranked by (ngram_jaccard - lcs_ratio) desc -- surfaces the
    opposite profile: word 5-grams matching all over a paragraph
    (ngram_jaccard high) without one long contiguous run (lcs_ratio
    comparatively low). That's the fingerprint of *reworded* copying --
    clauses reordered, synonyms swapped in, sentences split/merged -- as
    opposed to verbatim-with-minor-edits, which `lcs` mode already covers
    well. A citation/reference-heavy paragraph can also produce a high gap
    (shared author names and years scattered through otherwise-different
    text) without being real prose plagiarism, so this mode's hit rate on
    genuine content is expected to be noisier than `lcs` mode's, not cleaner.

Each row is also run through the same boilerplate-marker heuristic
inspect_lsh_buckets.py uses (reused from there, not duplicated), as a
first-pass filter -- not a verdict, just to flag which of the top N are
likely still boilerplate despite ducking the 0.99 filter (e.g. a *short*
boilerplate fragment can still score high lcs/ngram without tripping 0.99
similarity) so a human reviewer's attention goes to the more promising ones
first.
"""

import argparse
import logging
from pathlib import Path

import db
from inspect_lsh_buckets import BOILERPLATE_MARKERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("find_review_candidates")

DEFAULT_LIMIT = 100
DEFAULT_MAX_METRIC = 0.99
DEFAULT_SORT = "lcs"
SORT_ORDER_BY = {
    "lcs": "pd.lcs_ratio DESC, pd.similarity DESC",
    "gap": "(pd.ngram_jaccard - pd.lcs_ratio) DESC, pd.ngram_jaccard DESC",
}


def find_candidates(conn, limit, max_metric, offset=0, sort=DEFAULT_SORT):
    """`offset` lets a later call pick up where an earlier one left off in the
    same ranking (e.g. --offset 100 --limit 150 for "the next 150 after the
    ones already read by hand") -- the ranking itself is stable between calls
    as long as no rows in between get their ai_check set, since this only
    ever selects ai_check IS NULL rows; rows classified in the meantime
    simply drop out of the ranking rather than shifting it, so an --offset
    from before a classify_dupes.py run may skip or repeat a few rows across
    that boundary -- fine for this tool's actual purpose (a reading sample),
    not something to build automation on. `sort` picks the ranking (see
    SORT_ORDER_BY / the module docstring's `lcs` vs `gap` description)."""
    order_by = SORT_ORDER_BY[sort]
    return conn.execute(
        f"""
        SELECT pd.id, pd.similarity, pd.lcs_ratio, pd.ngram_jaccard,
               pd.paper_id_1, pd.paper_id_2, pp1.title, pp2.title,
               p1.text, p2.text
        FROM potential_dupes pd
        JOIN papers pp1 ON pp1.id = pd.paper_id_1
        JOIN papers pp2 ON pp2.id = pd.paper_id_2
        JOIN paragraphs p1 ON p1.id = pd.paragraph_id_1
        JOIN paragraphs p2 ON p2.id = pd.paragraph_id_2
        WHERE pd.ai_check IS NULL
          AND pd.same_paper = 0
          AND pd.same_author = 0
          AND pd.similarity < ?
          AND pd.lcs_ratio < ?
          AND pd.ngram_jaccard < ?
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        (max_metric, max_metric, max_metric, limit, offset),
    ).fetchall()


def looks_boilerplate(text_a, text_b):
    hits = sum(1 for t in (text_a, text_b) if any(m in t.lower()[:200] for m in BOILERPLATE_MARKERS))
    return hits >= 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--offset", type=int, default=0, help="Skip this many ranked rows first (see find_candidates())")
    parser.add_argument("--sort", choices=sorted(SORT_ORDER_BY), default=DEFAULT_SORT,
                         help="'lcs' (default): contiguous copying first. 'gap': ngram_jaccard-minus-lcs_ratio "
                              "desc, surfaces reworded/distributed copying instead (see module docstring).")
    parser.add_argument("--max-metric", type=float, default=DEFAULT_MAX_METRIC)
    parser.add_argument("--out", type=Path, default=Path("review_candidates_report.txt"))
    args = parser.parse_args()

    conn = db.connect(args.library_db)

    pool = conn.execute(
        "SELECT COUNT(*) FROM potential_dupes WHERE ai_check IS NULL AND same_paper = 0 AND same_author = 0"
    ).fetchone()[0]
    logger.info("%d unclassified, cross-author candidate(s) in the pool before the <%.2f metric filter",
                pool, args.max_metric)

    rows = find_candidates(conn, args.limit, args.max_metric, args.offset, args.sort)
    logger.info("%d candidate(s) matched (similarity/lcs_ratio/ngram_jaccard all < %.2f)", len(rows), args.max_metric)

    lines = []
    def out(s=""):
        print(s)
        lines.append(s)

    boiler_count = 0
    out(f"=== {len(rows)} unclassified cross-author candidates (rank {args.offset+1}-{args.offset+len(rows)}, "
        f"sort={args.sort}) (similarity/lcs_ratio/ngram_jaccard all < {args.max_metric}) ===")
    out(f"order by: {SORT_ORDER_BY[args.sort]}\n")
    for pd_id, sim, lcs, ngram, paper1, paper2, title1, title2, t1, t2 in rows:
        boiler = looks_boilerplate(t1, t2)
        if boiler:
            boiler_count += 1
        tag = "boilerplate-marker" if boiler else "review"
        out(f"[{tag}] id={pd_id} sim={sim:.3f} lcs={lcs:.3f} ngram={ngram:.3f} "
            f"papers=({paper1},{paper2})")
        out(f"  {title1[:60]!r} <-> {title2[:60]!r}")
        out(f"  T1: {t1[:200]!r}")
        out(f"  T2: {t2[:200]!r}")
        out("")

    out("=== summary ===")
    out(f"{boiler_count}/{len(rows)} flagged by the boilerplate-marker heuristic (not a verdict, just triage)")
    out(f"{len(rows) - boiler_count}/{len(rows)} unflagged -- worth a closer look first")

    args.out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("wrote %s", args.out)
    conn.close()


if __name__ == "__main__":
    main()
