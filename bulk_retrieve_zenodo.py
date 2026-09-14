#!/usr/bin/env python3
"""Bulk-retrieve papers/theses/reports from Zenodo (zenodo.org)'s open
repository, into the same state.sqlite3 manifest retrieve_papers.py/
bulk_retrieve_crossref.py use.

Why this exists, distinct from CORE (bulk_retrieve_core.py): both are
DOI-registry-independent repository content, not another angle on
Crossref/DataCite -- but Zenodo is CERN-operated general-purpose deposit
storage (anyone can upload a working paper, conference proceedings, report,
or dataset and get a DOI minted for it via DataCite), not an aggregator of
OTHER repositories the way CORE/BASE are. Confirmed live it holds real
anthropology grey literature Crossref/DataCite/CORE's respective indexes
don't surface the same way -- individual researchers' working papers and
conference materials deposited directly, not through an institutional
repository or a DOI-registering publisher. Genuinely different coverage,
not redundant with CORE.

Pipeline, per --keyword:
  1. Search Zenodo's REST API (GET /api/records, q=<keyword>,
     type=<resource-type>), page-paginated.
  2. Unlike CORE, Zenodo's search response carries the DOI it always mints
     for every deposit (reliable, not "frequently None" the way CORE's is)
     -- so this dedups the same way bulk_retrieve_crossref.py/
     bulk_retrieve_theses.py do, against a harvest-time known-DOI set
     (load_known_dois()), not CORE's per-candidate state.sqlite3 check.
  3. Each record's `files[]` list is searched for a `.pdf`-suffixed entry;
     its `links.self` is a direct, ready-to-download URL -- no DOI
     resolution, no Unpaywall/OpenAlex OA-location lookup needed, same
     "already resolved by the source itself" shape as CORE's downloadUrl.
     Records whose access_right isn't 'open', or which carry no PDF file at
     all (Zenodo hosts plenty of datasets/software/closed-access metadata
     records too), are skipped at parse time -- nothing to download.

Rate limit: confirmed live via Zenodo's own `x-ratelimit-*` response
headers -- 30 requests per rolling window (`x-ratelimit-reset` gives the
reset timestamp; observed ~60s windows), with a `retry-after` on 429
(handled by retrieve_papers.http_get()'s existing generic backoff, same as
every other source here). --min-interval-zenodo's default (2.5s) is picked
to stay comfortably under 30/window with margin for retries, not a guess
the way CORE's conservative default had to be -- Zenodo's rate-limit
headers are concrete numbers, confirmed by direct testing during this
script's own development.

Optional ZENODO_API_KEY env var (same convention as OPENALEX_API_KEY/
CORE_API_KEY -- an env var, never a CLI flag, for the `ps aux` reason those
have): sent as the documented `access_token` query param, NOT a Bearer
header (Zenodo's actual auth convention, confirmed against their real API --
different from CORE's Bearer-header scheme). Concretely, not vaguely,
useful here: unauthenticated requests are capped at 25 results per page
(confirmed live -- a size=100 request without a token gets HTTP 400 "Page
size cannot be greater than 25. Please use authenticated requests to
increase the limit to 100."); with a real token, page size goes up to 100,
a 4x reduction in the number of requests needed for the same harvest depth.
ZENODO_PAGE_SIZE is picked automatically based on whether a key is set.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import retrieve_papers as rp

ZENODO_SEARCH_API = "https://zenodo.org/api/records"
ZENODO_API_KEY_ENV_VAR = "ZENODO_API_KEY"  # deliberately an env var, never a CLI flag/args -- see module docstring
ZENODO_PAGE_SIZE_ANONYMOUS = 25  # confirmed live: HTTP 400 above this without a token
ZENODO_PAGE_SIZE_AUTHENTICATED = 100  # confirmed live: the documented ceiling with a real access_token

logger = logging.getLogger("bulk_retrieve_zenodo")


def setup_logger(log_file: Path):
    logger.setLevel(logging.INFO)
    logger.propagate = False  # see tests/run_tests.py's note on this exact footgun
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)


def parse_zenodo_item(item):
    """Returns None for a result missing a title, not open access, or with
    no PDF file attached (Zenodo hosts datasets/software/posters alongside
    real papers, and closed-access metadata-only records too -- none of
    those are fetchable, so there's no reason to carry them through
    harvesting only to fail at download time, same discipline as
    bulk_retrieve_core.py's parse_core_item())."""
    metadata = item.get("metadata") or {}
    title = (metadata.get("title") or "").strip()
    if not title:
        return None
    if metadata.get("access_right") != "open":
        return None

    pdf_url = None
    for f in item.get("files") or []:
        key = (f.get("key") or "").lower()
        if key.endswith(".pdf"):
            pdf_url = (f.get("links") or {}).get("self")
            if pdf_url:
                break
    if not pdf_url:
        return None

    authors = [c.get("name") for c in (metadata.get("creators") or []) if c.get("name")]
    pub_date = metadata.get("publication_date") or ""
    year = int(pub_date[:4]) if pub_date[:4].isdigit() else None

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": item.get("doi"),
        # Zenodo mints a DISTINCT doi/id per VERSION of one deposit -- conceptrecid is the
        # stable id shared by every version (confirmed live: e.g. id=10517905/doi=...10517905
        # and a v2 upload both carry conceptrecid=10517904). Found live 2026-08-30 as a real,
        # already-happened bug: DOI-only dedup can't recognize two different real DOIs as the
        # same underlying work, and Zenodo's own search index returns each version as its own
        # hit -- ~255 duplicate downloads in this project's own anthropology corpus before this
        # fix, all pairs with adjacent DOI-suffix numbers. Falls back to the doi itself if
        # conceptrecid is ever missing (not observed, but cheap to guard).
        "conceptrecid": item.get("conceptrecid") or item.get("doi"),
        "download_url": pdf_url,
        "zenodo_id": item.get("id"),
    }


