#!/usr/bin/env python3
"""Copy the source PDFs of "legit" duplicate pairs into a separate bucket
directory for easy review/showcase, without touching papers/ itself.

"Legit" is deliberately stricter than build_dupe_candidates.py's own
persistence threshold: cosine similarity alone is a semantic measure and
can be fooled by same-topic-not-same-text pairs (see CLAUDE.md and
text_overlap.py's docstring -- todo.md's post-mortem has real examples of
0.92-0.97 cosine similarity pairs that turned out to share no actual
wording). So a pair only qualifies here if it clears similarity AND AT
LEAST ONE of text_overlap.py's two checks:
  - lcs_ratio       >= --min-lcs-ratio   (longest verbatim shared run)
  - ngram_jaccard   >= --min-ngram-jaccard (shingle/5-gram overlap)
Originally required BOTH; switched to OR after auditing the 15 real cases
that requiring-both was excluding -- every one was genuine same-author
self-reuse (an author paraphrasing/restating their own prior text, so no
single long verbatim run or dense n-gram block survives even though the
reuse is real and classify_dupes.py's independent same-author heuristic
already agreed). One strong signal is still real evidence; demanding both
was costing recall for a common, legitimate case without buying much
precision.
A candidate whose lcs_ratio/ngram_jaccard hasn't been computed yet (NULL --
predates a build_dupe_candidates.py run since that check was added, or the
LSH scan that surfaced this pair was incremental and never touched it --
see CLAUDE.md's --full-rescan) is treated as failing, not skipped-as-unknown:
we can't confirm textual overlap without it, so it's excluded rather than
guessed at.

Same-paper pairs (same_paper=1, e.g. a repeated figure caption within one
document) are never copied -- there is only one PDF, and it isn't a
"duplicate" in the sense this bucket is for. A pair a human has already
marked status='false_positive' via review_dupes.py is also excluded, even
if the metrics pass -- that verdict should win.

Copies (not moves, not symlinks) both papers' PDFs so the bucket is
self-contained and safe to hand someone without the rest of the library.
Idempotent: a file already present at the destination (by name) is not
re-copied. Also writes/refreshes a manifest CSV of exactly which pairs
justified which files, so "why is this PDF in here" is always answerable.

This is additive-only by design: a file already copied is never removed
even if a later run's thresholds would no longer include it (e.g. after a
--recheck lowers ai_check verdicts or a human reverses a review) -- delete
from --out-dir by hand if that's actually wanted.
"""

from __future__ import annotations

import argparse
import csv
import logging
import shutil
from pathlib import Path

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("copy_dupe_pdfs")

DEFAULT_MIN_LCS_RATIO = 0.3
DEFAULT_MIN_NGRAM_JACCARD = 0.2


def qualifying_pairs(conn, min_similarity, min_lcs_ratio, min_ngram_jaccard):
    return conn.execute(
        """
        SELECT
            pd.id, pd.similarity, pd.lcs_ratio, pd.ngram_jaccard,
            p1.id, p1.file_path, p1.title,
            p2.id, p2.file_path, p2.title
        FROM potential_dupes pd
        JOIN papers p1 ON p1.id = pd.paper_id_1
        JOIN papers p2 ON p2.id = pd.paper_id_2
        WHERE pd.same_paper = 0
          AND pd.status != 'false_positive'
          AND pd.similarity >= ?
          AND ((pd.lcs_ratio IS NOT NULL AND pd.lcs_ratio >= ?)
            OR (pd.ngram_jaccard IS NOT NULL AND pd.ngram_jaccard >= ?))
        """,
        (min_similarity, min_lcs_ratio, min_ngram_jaccard),
    ).fetchall()


def copy_if_new(src: Path, out_dir: Path) -> tuple[Path, bool]:
    dest = out_dir / src.name
    if dest.exists():
        return dest, False
    if not src.exists():
        logger.warning("source PDF missing, skipping: %s", src)
        return dest, False
    shutil.copy2(src, dest)
    return dest, True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("flagged_dupe_pdfs"))
    parser.add_argument("--min-similarity", type=float, default=0.85,
                         help="Cosine similarity floor (default: 0.85, matches build_dupe_candidates.py's own default)")
    parser.add_argument("--min-lcs-ratio", type=float, default=DEFAULT_MIN_LCS_RATIO,
                         help=f"Longest-verbatim-run floor (default: {DEFAULT_MIN_LCS_RATIO})")
    parser.add_argument("--min-ngram-jaccard", type=float, default=DEFAULT_MIN_NGRAM_JACCARD,
                         help=f"5-gram overlap floor (default: {DEFAULT_MIN_NGRAM_JACCARD})")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(args.library_db)

    rows = qualifying_pairs(conn, args.min_similarity, args.min_lcs_ratio, args.min_ngram_jaccard)
    logger.info("%d qualifying pair(s) (similarity>=%.2f, lcs_ratio>=%.2f, ngram_jaccard>=%.2f)",
                len(rows), args.min_similarity, args.min_lcs_ratio, args.min_ngram_jaccard)

    manifest_path = args.out_dir / "_manifest.csv"
    manifest_rows = []
    copied_files = set()
    new_copies = 0

    for (pd_id, similarity, lcs_ratio, ngram_jaccard,
         paper_id_1, file_path_1, title_1,
         paper_id_2, file_path_2, title_2) in rows:
        dest_1, is_new_1 = copy_if_new(Path(file_path_1), args.out_dir)
        dest_2, is_new_2 = copy_if_new(Path(file_path_2), args.out_dir)
        new_copies += is_new_1 + is_new_2
        copied_files.add(dest_1.name)
        copied_files.add(dest_2.name)
        manifest_rows.append({
            "potential_dupe_id": pd_id,
            "similarity": round(similarity, 4),
            "lcs_ratio": round(lcs_ratio, 4),
            "ngram_jaccard": round(ngram_jaccard, 4),
            "paper_id_1": paper_id_1, "title_1": title_1, "pdf_1": dest_1.name,
            "paper_id_2": paper_id_2, "title_2": title_2, "pdf_2": dest_2.name,
        })

    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "potential_dupe_id", "similarity", "lcs_ratio", "ngram_jaccard",
            "paper_id_1", "title_1", "pdf_1", "paper_id_2", "title_2", "pdf_2",
        ])
        writer.writeheader()
        writer.writerows(manifest_rows)

    logger.info("%d file(s) newly copied, %d unique PDF(s) referenced by %d qualifying pair(s)",
                new_copies, len(copied_files), len(rows))
    logger.info("manifest: %s", manifest_path)


if __name__ == "__main__":
    main()
