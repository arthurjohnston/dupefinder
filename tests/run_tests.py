#!/usr/bin/env python3
"""Back-test find_duplicates.py against known, documented plagiarism cases.

Each case in tests/cases/*.json lists the papers involved (title/authors/year,
same shape as starting.json) plus a set of assertions about what
find_duplicates.py should (or shouldn't) find once the full pipeline has run
on them. This is what todo.md's "back testing" item asks for: prove the
approach works on ground truth before scaling up retrieval.

For each case, this script:
  1. Builds an isolated working directory (tests/work/<case-name>/) so cases
     never share state or pollute the main library.
  2. Runs retrieve_papers.py on the case's papers.
  3. For any paper retrieve_papers.py can't resolve on its own (a case's
     "manual" block -- the same situation documented in CLAUDE.md's "Known
     gap" for retrieve_papers.py: Crossref/Unpaywall missing an arXiv/PMLR
     preprint), fetches the given URL directly and records it in state.sqlite3
     via retrieve_papers.py's own PaperStore, exactly as done by hand.
  4. Runs extract_papers.py and embed_paragraphs.py.
  5. Checks each assertion against the resulting paragraph embeddings and
     reports PASS/FAIL.

Usage:
    python3 tests/run_tests.py --email you@example.com
    python3 tests/run_tests.py --email you@example.com --case carlini-roadmap-2022
    python3 tests/run_tests.py --email you@example.com --keep-work   # inspect DBs after
    python3 tests/run_tests.py --email you@example.com --refetch     # force a clean re-download

Downloaded PDFs + state.sqlite3 (tests/work/<case>/papers/, state.sqlite3) are
a cache, not run output: they're left in place after every run (regardless of
--keep-work) so re-running -- e.g. to check an extract_papers.py change --
never re-hits the network for papers already fetched. retrieve_papers.py's
own idempotency (see CLAUDE.md) does the actual skip-if-downloaded work; this
script just stops deleting that cache out from under it. Only
library.sqlite3/paragraphs.jsonl (derived from the PDFs, cheap to rebuild
locally) are cleared between runs, so extraction/embedding always reflect the
current code. Pass --refetch to wipe the download cache too, e.g. to confirm
retrieval still works from scratch.

Exit code is 0 iff every assertion in every case passed.
"""

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import retrieve_papers  # noqa: E402
import extract_papers  # noqa: E402
import embed_paragraphs  # noqa: E402
import find_duplicates  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("run_tests")

# retrieve_papers.py attaches its own console+file handlers to the "retrieve_papers" logger
# (see setup_logger()); since it also propagates to root by default and extract_papers.py's
# import already gave root a handler, every line would otherwise print twice.
logging.getLogger("retrieve_papers").propagate = False


def run_with_argv(module, argv):
    """Call module.main() with sys.argv patched -- reuses each script's own
    argparse + logic exactly as the CLI would, instead of reimplementing it."""
    old_argv = sys.argv
    sys.argv = argv
    try:
        module.main()
    finally:
        sys.argv = old_argv


def fetch_manual_paper(paper, work_dir, email):
    """Directly fetch a paper retrieve_papers.py couldn't resolve on its own,
    and record it in state.sqlite3 the same way a human would after
    verifying it by hand (see CLAUDE.md's retrieve_papers.py "Known gap").

    Two forms of "manual" are supported:
      - "pdf_url": fetched over HTTP, same as retrieve_papers.py would.
      - "local_path" (repo-root-relative): copied in directly, no network
        call at all -- for cases built from a PDF that was vetted and placed
        by hand (e.g. a dissertation with no Crossref/Unpaywall record to
        even attempt), so re-running the suite never re-fetches it.
    """
    manual = paper["manual"]
    key = retrieve_papers.make_key(paper)  # paper has no "doi" field -> title-based key, same as starting.json entries
    store = retrieve_papers.PaperStore(work_dir / "state.sqlite3")

    record = store.get(key)
    if record and record.get("status") == "downloaded" and record.get("file_path") and Path(record["file_path"]).exists():
        return  # retrieve_papers.py already got it on its own; nothing to do

    outdir = work_dir / "papers"
    outdir.mkdir(parents=True, exist_ok=True)
    filename = f"{retrieve_papers.slugify(paper['title'])}-{retrieve_papers.slugify(manual.get('doi') or 'manual')}.pdf"
    dest_path = outdir / filename

    if "local_path" in manual:
        src_path = ROOT / manual["local_path"]
        if not src_path.exists():
            raise RuntimeError(f"local_path {src_path} for {paper['title']!r} does not exist")
        shutil.copyfile(src_path, dest_path)
        pdf_url = manual.get("pdf_url")  # optional here -- purely informational, nothing is fetched from it
        logger.info("copied local file for %r -> %s", paper["title"], dest_path.name)
    else:
        session = requests.Session()
        session.headers.update({"User-Agent": retrieve_papers.USER_AGENT_TEMPLATE.format(email=email)})
        rate_limiter = retrieve_papers.RateLimiter(retrieve_papers.DEFAULT_MIN_INTERVAL)
        args = argparse.Namespace(max_retries=retrieve_papers.DEFAULT_MAX_RETRIES, timeout=retrieve_papers.DEFAULT_TIMEOUT)

        ok = retrieve_papers.download_pdf(session, manual["pdf_url"], dest_path, rate_limiter, args, logger)
        if not ok:
            raise RuntimeError(f"manual fetch of {manual['pdf_url']!r} for {paper['title']!r} did not serve a PDF")
        pdf_url = manual["pdf_url"]
        logger.info("manually fetched %r -> %s", paper["title"], dest_path.name)

    store.upsert(
        key, title=paper["title"], authors=json.dumps(paper.get("authors")), year=paper.get("year"),
        doi=manual.get("doi"), status="downloaded", oa_status=manual.get("oa_status", "manual"),
        pdf_url=pdf_url, file_path=str(dest_path), error=None,
    )


