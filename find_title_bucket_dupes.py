#!/usr/bin/env python3
"""Bucket papers by normalized title and run exact word-shingle matching across every
same-bucket pair that doesn't share an author -- a candidate-generation method that never looks
at paragraph embeddings at all, complementary to build_dupe_candidates.py's LSH+cosine pipeline.

See todo.md's "title-bucket + cross-author + expanding-n-gram-shingle" entry for the full
rationale and the concrete finds that validated the idea by hand before this script existed: of
18 identical-title/mismatched-author pairs hand-checked in computer-ethics (restricted to a known
predatory DOI-prefix family), 16 were confirmed real duplicates via this exact method -- and two
of those 16 had ZERO rows in potential_dupes at any threshold, because whatever made their
paragraph embeddings diverge was enough to miss every LSH bucket collision. Title-bucketing
sidesteps that miss entirely since it never looks at paragraph embeddings in the first place.

Mechanism, in order:
1. Group papers by find_duplicate_papers.py's own normalize_title() (HTML-unescape, lowercase,
   strip non-alnum, collapse whitespace) -- exact match after that normalization, not fuzzy
   similarity. A real gap, not fixed here: a punctuation-only title variant that this
   normalization doesn't happen to collapse to the same string (unlike case 01's "Impact on" vs
   "Impacton", which does) would be missed. Fuzzy title-similarity bucketing would catch more,
   at the cost of real implementation complexity (a similarity metric and its own threshold) --
   left for later per todo.md, not attempted in this first version.
2. Same MAX_GROUP_SIZE/MIN_TITLE_WORDS guards as find_duplicate_papers.py's own title tier, and
   for the identical reason documented there: an unbounded pass on this project's real corpus
   produced thousands of false "duplicate" groups from generic recurring publisher furniture
   ("Front Cover", "Masthead", per-reviewer "Review of <submission title>" reports) -- a group
   that large is a title collision, not a retrieval accident, regardless of what's inside it.
3. For every pair within a surviving bucket, checks build_dupe_candidates.py's own same_author
   logic (shared author_id via the paper_authors join table) -- reused rather than reimplemented
   so this script's notion of "same author" matches the rest of the project's exactly, including
   its one known limitation: the `authors` table has no name normalization, so "Deepak P" and
   "Deepak P." are two different author_ids, and a genuinely same-author pair can still come back
   same_author=False here. Treat that the same way REVIEWING.md's non-negotiable checklist
   already requires elsewhere in this project: verify from the actual PDF/extracted text before
   trusting a same_author=False from this script, never from the field alone.
4. Pairs already registered by find_duplicate_papers.py with an exact-DOI match are skipped
   entirely -- not a fresh cross-paper candidate at all, and that tier needs no further check.
   Pairs registered there only via ITS OWN title-normalization tier are deliberately NOT skipped
   -- that tier is exactly the one with no author/content gate (todo.md's "real bug found" entry:
   it blindly merges any identical-title pair as `same_paper=1`, which is what silently hid case
   07 after it had already been confirmed and written up). Trusting that tier's own skip-list
   here would mean never re-examining precisely the pairs this script exists to catch -- confirmed
   for real: an early version of this script did consult the full duplicate_papers table
   indiscriminately and came back with zero anthropology candidates, because every one of its
   613 title-tier pairs was already sitting in that table from find_duplicate_papers.py's own
   unconditional merge, never reverted the way computer-ethics's 16 real finds were.
5. Every surviving pair gets compare_two_papers.py's exact word-shingle scan
   (find_shingle_matches()) run against its two complete documents. Reports pairs whose total
   matched-word count (sum of every run's length) clears --min-total-words, ranked by that total
   descending.

--min-total-words is a first-pass heuristic floor, not a calibrated threshold -- todo.md
explicitly flags this as needing real calibration data the way build_dupe_candidates.py's
DEFAULT_THRESHOLD got from the Saxby/Taro ground-truth case, which this script doesn't have yet.
The default (20 words) is chosen only to filter out zero/near-zero-overlap noise before a human
looks at the ranked list; every candidate above it still needs a human/AI look, not automatic
trust -- this script surfaces candidates the way find_review_candidates.py does, it does not
itself render a verdict. Once a candidate looks real, hand it to compare_two_papers.py with
--append-to-writeup to produce the actual evidence for a flagged_cases write-up.

    python3 find_title_bucket_dupes.py --library-db computer-ethics/library.sqlite3
"""

