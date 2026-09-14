#!/usr/bin/env python3
"""Characterize what's actually inside the LSH index's biggest buckets --
built to check the hypothesis behind todo.md's post-mortem 5 at scale,
rather than trusting the ~27-pair hand sample that post-mortem was based on.

Read-only, standalone -- doesn't touch lsh_buckets, potential_dupes, or
anything else. Safe to run alongside a live pipeline (connects read-only via
db.py's WAL mode).

What it does:
  1. Groups lsh_buckets by (table_num, bucket_key), ranks by occupancy, and
     reports the overall size distribution (so "what does max_bucket_size=N
     actually cut off" has a real number behind it instead of a guess).
  2. For the biggest N buckets, samples a handful of member paragraphs and
     classifies the sample as boilerplate-looking (matches a marker list of
     common license/disclosure/methodology-template phrases -- same markers
     used by hand during the post-mortem 5 investigation) vs. not, plus how
     many distinct papers and how many distinct exact texts the sample spans
     (a bucket that's one exact sentence repeated across 500 different papers
     reads very differently from one with 500 genuinely different papers'
     worth of varied text that merely embeds similarly).
  3. Prints a candidate-pair-count projection at a few different
     --max-bucket-size values (15/30/50/100/300), so the actual recall/safety
     tradeoff from lowering the cap (post-mortem 1) is visible against
     TODAY's corpus size, not the ~330K-paragraph corpus it was tuned on.

This is read-only reconnaissance, not a fix -- see todo.md post-mortem 5 for
what would actually need to change (a content-based boilerplate exclusion
list, not a size threshold).
"""

import argparse
import logging
from pathlib import Path

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("inspect_lsh_buckets")

BOILERPLATE_MARKERS = [
    "creative commons", "competing interests", "open access", "author contributions",
    "received:", "accepted:", "published:", "copyright", "editor:", "availability of data",
    "©", "ethics approval", "funding", "conflict of interest", "acknowledg",
    "please cite this paper as", "electronic supplementary material", "all rights reserved",
]

DEFAULT_TOP_N = 40
DEFAULT_SAMPLE_SIZE = 6
DEFAULT_CANDIDATE_CAPS = (15, 30, 50, 100, 300)


def bucket_size_distribution(conn, logger_=None):
    """Full histogram of (table_num, bucket_key) occupancy across lsh_buckets.
    One GROUP BY over the whole table -- the WITHOUT ROWID primary key is
    already ordered by (table_num, bucket_key, paragraph_id), so this streams
    rather than needing a separate sort."""
    if logger_:
        logger_.info("computing bucket size distribution (one pass over lsh_buckets)...")
    rows = conn.execute(
        "SELECT table_num, bucket_key, COUNT(*) AS n FROM lsh_buckets GROUP BY table_num, bucket_key"
    ).fetchall()
    return rows  # list of (table_num, bucket_key, n)


def projected_pairs_at_cap(sizes, cap):
    """Sum of C(n,2) over every group with 2 <= n <= cap -- what
    scan_candidate_pairs()'s full-rescan pass-1 would project at that
    --max-bucket-size, without actually doing the expensive pass-2 work."""
    total = 0
    kept_groups = 0
    skipped_groups = 0
    for n in sizes:
        if n < 2:
            continue
        if n > cap:
            skipped_groups += 1
            continue
        kept_groups += 1
        total += n * (n - 1) // 2
    return total, kept_groups, skipped_groups


def sample_bucket(conn, table_num, bucket_key, sample_size):
    rows = conn.execute(
        """
        SELECT p.text, p.paper_id, pp.title
        FROM lsh_buckets b
        JOIN paragraphs p ON p.id = b.paragraph_id
        JOIN papers pp ON pp.id = p.paper_id
        WHERE b.table_num = ? AND b.bucket_key = ?
        LIMIT ?
        """,
        (table_num, bucket_key, sample_size),
    ).fetchall()
    return rows


def classify_sample(rows):
    texts = [r[0] for r in rows]
    papers = {r[1] for r in rows}
    distinct_texts = len(set(texts))
    boiler_hits = sum(
        1 for t in texts if any(m in t.lower()[:200] for m in BOILERPLATE_MARKERS)
    )
    looks_boilerplate = boiler_hits >= max(1, len(texts) // 2)
    return {
        "distinct_papers_in_sample": len(papers),
        "distinct_texts_in_sample": distinct_texts,
        "boilerplate_marker_hits": boiler_hits,
        "looks_boilerplate": looks_boilerplate,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="How many of the biggest buckets to sample")
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE, help="Paragraphs sampled per bucket")
    parser.add_argument("--out", type=Path, default=Path("lsh_bucket_report.txt"))
    args = parser.parse_args()

    conn = db.connect(args.library_db)

    rows = bucket_size_distribution(conn, logger)
    sizes = [n for _, _, n in rows]
    sizes_sorted = sorted(sizes, reverse=True)
    total_buckets = len(sizes)
    total_paragraph_slots = sum(sizes)
    multi_member = [n for n in sizes if n >= 2]

    lines = []
    def out(s=""):
        print(s)
        lines.append(s)

    out("=== LSH bucket size distribution ===")
    out(f"total (table_num, bucket_key) slots occupied: {total_buckets:,}")
    out(f"total paragraph-slot rows (sum of occupancy): {total_paragraph_slots:,}")
    out(f"buckets with >=2 members (candidate-eligible): {len(multi_member):,}")
    if sizes_sorted:
        out(f"max bucket size: {sizes_sorted[0]:,}")
        out(f"p99: {sizes_sorted[max(0, len(sizes_sorted)//100)]:,}")
        out(f"median: {sizes_sorted[len(sizes_sorted)//2]:,}")
        out(f"mean: {total_paragraph_slots/total_buckets:.1f}")

    out("\n=== projected candidate pairs at various --max-bucket-size caps ===")
    for cap in DEFAULT_CANDIDATE_CAPS:
        total, kept, skipped = projected_pairs_at_cap(sizes, cap)
        out(f"  cap={cap:>4}: {total:>14,} projected pair(s)  ({kept:,} groups kept, {skipped:,} groups skipped as oversized)")

    out(f"\n=== top {args.top_n} biggest buckets: sampled content ===")
    biggest = sorted(rows, key=lambda r: -r[2])[:args.top_n]
    boiler_count = 0
    for table_num, bucket_key, n in biggest:
        sample = sample_bucket(conn, table_num, bucket_key, args.sample_size)
        info = classify_sample(sample)
        tag = "BOILERPLATE" if info["looks_boilerplate"] else "REVIEW"
        if info["looks_boilerplate"]:
            boiler_count += 1
        out(f"\n--- table={table_num} bucket={bucket_key} occupancy={n:,} [{tag}] "
            f"(sample: {info['distinct_papers_in_sample']} distinct papers, "
            f"{info['distinct_texts_in_sample']} distinct texts, "
            f"{info['boilerplate_marker_hits']}/{len(sample)} marker hits) ---")
        for text, paper_id, title in sample[:3]:
            out(f"    paper_id={paper_id} {title[:55]!r}: {text[:160]!r}")

    out("\n=== summary ===")
    out(f"{boiler_count}/{len(biggest)} of the top {args.top_n} biggest buckets look like boilerplate "
        f"by the marker heuristic; {len(biggest) - boiler_count} flagged REVIEW (worth a closer look).")

    args.out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("wrote %s", args.out)
    conn.close()


if __name__ == "__main__":
    main()
