#!/usr/bin/env python3
"""Run [retrieve_papers.py ->] extract_papers.py -> embed_paragraphs.py ->
build_dupe_candidates.py -> classify_dupes.py -> write_dupe_reports.py [->
copy_dupe_pdfs.py] in sequence -- the deterministic "process whatever's been
retrieved" half of CLAUDE.md's pipeline diagram.

retrieve_papers.py itself stays optional and off by default (pass
--retrieve-input, plus --retrieve-email since retrieve_papers.py requires
one) -- bulk_retrieve_arxiv.py/bulk_retrieve_crossref.py have different
argument shapes and aren't chained the same way, and this script still
doesn't care which method populated state.sqlite3/papers/ if you'd rather
run retrieval yourself first, same as before. When given, it runs first and
writes into the same --state-db this script already passes to
extract_papers.py, so newly retrieved papers flow straight into extract
without a separate manual step.

classify_dupes.py and write_dupe_reports.py are included specifically so
this isn't a repeat of the gap that prompted adding them: build_dupe_candidates.py
alone leaves every new candidate's ai_check NULL, and write_dupe_reports.py
only looks at ai_check='yes' rows, so without these two steps a fresh batch
of papers would silently produce zero new reports no matter how many real
candidates it found. See todo.md's "AI pre-filter pass" section and
classify_dupes.py's docstring for what the automated classification pass can
and can't do (it leaves same_author=0 candidates it can't pattern-match as
NULL for a human -- or another AI pass -- to actually look at, rather than
guessing).

copy_dupe_pdfs.py runs last (on by default -- --skip-copy-pdfs opts out):
copies the source PDFs of pairs that clear a similarity floor plus at least
one of text_overlap.py's lcs_ratio/ngram_jaccard checks (not cosine
similarity alone -- see that script's docstring for why, and for why it's
OR rather than AND) into --dupe-pdfs-out-dir, for an at-a-glance "here are
this run's likely real duplicates" folder without digging through
dupe_reports/ or potential_dupes by hand.

Each stage runs via its own main() with sys.argv patched -- the same
run_with_argv() pattern tests/run_tests.py already uses -- so every stage
gets its own real argument parsing/defaults/logging exactly as running it
directly would, not a subprocess wrapper reimplementing any of that. Safe to
chain this way because all these scripts log through plain
logging.getLogger(name) with no extra handlers of their own (unlike
retrieve_papers.py's setup_logger(), which tests/run_tests.py has to
propagate=False around for exactly this reason -- not a concern here).

Stops at the first stage that raises -- letting embed_paragraphs.py run
against an incomplete extract, or build_dupe_candidates.py run against
incomplete embeddings, would silently work on partial data rather than fail
loudly, which is worse than just stopping.

All stages are independently idempotent/resumable (see CLAUDE.md), so
re-running this after a stopped/partial run is always safe -- each stage
just picks up whatever it didn't finish last time.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import build_dupe_candidates
import classify_dupes
import copy_dupe_pdfs
import embed_paragraphs
import extract_papers
import retrieve_papers
import write_dupe_reports

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("run_pipeline")

# retrieve_papers.py attaches its own console+file handlers (see its setup_logger())
# and also propagates to root by default; without this it would double-log everything
# through this module's basicConfig handler too. Same fix tests/run_tests.py uses.
logging.getLogger("retrieve_papers").propagate = False


def run_with_argv(module, argv, label):
    logger.info("=== starting %s ===", label)
    old_argv = sys.argv
    sys.argv = argv
    start = time.time()
    try:
        module.main()
    finally:
        sys.argv = old_argv
    logger.info("=== %s done in %.1fs ===", label, time.time() - start)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run extract_papers.py -> embed_paragraphs.py -> build_dupe_candidates.py -> "
                     "classify_dupes.py -> write_dupe_reports.py in sequence."
    )
    parser.add_argument("--state-db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--paragraphs-file", type=Path, default=Path("paragraphs.jsonl"))
    parser.add_argument("--retrieve-input", type=Path, default=None,
                         help="If set, run retrieve_papers.py on this JSON file first "
                              "(requires --retrieve-email), writing into --state-db.")
    parser.add_argument("--retrieve-email", default=None,
                         help="Contact email forwarded to retrieve_papers.py; required if --retrieve-input is set.")
    parser.add_argument("--retrieve-outdir", type=Path, default=Path("papers"),
                         help="passed through to retrieve_papers.py as --outdir")
    parser.add_argument("--model", default=embed_paragraphs.DEFAULT_MODEL)
    parser.add_argument("--threshold", type=float, default=build_dupe_candidates.DEFAULT_THRESHOLD,
                         help="build_dupe_candidates.py's similarity threshold")
    parser.add_argument("--max-bucket-size", type=int, default=None,
                         help="passed through to build_dupe_candidates.py if set (default: its own default)")
    parser.add_argument("--full-rescan", action="store_true", help="passed through to build_dupe_candidates.py")
    parser.add_argument("--reports-out-dir", type=Path, default=Path("dupe_reports"))
    parser.add_argument("--reports-min-count", type=int, default=write_dupe_reports.DEFAULT_MIN_COUNT,
                         help="passed through to write_dupe_reports.py")
    parser.add_argument("--skip-extract", action="store_true", help="paragraphs.jsonl is already current")
    parser.add_argument("--skip-embed", action="store_true", help="embeddings are already current")
    parser.add_argument("--skip-build-dupe", action="store_true", help="stop after embedding")
    parser.add_argument("--skip-classify", action="store_true", help="stop after build_dupe_candidates.py")
    parser.add_argument("--skip-reports", action="store_true", help="stop after classify_dupes.py, don't (re)write dupe_reports/")
    parser.add_argument("--dupe-pdfs-out-dir", type=Path, default=Path("flagged_dupe_pdfs"),
                         help="passed through to copy_dupe_pdfs.py as --out-dir")
    parser.add_argument("--dupe-pdfs-min-similarity", type=float, default=0.85,
                         help="passed through to copy_dupe_pdfs.py as --min-similarity")
    parser.add_argument("--dupe-pdfs-min-lcs-ratio", type=float, default=copy_dupe_pdfs.DEFAULT_MIN_LCS_RATIO,
                         help="passed through to copy_dupe_pdfs.py as --min-lcs-ratio")
    parser.add_argument("--dupe-pdfs-min-ngram-jaccard", type=float, default=copy_dupe_pdfs.DEFAULT_MIN_NGRAM_JACCARD,
                         help="passed through to copy_dupe_pdfs.py as --min-ngram-jaccard")
    parser.add_argument("--skip-copy-pdfs", action="store_true", help="don't run copy_dupe_pdfs.py at the end")
    args = parser.parse_args()
    if args.retrieve_input and not args.retrieve_email:
        parser.error("--retrieve-email is required when --retrieve-input is set")
    return args


def main():
    args = parse_args()
    overall_start = time.time()

    if args.retrieve_input:
        run_with_argv(retrieve_papers, [
            "retrieve_papers.py", str(args.retrieve_input),
            "--email", args.retrieve_email,
            "--outdir", str(args.retrieve_outdir),
            "--db", str(args.state_db),
        ], "retrieve_papers.py")
    else:
        logger.info("no --retrieve-input given: not running retrieve_papers.py, using %s as-is", args.state_db)

    if not args.skip_extract:
        run_with_argv(extract_papers, [
            "extract_papers.py", "--state-db", str(args.state_db),
            "--library-db", str(args.library_db), "--paragraphs-file", str(args.paragraphs_file),
        ], "extract_papers.py")
    else:
        logger.info("--skip-extract: leaving %s as-is", args.paragraphs_file)

    if not args.skip_embed:
        run_with_argv(embed_paragraphs, [
            "embed_paragraphs.py", "--library-db", str(args.library_db),
            "--paragraphs-file", str(args.paragraphs_file), "--model", args.model,
        ], "embed_paragraphs.py")
    else:
        logger.info("--skip-embed: leaving embeddings as-is")

    if not args.skip_build_dupe:
        build_argv = [
            "build_dupe_candidates.py", "--library-db", str(args.library_db), "--threshold", str(args.threshold),
        ]
        if args.max_bucket_size is not None:
            build_argv += ["--max-bucket-size", str(args.max_bucket_size)]
        if args.full_rescan:
            build_argv.append("--full-rescan")
        run_with_argv(build_dupe_candidates, build_argv, "build_dupe_candidates.py")
    else:
        logger.info("--skip-build-dupe: stopping after embed_paragraphs.py")
        logger.info("pipeline complete in %.1fs total", time.time() - overall_start)
        return

    if not args.skip_classify:
        run_with_argv(classify_dupes, [
            "classify_dupes.py", "--library-db", str(args.library_db),
        ], "classify_dupes.py")
    else:
        logger.info("--skip-classify: stopping after build_dupe_candidates.py")
        logger.info("pipeline complete in %.1fs total", time.time() - overall_start)
        return

    if not args.skip_reports:
        run_with_argv(write_dupe_reports, [
            "write_dupe_reports.py", "--library-db", str(args.library_db),
            "--out-dir", str(args.reports_out_dir), "--min-count", str(args.reports_min_count),
        ], "write_dupe_reports.py")
    else:
        logger.info("--skip-reports: stopping after classify_dupes.py")
        logger.info("pipeline complete in %.1fs total", time.time() - overall_start)
        return

    if not args.skip_copy_pdfs:
        run_with_argv(copy_dupe_pdfs, [
            "copy_dupe_pdfs.py", "--library-db", str(args.library_db),
            "--out-dir", str(args.dupe_pdfs_out_dir),
            "--min-similarity", str(args.dupe_pdfs_min_similarity),
            "--min-lcs-ratio", str(args.dupe_pdfs_min_lcs_ratio),
            "--min-ngram-jaccard", str(args.dupe_pdfs_min_ngram_jaccard),
        ], "copy_dupe_pdfs.py")
    else:
        logger.info("--skip-copy-pdfs: not copying flagged PDFs")

    logger.info("pipeline complete in %.1fs total", time.time() - overall_start)


if __name__ == "__main__":
    main()