import argparse
import logging
import re
from pathlib import Path

import db
from build_dupe_candidates import load_paper_authors
from compare_two_papers import find_shingle_matches, load_paper_words
from find_duplicate_papers import init_table, normalize_title

logger = logging.getLogger("find_title_bucket_dupes")

DEFAULT_MAX_GROUP_SIZE = 3  # see module docstring point 2 -- matches find_duplicate_papers.py's own guard
DEFAULT_MIN_TITLE_WORDS = 4  # ditto
DEFAULT_SHINGLE_SIZE = 10
DEFAULT_MIN_TOTAL_WORDS = 20  # heuristic floor, not calibrated -- see module docstring
SIGNIFICANT_WORD_RE = re.compile(r"[a-z0-9]{4,}")


def bucket_by_title(papers, min_title_words, max_group_size):
    """papers: iterable of (paper_id, title). Returns list of paper_id lists, each of size
    2..max_group_size, for normalized titles with >= min_title_words significant (4+ char) words.
    Oversized groups (generic recurring titles, per module docstring point 2) are dropped
    entirely, matching find_duplicate_papers.py's own find_pairs()."""
    by_norm = {}
    for paper_id, title in papers:
        if not title:
            continue
        norm = normalize_title(title)
        if len(SIGNIFICANT_WORD_RE.findall(norm)) < min_title_words:
            continue
        by_norm.setdefault(norm, []).append(paper_id)
    return [ids for ids in by_norm.values() if 2 <= len(ids) <= max_group_size]


def load_doi_matched_pairs(conn):
    """Set of frozenset({paper_id_1, paper_id_2}) pairs find_duplicate_papers.py registered
    specifically via its exact-DOI-match tier -- see module docstring point 4 for why this
    deliberately does NOT reuse build_dupe_candidates.py's load_duplicate_paper_pairs(), which
    pools every tier (including the title-normalization tier this script exists to re-check)
    into one set."""
    init_table(conn)
    rows = conn.execute(
        "SELECT paper_id_1, paper_id_2 FROM duplicate_papers WHERE reason LIKE 'same DOI%'"
    ).fetchall()
    return {frozenset((a, b)) for a, b in rows}


