#!/usr/bin/env python3
"""Bulk-retrieve preprints from SocArXiv -- a genuine preprint server for
the social sciences (anthropology included), hosted on the Center for Open
Science's OSF (Open Science Framework) platform -- into the same
state.sqlite3 manifest retrieve_papers.py/bulk_retrieve_crossref.py use.

Why this exists, distinct from CORE and Zenodo: those are both repository
DEPOSITS (someone uploaded a finished document somewhere). SocArXiv is a
real preprint server in the arXiv sense -- researchers post their own work
there specifically as a preprint, often before or alongside journal
submission, the same category bulk_retrieve_arxiv.py already mines for
computer science but that project had no equivalent source for
anthropology/social-science preprints until now. Confirmed live it holds
real, freely-downloadable anthropology preprints Crossref/DataCite/CORE/
Zenodo don't surface the same way.

API: OSF's v2 REST API (JSON:API format), not a SocArXiv-specific one --
SocArXiv is one of several dozen "preprint providers" hosted on shared OSF
infrastructure, selected via `filter[provider]=socarxiv` on the general
`/v2/preprints/` endpoint.

Two real hops per candidate, not one, unlike CORE/Zenodo's single-request
"search result already has a ready download link" shape:
  1. Search (`q=<keyword>&filter[provider]=socarxiv&embed=contributors`,
     page-paginated via the response's own `links.next`) returns metadata
     and author names (via the `embed=contributors` param -- OSF's
     JSON:API sideloading, avoiding a separate request per author) but
     only a `relationships.primary_file` REFERENCE, not a download link.
  2. fetch_candidate() resolves that reference (one more GET, same
     api.osf.io host so it shares the same rate limiter as harvesting) to
     get the file's actual `links.download` URL, then downloads it exactly
     like every other source here via retrieve_papers.download_pdf().

Deliberately does NOT try to embed the primary_file relationship inline
alongside contributors (`embed=primary_file`) to avoid this second hop --
confirmed live during development that combining both embeds in one
request reliably returns HTTP 502 from OSF's API (a real server-side bug,
not a client mistake -- `embed=contributors` alone works fine, `embed=
primary_file` alone consistently 502s). Two hops it is.

No manual "is this a PDF" check on the resolved file: SocArXiv accepts
Word-doc uploads as a preprint's primary file, not just PDF (confirmed
live: a real search result's primary file was a .docx) -- rather than
inspect the filename extension, this just hands the resolved download URL
straight to download_pdf(), whose own magic-byte sniffing already rejects
anything that isn't a real PDF (same "oa_url_not_pdf" outcome any other
source's landing-page-not-actually-a-PDF case gets). One less thing to get
wrong, and it's already exactly the right behavior.

Rate limit: unlike Zenodo's confirmed 30-requests-per-window (real
x-ratelimit-* headers), OSF's API exposes no rate-limit headers at all in
testing, and its docs page is a JS-rendered SPA that doesn't reveal a
number to curl either. --min-interval-osf's default (1.0s) is therefore a
conservative-by-convention choice -- roughly Crossref/Unpaywall's standard
"polite pool" pace elsewhere in this project -- not a measured limit, and
retrieve_papers.http_get()'s existing generic 429/Retry-After backoff is
the real safety net regardless of what that default turns out to be. No
API key/env var: every request here is against public, published preprint
metadata, and testing found no access-restricted behavior to authenticate
past.
"""

import argparse
import json
import logging
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import retrieve_papers as rp

OSF_PREPRINTS_API = "https://api.osf.io/v2/preprints/"
PAGE_SIZE = 100  # confirmed live: works fine unauthenticated, no documented ceiling found below this

logger = logging.getLogger("bulk_retrieve_socarxiv")


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


def parse_socarxiv_item(item):
    """Returns None for a result missing a title or with no primary_file
    relationship at all (an orphaned/withdrawn preprint record -- nothing
    ever fetchable). Does NOT check whether that file is actually a PDF --
    that's resolved (and, if it isn't, rejected) at fetch time; see module
    docstring."""
    attrs = item.get("attributes") or {}
    title = (attrs.get("title") or "").strip()
    if not title:
        return None

    primary_file_rel = (item.get("relationships") or {}).get("primary_file") or {}
    file_info_url = ((primary_file_rel.get("links") or {}).get("related") or {}).get("href")
    if not file_info_url:
        return None

    authors = []
    embeds = item.get("embeds") or {}
    for c in (embeds.get("contributors") or {}).get("data") or []:
        user = ((c.get("embeds") or {}).get("users") or {}).get("data") or {}
        name = (user.get("attributes") or {}).get("full_name")
        if name:
            authors.append(name)

    date_published = attrs.get("date_published") or ""
    year = int(date_published[:4]) if date_published[:4].isdigit() else None

    doi = None
    preprint_doi_url = (item.get("links") or {}).get("preprint_doi")
    if preprint_doi_url and "doi.org/" in preprint_doi_url:
        doi = preprint_doi_url.split("doi.org/", 1)[1]

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "file_info_url": file_info_url,
        "preprint_id": item.get("id"),
    }


