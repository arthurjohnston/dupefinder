#!/usr/bin/env python3
"""Close the gap bulk_retrieve_retraction_watch.py's own docstring flags:
the Retraction Watch dataset has no structured field linking a plagiarizing
paper to what it plagiarized. This script finds that link a different way --
by reading the retraction NOTICE itself.

Confirmed for real before writing a line of matching logic (not assumed):
fetched a live retraction notice (10.1038/s41598-025-98710-9, a Nature
Scientific Reports "Retraction Note") and it explicitly named the source in
its own reference list -- "significant textual overlaps with previously-
published work with no common authors[1]" followed by a full citation with
DOI (10.1016/j.procs.2016.09.147). Retraction notices are short (usually one
page), typically openly licensed even when the retracted article itself
isn't, and journals writing them almost always cite what was copied from --
that's the whole point of the notice. This is a much better lever than
trying to identify the source from the plagiarizing paper's own text (which
is the thing being investigated, not a reliable witness).

Pipeline per case (from bulk_retrieve_retraction_watch.py's
retraction_watch_plagiarism_cases.json, or --csv-path/--reason to re-derive
the case list directly from the dataset):
  1. Resolve the retraction notice's own OA location (RetractionDOI, not the
     retracted paper's own DOI) via the same OpenAlex-batch-then-Unpaywall
     pipeline every other bulk_retrieve_*.py script uses.
  2. Download it to retraction_notices/ -- kept as evidence, deliberately
     NOT added to state.sqlite3/library.sqlite3's normal paper set (it's a
     short editorial notice, not a research paper; letting it into
     extract_papers.py's candidate set would just seed the corpus with
     retraction-notice boilerplate paragraphs, not real content).
  3. Extract its text (pdftotext) and regex-scan for DOI-shaped strings,
     excluding the case's own DOI and the notice's own DOI (self-references,
     not sources).
  4. Whatever DOIs remain are candidate sources -- fed through the normal
     bulk_retrieve_crossref.fetch_candidate() pipeline to actually download
     them into papers/ and state.sqlite3, same as any other retrieval.

Not every notice will name a source this cleanly (format varies by
publisher, some retractions cite multiple works, some notices are scanned
images with no extractable text) -- this is a real lever, not a guaranteed
resolution for every case. Results (which notice yielded which candidate
DOI(s), and whether the source was actually downloaded) are written to
--found-out for auditing, since a regex over free text is exactly the kind
of thing worth being able to spot-check.
"""

import argparse
import json
import logging
import re
import subprocess
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import bulk_retrieve_retraction_watch as brw
import retrieve_papers as rp

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>)\]]+")
CROSSREF_WORK_API = "https://api.crossref.org/works/{doi}"
DEFAULT_CASES_PATH = Path("retraction_watch_plagiarism_cases.json")
DEFAULT_FOUND_OUT = Path("retraction_watch_sources_found.json")
DEFAULT_NOTICES_DIR = Path("retraction_notices")

logger = logging.getLogger("find_plagiarism_sources")


def setup_logger(log_file: Path):
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)


def clean_doi(raw: str) -> str:
    """Strips trailing punctuation a sentence/citation commonly leaves stuck
    to a DOI (periods, semicolons, closing parens picked up by the regex's
    otherwise-greedy match) -- confirmed necessary against the real notice
    text used to validate this script (a DOI at the end of a reference line
    ending in a period)."""
    return raw.rstrip(".,;:)")


def extract_candidate_source_dois(text: str, exclude: set) -> list:
    found = []
    seen = set()
    for m in DOI_RE.finditer(text):
        doi = clean_doi(m.group(0)).lower()
        if doi in exclude or doi in seen:
            continue
        seen.add(doi)
        found.append(doi)
    return found


def load_cases(args):
    if args.cases_path.exists() and not args.refresh:
        logger.info("using existing case list %s", args.cases_path)
        by_doi = json.loads(args.cases_path.read_text())
        return list(by_doi.values())
    logger.info("no cached case list -- deriving from %s (Reason contains %r)", args.csv_path, args.reason)
    brw.fetch_csv(args.csv_path, args.refresh, args.timeout)
    return list(brw.load_plagiarism_cases(args.csv_path, args.reason))