def find_candidates(conn, min_title_words=DEFAULT_MIN_TITLE_WORDS, max_group_size=DEFAULT_MAX_GROUP_SIZE,
                     shingle_size=DEFAULT_SHINGLE_SIZE, min_total_words=DEFAULT_MIN_TOTAL_WORDS, logger_=None):
    """Returns (results, degenerate): `results` is a list of dicts, one per surviving candidate
    pair, sorted by total_words descending; `degenerate` is a list of (paper_id_1, paper_id_2,
    len_words_1, len_words_2) tuples for pairs find_shingle_matches() couldn't produce a trustable
    count for (see the sanity check below). See module docstring for the full pipeline this runs."""
    papers = conn.execute("SELECT id, title FROM papers").fetchall()
    groups = bucket_by_title(papers, min_title_words, max_group_size)
    if logger_:
        logger_.info("%d paper(s) -> %d title bucket(s) (size 2-%d) to check", len(papers), len(groups), max_group_size)

    authors_by_paper = load_paper_authors(conn)
    duplicate_pairs = load_doi_matched_pairs(conn)

    pairs = []
    for ids in groups:
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if frozenset((a, b)) in duplicate_pairs:
                    continue
                if authors_by_paper.get(a, set()) & authors_by_paper.get(b, set()):
                    continue
                pairs.append((a, b))
    if logger_:
        logger_.info("%d cross-author, non-duplicate-registered pair(s) to shingle-scan", len(pairs))

    results = []
    degenerate = []
    for i, (a, b) in enumerate(pairs):
        if logger_ and i and i % 200 == 0:
            logger_.info("%d/%d pair(s) scanned, %d candidate(s) found so far", i, len(pairs), len(results))
        words_a = load_paper_words(conn, a)
        words_b = load_paper_words(conn, b)
        if not words_a or not words_b:
            continue
        runs = find_shingle_matches(words_a, words_b, shingle_size=shingle_size)
        total_words = sum(r[0] for r in runs)
        # Sanity check: total matched words landing somewhat above the shorter paper's own length
        # is legitimate (e.g. a repeated phrase inside one paper separately matching two spots in
        # the other counts twice) -- confirmed against this project's own already-verified cases,
        # which land as high as ~1.14x. An order-of-magnitude-plus blowup is not: find_shingle_
        # matches() has hit a real bug on a pair this large/self-similar (confirmed for real: a
        # paper-vs-its-author's-own-100K-word PhD thesis pair came back reporting 21.8 million
        # matched words, ~207x either paper's length). Not fixed here -- see todo.md's
        # "find_shingle_matches() degenerate on large/highly-repetitive pairs" entry. The 5x
        # cutoff is chosen only to sit well clear of both known-good ratios and the one known-bad
        # one -- not independently calibrated. Flagged separately rather than silently dropped,
        # since a pair that triggers this is usually a genuine large overlap (e.g. a thesis
        # legitimately incorporating its own author's earlier paper) the shingle *counting* can't
        # be trusted for, not a non-match.
        if total_words > 5 * min(len(words_a), len(words_b)):
            if logger_:
                logger_.warning("degenerate shingle count for pair (%d,%d): total_words=%d exceeds "
                                 "shorter paper's %d words -- find_shingle_matches() bug, not a real "
                                 "count; flagging separately instead of reporting it", a, b, total_words,
                                 min(len(words_a), len(words_b)))
            degenerate.append((a, b, len(words_a), len(words_b)))
            continue
        if total_words < min_total_words:
            continue
        title_a = conn.execute("SELECT title FROM papers WHERE id=?", (a,)).fetchone()[0]
        doi_a = conn.execute("SELECT doi FROM papers WHERE id=?", (a,)).fetchone()[0]
        doi_b = conn.execute("SELECT doi FROM papers WHERE id=?", (b,)).fetchone()[0]
        results.append({
            "paper_id_1": a, "paper_id_2": b, "title": title_a, "doi_1": doi_a, "doi_2": doi_b,
            "num_runs": len(runs), "total_words": total_words,
            "longest_run": max((r[0] for r in runs), default=0),
        })

    results.sort(key=lambda r: -r["total_words"])
    return results, degenerate


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--min-title-words", type=int, default=DEFAULT_MIN_TITLE_WORDS)
    parser.add_argument("--max-group-size", type=int, default=DEFAULT_MAX_GROUP_SIZE)
    parser.add_argument("--shingle-size", type=int, default=DEFAULT_SHINGLE_SIZE)
    parser.add_argument("--min-total-words", type=int, default=DEFAULT_MIN_TOTAL_WORDS,
                         help="heuristic noise floor, not calibrated -- see module docstring")
    parser.add_argument("--out", type=Path, default=Path("title_bucket_dupes_report.txt"))
    args = parser.parse_args()

    # Set up here, not at module level: a bare module-level logging.basicConfig() call would
    # silently break any OTHER script's own logging setup if it ever imports this module (a real
    # bug found and fixed elsewhere in this project this session -- see todo.md).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    conn = db.connect(args.library_db)
    results, degenerate = find_candidates(conn, args.min_title_words, args.max_group_size,
                                           args.shingle_size, args.min_total_words, logger_=logger)

    lines = []
    def out(s=""):
        print(s)
        lines.append(s)

    out(f"=== {len(results)} title-bucketed, cross-author candidate(s) with >= {args.min_total_words} "
        f"matched word(s) at shingle-size {args.shingle_size} ===\n")
    for r in results:
        out(f"paper_id=({r['paper_id_1']},{r['paper_id_2']}) runs={r['num_runs']} "
            f"total_words={r['total_words']} longest_run={r['longest_run']}")
        out(f"  title: {r['title'][:100]!r}")
        out(f"  doi_1: {r['doi_1']}")
        out(f"  doi_2: {r['doi_2']}")
        out("")

    if degenerate:
        out(f"=== {len(degenerate)} pair(s) find_shingle_matches() couldn't produce a trustable count "
            f"for (see module docstring) -- likely real overlap, needs a manual look, not a non-match ===\n")
        for a, b, len_a, len_b in degenerate:
            out(f"paper_id=({a},{b}) word_counts=({len_a},{len_b})")
        out("")

    args.out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("wrote %s", args.out)
    conn.close()


if __name__ == "__main__":
    main()