def search_socarxiv(session, rate_limiter, keyword, max_results, args):
    """Yields parsed candidate dicts for one keyword, following the
    response's own `links.next` (standard JSON:API pagination) rather than
    computing page numbers by hand -- the next-page URL already carries
    every query param (filter/q/embed) forward, confirmed live."""
    url = OSF_PREPRINTS_API
    params = {
        "filter[provider]": "socarxiv",
        "q": keyword,
        "embed": "contributors",
        "page[size]": min(PAGE_SIZE, max_results),
    }
    seen = 0
    while url and seen < max_results:
        resp = rp.http_get(session, url, params, rate_limiter=rate_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger)
        if resp.status_code != 200:
            raise rp.RetrievalError(f"socarxiv search failed: HTTP {resp.status_code}")
        body = resp.json()
        items = body.get("data", [])
        if not items:
            break
        for item in items:
            paper = parse_socarxiv_item(item)
            if paper:
                yield paper
        seen += len(items)
        url = (body.get("links") or {}).get("next")
        params = None  # the next URL already has every query param baked in


def fetch_candidate(session, paper, store, osf_limiter, download_limiter, args):
    """Two real network steps, unlike every other source's fetch_candidate()
    here: resolve paper['file_info_url'] to an actual download link first
    (same api.osf.io host as harvesting, same osf_limiter), then download
    it exactly like any other source. See module docstring for why no
    manual PDF-extension check happens here -- download_pdf()'s own
    magic-byte sniffing already handles a non-PDF primary file correctly."""
    key = rp.make_key(paper)
    title = paper["title"]
    authors_json = json.dumps(paper["authors"])
    doi = rp.normalize_doi(paper["doi"]) if paper.get("doi") else None

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

    try:
        resp = rp.http_get(session, paper["file_info_url"], None, rate_limiter=osf_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger)
        if resp.status_code != 200:
            raise rp.RetrievalError(f"file info lookup failed: HTTP {resp.status_code}")
        download_url = ((resp.json().get("data") or {}).get("links") or {}).get("download")
        if not download_url:
            raise rp.RetrievalError("no download link in file info response")
    except rp.RetrievalError as exc:
        logger.error("file info lookup error for %r: %s", title, exc)
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="error",
                     error=str(exc))
        return "error"

    filename = f"{rp.slugify(title)}-osf{paper['preprint_id']}.pdf"
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
    parser = argparse.ArgumentParser(description="Bulk-retrieve preprints from SocArXiv (via OSF's API).")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent")
    parser.add_argument("--keyword", dest="keywords", action="append",
                         help="SocArXiv/OSF search query term (repeatable). Default: bulk_retrieve_crossref.py's "
                              "own computer-ethics/adjacent-fields keyword list, reused rather than duplicated.")
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--max-per-keyword", type=int, default=2000,
                         help="Stop harvesting candidates for a keyword after this many results (default 2000)")
    parser.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    parser.add_argument("--max-workers", type=int, default=6,
                         help="Concurrent fetch threads (default 6 -- both the file-info lookup and the "
                              "download itself go through per-host RateLimiters shared across all threads, "
                              "so this mainly bounds how many candidates are in flight at once, not raw "
                              "throughput past those limiters)")
    parser.add_argument("--dry-run", action="store_true", help="Harvest and report counts; download nothing")
    parser.add_argument("--min-interval-osf", type=float, default=1.0,
                         help="Seconds between api.osf.io requests (search AND file-info lookups share this "
                              "limiter, same host -- default 1.0, conservative-by-convention; see module "
                              "docstring for why this isn't a measured number the way Zenodo's is)")
    parser.add_argument("--min-interval-download", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_socarxiv.log"))
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
    osf_limiter = rp.RateLimiter(args.min_interval_osf)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)

    seen_dois, candidates = set(known_dois), []
    for keyword in args.keywords:
        logger.info("harvesting keyword=%r, up to %d result(s)", keyword, args.max_per_keyword)
        found = new = 0
        try:
            for paper in search_socarxiv(session, osf_limiter, keyword, args.max_per_keyword, args):
                found += 1
                doi = (paper.get("doi") or "").lower()
                if doi and doi in seen_dois:
                    continue
                if doi:
                    seen_dois.add(doi)
                candidates.append(paper)
                new += 1
        except rp.RetrievalError as exc:
            logger.warning("stopped harvesting after keyword=%r: %s -- keeping the %d candidate(s) "
                            "already found this run", keyword, exc, len(candidates))
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
    tasks = [(session, paper, thread_store, osf_limiter, download_limiter, args) for paper in candidates]
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
