#!/usr/bin/env python3
"""Find near-duplicate paragraphs using the embeddings from embed_paragraphs.py.

Loads every embedded paragraph from library.sqlite3 and computes pairwise
cosine similarity via blocked matrix multiplies (embeddings are stored as
unit vectors, so cosine similarity is just the dot product; blocking avoids
ever materializing a full N x N matrix, which stops being affordable well
before N reaches this corpus's paragraph count). Pairs above --threshold
are reported, ranked most-similar first -- this is the "dupefinder" the
project is named for.

Similarity is a spectrum, not a duplicate/not-duplicate binary:
  ~0.95-1.00  near-verbatim (identical or lightly edited text)
  ~0.85-0.95  same claim/sentence reworded, or heavy boilerplate overlap
  ~0.60-0.85  same topic, different content -- not a duplicate
Start at the default and raise/lower --threshold depending on what you're
hunting for.
"""

import argparse
from pathlib import Path

import numpy as np

import db

DEFAULT_THRESHOLD = 0.92


def load_paragraphs(conn):
    return conn.execute(
        """
        SELECT pr.id, pr.paper_id, pr.para_index, pr.text, pr.embedding, p.title
        FROM paragraphs pr
        JOIN papers p ON p.id = pr.paper_id
        WHERE pr.embedding IS NOT NULL
        ORDER BY pr.id
        """
    ).fetchall()


def load_paragraphs_by_ids(conn, ids):
    """Same row shape as load_paragraphs(), but scoped to specific paragraph
    ids -- for build_dupe_candidates.py's LSH path, where the candidate set
    (a few thousand paragraphs at most) is known before any row data is
    needed, so there's no reason to pull the whole (possibly hundreds of
    thousands of rows) paragraphs table just for an id -> row lookup."""
    ids = list(ids)
    rows = []
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        placeholders = ",".join("?" * len(chunk))
        rows.extend(conn.execute(
            f"""
            SELECT pr.id, pr.paper_id, pr.para_index, pr.text, pr.embedding, p.title
            FROM paragraphs pr
            JOIN papers p ON p.id = pr.paper_id
            WHERE pr.id IN ({placeholders})
            """,
            chunk,
        ).fetchall())
    return rows


def to_matrix(rows):
    vectors = [np.frombuffer(row[4], dtype=np.float32) for row in rows]
    return np.vstack(vectors)


DEFAULT_BLOCK_MEMORY_BYTES = 1_500_000_000  # ~1.5GB budget for one block's transient sims matrix


def find_pairs(rows, mat, threshold, cross_paper_only, min_length, block_size=None):
    """Cosine similarity above --threshold, computed in row-blocks rather than
    one `mat @ mat.T` matmul. At this corpus's paragraph count (PyMuPDF's
    finer, paragraph-granular splitting puts the full corpus in the ~800K-900K
    range) a single N x N float32 matrix would need multiple terabytes of
    RAM -- infeasible. Blocking keeps a transient block_size x (n - i0) slice
    in memory instead (each block also only multiplies against columns >= its
    own start index, since similarity is symmetric and we only want the
    upper triangle -- same total pairs as before, half the multiplies).
    Results are identical to the old full-matrix version, just computed
    without ever materializing the whole thing at once.

    block_size defaults to None, meaning "pick it from a fixed memory budget"
    (DEFAULT_BLOCK_MEMORY_BYTES) rather than a fixed row count: a block's size
    in memory is block_size * (n - i0) * 4 bytes, dominated by n, not
    block_size -- a row count tuned against a small/synthetic n (e.g. 1000,
    tuned against a few thousand rows) silently stops being safe once n grows
    into the hundreds of thousands, where even the *first* block would need
    several GB. Auto-sizing from a byte budget keeps peak memory roughly
    constant regardless of how large the corpus gets.
    """
    n = len(rows)
    if block_size is None:
        block_size = max(64, min(n, DEFAULT_BLOCK_MEMORY_BYTES // max(n, 1) // 4))
    keep_mask = np.array([len(row[3]) >= min_length for row in rows])

    pairs = []
    for i0 in range(0, n, block_size):
        i1 = min(i0 + block_size, n)
        block_sims = mat[i0:i1] @ mat[i0:].T  # shape (i1-i0, n-i0); col c == actual index i0+c
        # Zero out the lower triangle/diagonal (j <= i) and anything below threshold in
        # one vectorized pass -- np.nonzero on the survivors is cheap, a Python-level loop
        # over every cell (up to ~n^2/2, easily hundreds of billions) would not finish.
        cols = np.arange(block_sims.shape[1])
        local_rows = np.arange(i1 - i0)[:, None]
        upper_triangle = cols[None, :] > local_rows  # j > i
        hits = upper_triangle & (block_sims >= threshold)
        local_is, local_js = np.nonzero(hits)
        for local_i, col in zip(local_is.tolist(), local_js.tolist()):
            i = i0 + local_i
            j = i0 + col
            if not (keep_mask[i] and keep_mask[j]):
                continue
            same_paper = rows[i][1] == rows[j][1]
            if cross_paper_only and same_paper:
                continue
            pairs.append((float(block_sims[local_i, col]), rows[i], rows[j], same_paper))

    pairs.sort(key=lambda x: x[0], reverse=True)
    return pairs


def snippet(text, length=140):
    text = " ".join(text.split())
    return text[:length] + ("…" if len(text) > length else "")


def parse_args():
    parser = argparse.ArgumentParser(description="Find near-duplicate paragraphs via embedding similarity.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                         help=f"minimum cosine similarity to report, 0-1 (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--top", type=int, default=30, help="max number of pairs to print")
    parser.add_argument("--cross-paper-only", action="store_true", help="only report pairs from different papers")
    parser.add_argument("--min-length", type=int, default=0, help="skip paragraphs shorter than N characters")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)  # other pipeline stages may be writing concurrently (see db.py: WAL mode)
    rows = load_paragraphs(conn)
    conn.close()

    if len(rows) < 2:
        print("Fewer than 2 embedded paragraphs -- nothing to compare. Run embed_paragraphs.py first.")
        return

    mat = to_matrix(rows)
    pairs = find_pairs(rows, mat, args.threshold, args.cross_paper_only, args.min_length)

    scope = " (cross-paper only)" if args.cross_paper_only else ""
    print(f"{len(rows)} paragraphs compared, {len(pairs)} pair(s) >= {args.threshold:.2f} similarity{scope}\n")

    for score, a, b, same_paper in pairs[:args.top]:
        _, _, idx_a, text_a, _, title_a = a
        _, _, idx_b, text_b, _, title_b = b
        tag = "SAME PAPER " if same_paper else "CROSS PAPER"
        print(f"[{score:.3f}] {tag}")
        print(f"  A: {title_a} (¶{idx_a}) -- {snippet(text_a)}")
        print(f"  B: {title_b} (¶{idx_b}) -- {snippet(text_b)}")
        print()

    if len(pairs) > args.top:
        print(f"... {len(pairs) - args.top} more pair(s) not shown (raise --top to see them)")


if __name__ == "__main__":
    main()
