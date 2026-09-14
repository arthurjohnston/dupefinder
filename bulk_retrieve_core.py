#!/usr/bin/env python3
"""Bulk-retrieve papers/theses from CORE (core.ac.uk)'s aggregated open-access
index, into the same state.sqlite3 manifest retrieve_papers.py/
bulk_retrieve_crossref.py use.

Why this exists: every other bulk-retrieval script in this project is
ultimately a DOI-registry search -- bulk_retrieve_crossref.py searches
Crossref, bulk_retrieve_theses.py searches DataCite. Both find plenty of
theses/dissertations bulk_retrieve_arxiv.py's arXiv-only pull structurally
couldn't (see that script's own docstring for why that mattered -- the one
confirmed real plagiarism case in this project, Saxby/Taro, was exactly a
dissertation), but they share one blind spot: a DOI-registry search can only
ever find things that HAVE a DOI. A repository that never registered a DOI at
all -- through DataCite or anyone else -- for a given thesis is invisible to
every script in this project so far, regardless of how deep the search goes.

CORE aggregates repository content directly (institutional repositories,
preprint servers, journals) rather than searching a DOI registry, so its
index isn't DOI-gated at all: confirmed live, a real search result routinely
carries `doi: null` while still having a genuine CORE-hosted download URL.
This is the actual source for that gap, not just another angle on the same
one bulk_retrieve_theses.py already covers.

Pipeline, per --keyword:
  1. Search CORE's v3 REST API (GET /search/works, q=<keyword>), offset-
     paginated.
  2. Unlike Crossref/DataCite, CORE's own search response already carries a
     resolved `downloadUrl` for open-access content -- no DOI needed, no
     separate Unpaywall/OpenAlex OA-location lookup required. This script
     downloads directly from that URL via retrieve_papers.py's own
     download_pdf() (same PDF-sniffing/atomic-write logic, same state.sqlite3
     schema, so extract_papers.py needs zero changes to pick these up).

Works without any API key -- CORE's public search API allows unauthenticated
access, confirmed live -- but at a real, fairly restrictive rate limit
(observed empirically: roughly single-digit requests before a multi-minute
cooldown, via the API's own `x-ratelimit-*` response headers; CORE doesn't
publish an exact unauthenticated quota, so --min-interval-core's default is a
conservative guess informed by that observation, not a documented number). A
free API key (https://core.ac.uk/services/api, a few minutes to register)
raises the effective limit substantially; set it via the CORE_API_KEY env
var, same convention as retrieve_papers.py's OPENALEX_API_KEY (deliberately
an env var, never a CLI flag -- a flag would sit in plain sight in `ps aux`
for the life of the process). Sent as an `Authorization: Bearer <key>` header
on the session, not a query param, since CORE's docs specify Bearer auth for
the v3 API.

Resumable, but via a different mechanism than bulk_retrieve_crossref.py/
bulk_retrieve_theses.py: those dedup at HARVEST time against a set of known
DOIs (load_known_dois()), which doesn't work here since most candidates this
script is specifically looking for have no DOI to dedup on. Instead,
fetch_candidate() checks state.sqlite3 directly (via retrieve_papers.py's own
make_key(), which already falls back to a title/year/authors slug when doi is
None -- no changes needed there) immediately before downloading, the same
check retrieve_papers.py's own process_paper() uses. Slightly more DB
round-trips than the other scripts' harvest-time set-membership check, cheap
at this scale and correct for both DOI and no-DOI candidates uniformly.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import retrieve_papers as rp

CORE_SEARCH_API = "https://api.core.ac.uk/v3/search/works/"
CORE_API_KEY_ENV_VAR = "CORE_API_KEY"  # deliberately an env var, never a CLI flag/args -- see module docstring

logger = logging.getLogger("bulk_retrieve_core")


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


def parse_core_item(item):
    """Returns None for a result missing a title (nothing usable to key on)
    or with no downloadUrl at all (CORE indexes plenty of closed-access
    metadata-only records; those can never be fetched, so there's no reason
    to carry them through harvesting only to fail at download time)."""
    title = (item.get("title") or "").strip()
    if not title:
        return None
    download_url = item.get("downloadUrl")
    if not download_url:
        return None
    authors = [a.get("name") for a in (item.get("authors") or []) if a.get("name")]
    return {
        "title": title,
        "authors": authors,
        "year": item.get("yearPublished"),
        "doi": item.get("doi"),  # frequently None -- the whole point of this source, see module docstring
        "download_url": download_url,
        "core_id": item.get("id"),
    }


def search_core(session, rate_limiter, keyword, max_results, args):
    """Yields parsed candidate dicts for one keyword, offset-paginated."""
    page_size = 100
    offset = 0
    while offset < max_results:
        params = {"q": keyword, "limit": min(page_size, max_results - offset), "offset": offset}
        resp = rp.http_get(session, CORE_SEARCH_API, params, rate_limiter=rate_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger)
        if resp.status_code != 200:
            raise rp.RetrievalError(f"core search failed: HTTP {resp.status_code}")
        body = resp.json()
        items = body.get("results", [])
        if not items:
            break
        for item in items:
            paper = parse_core_item(item)
            if paper:
                yield paper
        offset += len(items)
        if len(items) < page_size:
            break


def fetch_candidate(session, paper, store, download_limiter, args):
    """See module docstring for why this checks state.sqlite3 directly
    rather than relying on a harvest-time known-DOI filter.

    A record already marked 'excluded_crank'/'excluded_offtopic' by
    filter_low_relevance_papers.py is skipped unconditionally, file-existence
    check or not -- that status means a human-in-spirit decision already
    excluded this exact paper from the pipeline, so re-fetching it just
    because its (relocated) file happens to be missing would silently undo
    that decision, unlike the ordinary "status='downloaded' but the file's
    gone, redownload it" case just below, which is a real recovery, not a
    reversal."""
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

    filename = f"{rp.slugify(title)}-core{paper['core_id']}.pdf"
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
    parser = argparse.ArgumentParser(description="Bulk-retrieve papers/theses from CORE (core.ac.uk), "
                                                   "including content with no DOI at all.")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent")
    parser.add_argument("--keyword", dest="keywords", action="append",
                         help="CORE search query term (repeatable). Default: bulk_retrieve_crossref.py's "
                              "own computer-ethics/adjacent-fields keyword list, reused rather than "
                              "duplicated.")
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--max-per-keyword", type=int, default=500,
                         help="Stop harvesting candidates for a keyword after this many CORE results "
                              "(default 500)")
    parser.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    parser.add_argument("--max-workers", type=int, default=4,
                         help="Concurrent fetch threads for the download phase (default 4 -- lower than "
                              "bulk_retrieve_crossref.py's 8: CORE-hosted downloads all go to the same "
                              "core.ac.uk host, so more threads would just queue behind the same "
                              "per-host RateLimiter rather than adding real parallelism)")
    parser.add_argument("--dry-run", action="store_true", help="Harvest and report counts; download nothing")
    parser.add_argument("--min-interval-core", type=float, default=8.0,
                         help="Seconds between CORE search requests (default 8.0 -- conservative given "
                              "CORE's unauthenticated rate limit is real but not precisely documented; "
                              "see module docstring. Safe to lower substantially with CORE_API_KEY set.)")
    parser.add_argument("--min-interval-download", type=float, default=1.0, help="Seconds between PDF downloads")
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_core.log"))
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
    headers = {"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)}
    api_key = os.environ.get(CORE_API_KEY_ENV_VAR)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    session.headers.update(headers)
    core_limiter = rp.RateLimiter(args.min_interval_core)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)

    seen_core_ids, candidates = set(), []
    for keyword in args.keywords:
        logger.info("harvesting keyword=%r, up to %d result(s) (%s)",
                    keyword, args.max_per_keyword, "using CORE_API_KEY" if api_key else "anonymous, no CORE_API_KEY set")
        found = new = 0
        try:
            for paper in search_core(session, core_limiter, keyword, args.max_per_keyword, args):
                found += 1
                core_id = paper.get("core_id")
                if core_id is not None:
                    if core_id in seen_core_ids:
                        continue
                    seen_core_ids.add(core_id)
                candidates.append(paper)
                new += 1
        except rp.RetrievalError as exc:
            # A persistent 429 here (http_get()'s own retry/backoff already exhausted
            # --max-retries) means CORE's rate limit is exceeded for the rest of this
            # run's window, not a transient blip -- retrying the next keyword would just
            # hit the same wall immediately. Stop harvesting entirely (candidates already
            # found this run are still processed below) rather than crash with a
            # traceback; without CORE_API_KEY this is expected to happen on any run that
            # tries more than a handful of keywords, see module docstring.
            logger.warning("stopped harvesting after keyword=%r: %s -- keeping the %d candidate(s) "
                            "already found this run (set %s for a much higher rate limit)",
                            keyword, exc, len(candidates), CORE_API_KEY_ENV_VAR)
            break
        logger.info("  %r: %d found, %d new (this run)", keyword, found, new)

    logger.info("total: %d candidate paper(s) across all keywords (not yet checked against %s)",
                len(candidates), args.db)
    if args.dry_run:
        logger.info("--dry-run: stopping before any download activity")
        store.conn.close()
        return
    store.conn.close()

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