def fetch_notice_pdf(session, case, notices_dir, unpaywall_limiter, openalex_limiter, download_limiter, args):
    """Returns the local Path of the downloaded notice PDF, or None if it
    couldn't be resolved/downloaded. Does not touch state.sqlite3 -- see
    module docstring for why notices are deliberately kept out of the
    normal paper-tracking table."""
    retraction_doi = rp.normalize_doi(case.get("retraction_doi", ""))
    if not retraction_doi:
        return None

    pdf_url = None
    hits = rp.query_openalex_batch(session, [retraction_doi], openalex_limiter, args, logger) \
        if not args.no_openalex else {}
    hit = hits.get(retraction_doi.lower())
    if hit:
        pdf_url = hit["pdf_url"]
    else:
        try:
            data = rp.query_unpaywall(session, retraction_doi, unpaywall_limiter, args, logger)
        except rp.RetrievalError:
            data = None
        if data:
            best_loc = data.get("best_oa_location") or {}
            pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")

    if not pdf_url:
        return None

    dest_path = notices_dir / f"{rp.slugify(retraction_doi)}.pdf"
    if dest_path.exists():
        return dest_path
    try:
        ok, _ = rp.download_with_landing_page_fallback(session, pdf_url, dest_path, download_limiter, args, logger)
    except rp.RetrievalError as exc:
        logger.warning("notice download failed for %s: %s", retraction_doi, exc)
        return None
    return dest_path if ok else None


def lookup_crossref_metadata(session, doi, rate_limiter, args, logger_):
    """A discovered source DOI comes from regex-scanning free text, not a
    search result -- unlike every other bulk_retrieve_*.py script's
    candidates, there's no title/authors/year already in hand for it, only
    the bare DOI. Storing the DOI itself as a placeholder title would leak
    into library.sqlite3 as this paper's real title (extract_papers.py
    trusts state.sqlite3's title/authors/year as Crossref-verified, per
    CLAUDE.md) -- a real data-quality bug, not cosmetic, so this looks the
    metadata up properly via Crossref's single-work endpoint before the
    paper is ever downloaded. Returns {"title", "authors", "year"} (title
    falls back to the DOI only if Crossref genuinely has nothing, e.g. a
    non-Crossref-registered DOI)."""
    url = CROSSREF_WORK_API.format(doi=doi)
    try:
        resp = rp.http_get(session, url, {"mailto": args.email}, rate_limiter=rate_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger_)
    except rp.RetrievalError:
        return {"title": doi, "authors": [], "year": None}
    if resp.status_code != 200:
        return {"title": doi, "authors": [], "year": None}
    msg = resp.json().get("message", {})
    title = " ".join(msg.get("title") or []).strip() or doi
    authors = []
    for a in msg.get("author") or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p)
        if name:
            authors.append(name)
    year = None
    for date_field in ("published-print", "published-online", "created", "issued"):
        parts = (msg.get(date_field) or {}).get("date-parts")
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    return {"title": title, "authors": authors, "year": year}