def search_zenodo(session, rate_limiter, keyword, max_results, page_size, args, resource_type):
    """Yields parsed candidate dicts for one keyword, page-paginated."""
    page = 1
    seen = 0
    while seen < max_results:
        params = {"q": keyword, "size": min(page_size, max_results - seen), "page": page}
        if resource_type:
            params["type"] = resource_type
        resp = rp.http_get(session, ZENODO_SEARCH_API, params, rate_limiter=rate_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger)
        if resp.status_code != 200:
            raise rp.RetrievalError(f"zenodo search failed: HTTP {resp.status_code}")
        body = resp.json()
        items = body.get("hits", {}).get("hits", [])
        if not items:
            break
        for item in items:
            paper = parse_zenodo_item(item)
            if paper:
                yield paper
        seen += len(items)
        page += 1
        if len(items) < page_size:
            break


def fetch_candidate(session, paper, store, download_limiter, args):
    """Same shape as every other bulk_retrieve_*.py fetch_candidate(): the
    OA location is already resolved (paper['download_url']), so this is
    just retrieve_papers.download_pdf() plus the state.sqlite3 bookkeeping.
    Dedup against already-downloaded work happens at harvest time (known
    DOIs, see main()) since Zenodo reliably provides one -- this only
    re-checks the specific key's own record for the same
    already-downloaded-file-exists / already-excluded cases every other
    source's fetch_candidate() checks."""
    key = rp.make_key(paper)
    title = paper["title"]
    authors_json = json.dumps(paper["authors"])
    doi = rp.normalize_doi(paper["doi"]) if paper.get("doi") else None
    download_url = paper["download_url"]

    record = store.get(key)
    if record:
        status = record.get("status")
        if status and status.startswith("excluded_"):
            return "skipped"
        if status == "downloaded" and record.get("file_path") and Path(record["file_path"]).exists():
            return "skipped"

    # Atomic claim before starting real work -- see PaperStore.claim()'s own docstring and
    # todo.md's "Cross-process retrieval dedup race" entry. False means another process
    # already claimed/finished this key since the read-only checks above.
    if not store.claim(key):
        return "skipped"

    filename = f"{rp.slugify(title)}-zenodo{paper['zenodo_id']}.pdf"
    dest_path = args.outdir / filename

    try:
        ok = rp.download_pdf(session, download_url, dest_path, download_limiter, args, logger)
    except rp.RetrievalError as exc:
        logger.error("download error for %r: %s", title, exc)
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="error",
                     pdf_url=download_url, error=str(exc))
        return "error"

    if ok:
        logger.info("downloaded %r -> %s", title, dest_path.name)
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="downloaded",
                     pdf_url=download_url, file_path=str(dest_path))
        return "downloaded"

    store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="oa_url_not_pdf",
                 pdf_url=download_url)
    return "oa_url_not_pdf"