def run_pipeline(case, work_dir, email):
    work_dir.mkdir(parents=True, exist_ok=True)
    starting = [{"title": p["title"], "authors": p.get("authors"), "year": p.get("year")} for p in case["papers"]]
    starting_path = work_dir / "starting.json"
    starting_path.write_text(json.dumps(starting, indent=2))

    run_with_argv(retrieve_papers, [
        "retrieve_papers.py", str(starting_path), "--email", email,
        "--outdir", str(work_dir / "papers"), "--db", str(work_dir / "state.sqlite3"),
        "--log-file", str(work_dir / "retrieve.log"),
    ])

    for paper in case["papers"]:
        if "manual" in paper:
            fetch_manual_paper(paper, work_dir, email)

    # library.sqlite3/paragraphs.jsonl are derived purely from the (cached)
    # downloaded PDFs -- cheap and local to rebuild -- so they're cleared on
    # every run to make sure extraction/embedding reflect the current code,
    # never a stale run's leftovers.
    for name in ("library.sqlite3", "paragraphs.jsonl"):
        stale = work_dir / name
        if stale.exists():
            stale.unlink()

    run_with_argv(extract_papers, [
        "extract_papers.py", "--state-db", str(work_dir / "state.sqlite3"),
        "--library-db", str(work_dir / "library.sqlite3"), "--paragraphs-file", str(work_dir / "paragraphs.jsonl"),
    ])

    run_with_argv(embed_paragraphs, [
        "embed_paragraphs.py", "--library-db", str(work_dir / "library.sqlite3"),
        "--paragraphs-file", str(work_dir / "paragraphs.jsonl"),
    ])


def max_similarity_between(library_db, title_a, title_b):
    conn = db.connect(library_db)
    rows = find_duplicates.load_paragraphs(conn)
    conn.close()

    rows_a = [r for r in rows if r[5] == title_a]
    rows_b = [r for r in rows if r[5] == title_b]
    if not rows_a or not rows_b:
        missing = title_a if not rows_a else title_b
        raise RuntimeError(f"no embedded paragraphs found for {missing!r} -- did extraction/embedding fail?")

    mat_a = find_duplicates.to_matrix(rows_a)
    mat_b = find_duplicates.to_matrix(rows_b)
    sims = mat_a @ mat_b.T
    i, j = divmod(int(sims.argmax()), sims.shape[1])
    return float(sims[i, j]), rows_a[i], rows_b[j]


def check_assertions(case, work_dir):
    results = []
    for assertion in case["assertions"]:
        title_a, title_b = assertion["pair"]
        score, row_a, row_b = max_similarity_between(work_dir / "library.sqlite3", title_a, title_b)
        threshold = assertion["threshold"]

        if assertion["type"] == "min_similarity":
            passed = score >= threshold
            detail = f"best match {score:.3f} {'>=' if passed else '<'} required {threshold:.2f}"
        elif assertion["type"] == "max_similarity":
            passed = score <= threshold
            detail = f"best match {score:.3f} {'<=' if passed else '>'} required max {threshold:.2f}"
        else:
            raise ValueError(f"unknown assertion type {assertion['type']!r}")

        results.append({
            "passed": passed, "detail": detail, "score": score,
            "pair": (title_a, title_b), "paragraphs": (row_a[2], row_b[2]),  # para_index of each side's best match
        })
    return results


def parse_args():
    parser = argparse.ArgumentParser(description="Back-test find_duplicates.py against known plagiarism cases.")
    parser.add_argument("--email", required=True, help="Contact email forwarded to retrieve_papers.py")
    parser.add_argument("--cases-dir", type=Path, default=Path(__file__).parent / "cases")
    parser.add_argument("--work-dir", type=Path, default=Path(__file__).parent / "work")
    parser.add_argument("--case", help="Run only the case with this name (default: all cases)")
    parser.add_argument("--keep-work", action="store_true",
                         help="Don't delete library.sqlite3/paragraphs.jsonl after the run (inspect DBs after). "
                              "Downloaded PDFs/state.sqlite3 are always kept regardless of this flag.")
    parser.add_argument("--refetch", action="store_true",
                         help="Wipe the whole per-case work dir first, including cached downloads, "
                              "forcing a clean re-download from the network")
    return parser.parse_args()


def main():
    args = parse_args()
    case_files = sorted(args.cases_dir.glob("*.json"))
    if args.case:
        case_files = [f for f in case_files if f.stem == args.case]
        if not case_files:
            print(f"no case named {args.case!r} in {args.cases_dir}")
            sys.exit(2)

    all_passed = True
    print(f"running {len(case_files)} case(s)\n")

    for case_file in case_files:
        case = json.loads(case_file.read_text())
        work_dir = args.work_dir / case["name"]
        if args.refetch and work_dir.exists():
            shutil.rmtree(work_dir)

        print(f"=== {case['name']} ===")
        print(case["description"])
        try:
            run_pipeline(case, work_dir, args.email)
            results = check_assertions(case, work_dir)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            all_passed = False
            continue

        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            all_passed = all_passed and r["passed"]
            a, b = r["pair"]
            print(f"  [{status}] {r['detail']}")
            print(f"         {a} (¶{r['paragraphs'][0]}) <-> {b} (¶{r['paragraphs'][1]})")

        if not args.keep_work:
            for name in ("library.sqlite3", "paragraphs.jsonl"):
                stale = work_dir / name
                if stale.exists():
                    stale.unlink()
        print()

    print("ALL PASSED" if all_passed else "SOME FAILED")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