def notice_text(pdf_path: Path) -> str:
    try:
        result = subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, timeout=30, text=True)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("pdftotext failed on %s: %s", pdf_path, exc)
        return ""
    return result.stdout or ""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch retraction notices and extract the source DOI each one names.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--cases-path", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--csv-path", type=Path, default=brw.DEFAULT_CSV_PATH)
    parser.add_argument("--reason", default=brw.DEFAULT_REASON)
    parser.add_argument("--refresh", action="store_true", help="Re-derive the case list from the CSV even if --cases-path exists")
    parser.add_argument("--notices-dir", type=Path, default=DEFAULT_NOTICES_DIR)
    parser.add_argument("--found-out", type=Path, default=DEFAULT_FOUND_OUT)
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--max-cases", type=int, default=None, help="Stop after processing this many cases (notices)")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true", help="Report case counts; fetch/download nothing")
    parser.add_argument("--min-interval-unpaywall", type=float, default=1.0)
    parser.add_argument("--min-interval-openalex", type=float, default=0.3)
    parser.add_argument("--no-openalex", action="store_true")
    parser.add_argument("--min-interval-download", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("find_plagiarism_sources.log"))
    return parser.parse_args()


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    args.notices_dir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    cases = load_cases(args)
    if args.max_cases:
        cases = cases[:args.max_cases]
    logger.info("%d case(s) to process", len(cases))
    if args.dry_run:
        logger.info("--dry-run: stopping before any network activity")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    unpaywall_limiter = rp.RateLimiter(args.min_interval_unpaywall)
    openalex_limiter = rp.RateLimiter(args.min_interval_openalex)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    found = {}
    all_candidate_dois = set()
    notices_fetched = notices_with_source = 0

    for i, case in enumerate(cases):
        if i % 50 == 0:
            logger.info("processed %d/%d case(s), %d notice(s) fetched, %d yielded a candidate source",
                        i, len(cases), notices_fetched, notices_with_source)
        retraction_doi = rp.normalize_doi(case.get("retraction_doi", ""))
        if not retraction_doi:
            continue
        exclude = {case["doi"].lower(), retraction_doi.lower()}
        try:
            notice_path = fetch_notice_pdf(session, case, args.notices_dir, unpaywall_limiter,
                                            openalex_limiter, download_limiter, args)
        except rp.RateLimited:
            logger.warning("OpenAlex rate-limited us fetching notices -- continuing with --no-openalex "
                            "behavior (Unpaywall fallback) for the rest of this run")
            args.no_openalex = True
            continue
        if not notice_path:
            continue
        notices_fetched += 1
        text = notice_text(notice_path)
        candidates = extract_candidate_source_dois(text, exclude)
        if candidates:
            notices_with_source += 1
            all_candidate_dois.update(candidates)
        found[case["doi"]] = {
            "retraction_doi": retraction_doi,
            "notice_path": str(notice_path),
            "candidate_source_dois": candidates,
        }

    logger.info("notices fetched: %d/%d, yielded a candidate source: %d, unique candidate source DOIs: %d",
                notices_fetched, len(cases), notices_with_source, len(all_candidate_dois))

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)
    new_dois = [d for d in all_candidate_dois if d not in known_dois]
    store.conn.close()
    logger.info("%d of those source DOI(s) not already in %s", len(new_dois), args.db)

    downloaded = 0
    if new_dois:
        crossref_limiter = rp.RateLimiter(1.0)
        logger.info("looking up real title/authors/year for %d discovered source DOI(s) via Crossref...", len(new_dois))
        source_papers = []
        for d in new_dois:
            meta = lookup_crossref_metadata(session, d, crossref_limiter, args, logger)
            source_papers.append({"doi": d, **meta})

        source_openalex_hits = {}
        if not args.no_openalex:
            for batch in rp.chunked(new_dois, rp.OPENALEX_BATCH_SIZE):
                try:
                    source_openalex_hits.update(rp.query_openalex_batch(session, batch, openalex_limiter, args, logger))
                except rp.RateLimited:
                    logger.warning("OpenAlex rate-limited us on source downloads -- falling back to Unpaywall for the rest")
                    break

        thread_store = rp.ThreadLocalPaperStore(args.db)
        tasks = [(session, p, thread_store, unpaywall_limiter, download_limiter, args, source_openalex_hits)
                 for p in source_papers]
        for _, result in rp.concurrent_fetch(tasks, lambda t: brc.fetch_candidate(*t), max_workers=args.max_workers,
                                              logger=logger):
            if result == "downloaded":
                downloaded += 1

    args.found_out.write_text(json.dumps(found, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %s", args.found_out)
    logger.info("done. %d source paper(s) newly downloaded", downloaded)


if __name__ == "__main__":
    main()