def parse_args():
    parser = argparse.ArgumentParser(description="Bulk-retrieve papers/theses/reports from Zenodo (zenodo.org).")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent")
    parser.add_argument("--keyword", dest="keywords", action="append",
                         help="Zenodo search query term (repeatable). Default: bulk_retrieve_crossref.py's "
                              "own computer-ethics/adjacent-fields keyword list, reused rather than duplicated.")
    parser.add_argument("--resource-type", default="publication",
                         help="Zenodo `type` filter (default 'publication' -- excludes datasets/software/"
                              "posters/presentations, which Zenodo hosts alongside real papers. Empty string "
                              "disables the filter entirely.")
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--max-per-keyword", type=int, default=2000,
                         help="Stop harvesting candidates for a keyword after this many Zenodo results "
                              "(default 2000)")
    parser.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="Harvest and report counts; download nothing")
    parser.add_argument("--min-interval-zenodo", type=float, default=2.5,
                         help="Seconds between Zenodo search requests (default 2.5 -- confirmed live rate "
                              "limit is 30 requests per ~60s window; this stays comfortably under that with "
                              "margin for retries. See module docstring.)")
    parser.add_argument("--min-interval-download", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_zenodo.log"))
    parser.add_argument("--no-key-ok", action="store_true",
                         help="Accepted but otherwise unused here -- exists so the require_api_keys.py "
                              "PreToolUse hook's documented escape hatch (append this flag to run "
                              "anonymously/slower without the usual API key) is valid argv for this "
                              "script instead of an 'unrecognized arguments' error.")
    args = parser.parse_args()
    if not args.keywords:
        args.keywords = brc.DEFAULT_KEYWORDS
    return args


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    api_key = os.environ.get(ZENODO_API_KEY_ENV_VAR)
    if api_key:
        session.params = {"access_token": api_key}  # applied to every request via this session
    page_size = ZENODO_PAGE_SIZE_AUTHENTICATED if api_key else ZENODO_PAGE_SIZE_ANONYMOUS
    logger.info("%s (page_size=%d)", "using ZENODO_API_KEY" if api_key else
                "anonymous, no ZENODO_API_KEY set -- capped at 25 results/page (100 with a key)", page_size)
    zenodo_limiter = rp.RateLimiter(args.min_interval_zenodo)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)

    seen_dois, seen_conceptrecids, candidates = set(known_dois), set(), []
    for keyword in args.keywords:
        logger.info("harvesting keyword=%r, up to %d result(s)", keyword, args.max_per_keyword)
        found = new = 0
        try:
            for paper in search_zenodo(session, zenodo_limiter, keyword, args.max_per_keyword, page_size,
                                        args, args.resource_type):
                found += 1
                # conceptrecid dedup first: catches two different, both-real DOIs that are
                # actually two versions of the same deposit (see parse_zenodo_item()'s own
                # comment) -- a plain DOI check alone can't, since each version's DOI IS unique.
                conceptrecid = paper.get("conceptrecid")
                if conceptrecid:
                    if conceptrecid in seen_conceptrecids:
                        continue
                    seen_conceptrecids.add(conceptrecid)
                doi = (paper.get("doi") or "").lower()
                if doi and doi in seen_dois:
                    continue
                if doi:
                    seen_dois.add(doi)
                candidates.append(paper)
                new += 1
        except rp.RetrievalError as exc:
            # A persistent 429 here (http_get()'s own retry/backoff already exhausted --max-retries)
            # means the rate limit is exceeded for the rest of this run's window -- retrying the next
            # keyword would just hit the same wall immediately. Stop harvesting entirely (candidates
            # already found this run are still processed below), same discipline as every other
            # source's harvest loop in this project.
            logger.warning("stopped harvesting after keyword=%r: %s -- keeping the %d candidate(s) "
                            "already found this run (set %s for a much higher rate limit)",
                            keyword, exc, len(candidates), ZENODO_API_KEY_ENV_VAR)
            break
        logger.info("  %r: %d found, %d new (this run)", keyword, found, new)

    logger.info("total: %d new candidate(s) across all keywords (not yet checked against %s)",
                len(candidates), args.db)
    if args.dry_run:
        logger.info("--dry-run: stopping before any download activity")
        store.conn.close()
        return
    store.conn.close()

    if args.max_papers is not None:
        candidates = candidates[:args.max_papers]

    thread_store = rp.ThreadLocalPaperStore(args.db)
    tasks = [(session, paper, thread_store, download_limiter, args) for paper in candidates]
    downloaded = skipped = 0
    for _, result in rp.concurrent_fetch(tasks, lambda t: fetch_candidate(*t), max_workers=args.max_workers,
                                          max_successes=args.max_papers, logger=logger):
        if result == "downloaded":
            downloaded += 1
            if downloaded % 25 == 0:
                logger.info("downloaded %d so far", downloaded)
        elif result == "skipped":
            skipped += 1

    logger.info("done. downloaded %d new paper(s) (%d already had it)", downloaded, skipped)


if __name__ == "__main__":
    main()
