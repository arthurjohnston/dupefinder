#!/usr/bin/env python3
"""Bulk-retrieve PhD theses/dissertations via DataCite's public works index,
into the same state.sqlite3 manifest retrieve_papers.py/bulk_retrieve_crossref.py use.

Why this exists: the 2026-08-21 full-corpus plagiarism audit (see todo.md's
"Full-corpus plagiarism audit" section) found that of 70,041 organically-
retrieved papers, only **2 had no DOI** -- i.e. essentially zero theses/
dissertations made it into the corpus, because Crossref (which
retrieve_papers.py/bulk_retrieve_crossref.py both search) mostly doesn't
register them; university repositories typically register dissertation DOIs
through DataCite instead. That's not a diversity nicety, it's the actual gap:
the one confirmed real plagiarism case in this whole project (Saxby/Taro,
tests/cases/saxby-taro-2023.json) is exactly a dissertation that a journal
paper copied from -- an obscure, rarely-cross-referenced document sitting in
an institutional repository, the textbook profile for where copying survives
undetected. A 100k-paper corpus of arXiv preprints and journal articles
structurally can't contain more of that pattern; this script targets the
actual missing category instead of just growing the same kind of corpus further.

Pipeline, per --keyword:
  1. Search DataCite's /dois endpoint (query=<keyword>,
     resource-type-id=dissertation) with cursor-based pagination (DataCite
     hands back a ready-to-use `links.next` URL each page -- simpler than
     Crossref's cursor scheme, which requires reconstructing the params).
  2. For each candidate DOI not already status='downloaded' in state.sqlite3
     (any key), reuse retrieve_papers.py's query_unpaywall()/download_pdf()
     exactly as bulk_retrieve_crossref.py's fetch_candidate() does -- same OA
     resolution logic (Unpaywall covers any DOI, not just Crossref-registered
     ones), same PDF-sniffing download, same state.sqlite3 schema, so
     extract_papers.py needs zero changes to pick these up. Most DataCite
     dissertation records hand back a direct repository landing-page URL as
     `attributes.url`, which is also passed through as a fallback OA
     candidate if Unpaywall itself comes up empty -- download_pdf()'s
     citation_pdf_url landing-page fallback (see retrieve_papers.py) is what
     turns a repository landing page into an actual PDF fetch.

Deliberately does NOT resolve_doi() (retrieve_papers.py's title/author
fuzzy-match against Crossref) -- DataCite search already hands back a DOI
directly for each candidate, same reasoning bulk_retrieve_crossref.py gives
for skipping it.

Resumable/dedup-aware the same way bulk_retrieve_crossref.py is: a DOI
already status='downloaded' under any key is skipped, never re-fetched.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import requests

import bulk_retrieve_crossref as brc
import retrieve_papers as rp

DATACITE_DOIS_API = "https://api.datacite.org/dois"

logger = logging.getLogger("bulk_retrieve_theses")


def parse_datacite_item(item):
    doi = item.get("id") or (item.get("attributes") or {}).get("doi")
    if not doi:
        return None
    attrs = item.get("attributes") or {}
    titles = attrs.get("titles") or []
    title = (titles[0].get("title") if titles else "").strip()
    if not title:
        return None
    authors = [c.get("name") for c in (attrs.get("creators") or []) if c.get("name")]
    year = attrs.get("publicationYear")
    landing_url = attrs.get("url")
    return {"doi": doi, "title": title, "authors": authors, "year": year, "landing_url": landing_url}


def search_datacite(session, rate_limiter, keyword, max_results, args):
    """Yields parsed candidate dicts for one keyword, cursor-paginated via
    DataCite's own `links.next` (a full ready-to-use URL each page -- no
    manual param reconstruction needed, unlike Crossref's cursor scheme)."""
    url = DATACITE_DOIS_API
    params = {
        "query": keyword,
        "resource-type-id": "dissertation",
        "page[cursor]": "1",
        "page[size]": min(200, max_results),
    }
    seen = 0
    while url and seen < max_results:
        resp = rp.http_get(session, url, params, rate_limiter=rate_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger)
        if resp.status_code != 200:
            raise rp.RetrievalError(f"datacite search failed: HTTP {resp.status_code}")
        body = resp.json()
        items = body.get("data", [])
        if not items:
            break
        for item in items:
            paper = parse_datacite_item(item)
            if paper:
                yield paper
            seen += 1
            if seen >= max_results:
                break
        url = (body.get("links") or {}).get("next")
        params = None  # the next-link already carries every query param


def fetch_candidate(session, paper, store, unpaywall_limiter, download_limiter, args, openalex_hits=None):
    """Same shape as bulk_retrieve_crossref.py's fetch_candidate() (including
    the openalex_hits pre-resolution -- see its docstring), plus one extra
    fallback specific to theses: if NEITHER OpenAlex NOR Unpaywall has an OA
    location on file for this DOI (common for institutional-repository
    theses -- both have thinner coverage of DataCite-registered DOIs than
    Crossref-registered ones), fall back to DataCite's own `attributes.url`
    (the repository landing page) before giving up -- download_pdf()'s
    landing-page/citation_pdf_url fallback (retrieve_papers.py) is what turns
    that into a real PDF.

    Live pre-check + atomic claim (added after a real cross-process corruption
    incident): main()'s `known_dois` is a one-time snapshot taken at process
    startup, not re-checked per candidate -- fine as a cheap first filter, but
    on a long run (this harvest routinely takes 40+ minutes across 25
    keywords) a DIFFERENT concurrently-running retrieval process can legitimately
    download the same DOI in the meantime. Without a live check here, this
    function would proceed to re-fetch it anyway and its own final upsert()
    would silently clobber the other process's already-good 'downloaded' row
    with this call's (often worse, since it's now the second/redundant
    attempt) outcome -- confirmed for real: found 315 rows across this
    project's corpora with a real file_path on disk but a non-'downloaded'
    status, all from concurrent runs where this script was one of the
    processes involved. bulk_retrieve_core.py's fetch_candidate() already has
    this exact pattern (pre-check + store.claim()) for the same reason; this
    mirrors it. See PaperStore.claim()'s own docstring and todo.md's
    "Cross-process retrieval dedup race" entry -- that entry's fix (commit
    8b66d67) wired claim() into core/zenodo/socarxiv and explicitly flagged
    this script as remaining work, never done until now."""
    doi = rp.normalize_doi(paper["doi"])
    key = "doi:" + doi.lower()
    title = paper["title"]
    authors_json = json.dumps(paper["authors"])

    record = store.get(key)
    if record:
        status = record.get("status")
        if status and status.startswith("excluded_"):
            return "skipped"
        if status == "downloaded" and record.get("file_path") and Path(record["file_path"]).exists():
            return "skipped"

    if not store.claim(key):
        return "skipped"

    openalex_hit = (openalex_hits or {}).get(doi.lower())
    pdf_url, oa_status, data = None, None, True  # data=True: "known", only None means "Unpaywall never heard of it"
    if openalex_hit:
        oa_status, pdf_url = openalex_hit["oa_status"], openalex_hit["pdf_url"]
    else:
        try:
            data = rp.query_unpaywall(session, doi, unpaywall_limiter, args, logger)
        except rp.RetrievalError as exc:
            logger.error("unpaywall error for %r (%s): %s", title, doi, exc)
            store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi,
                         status="error", error=str(exc))
            return "error"

        if data is not None:
            oa_status = data.get("oa_status")
            best_loc = data.get("best_oa_location") or {}
            pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")

    if not pdf_url and paper.get("landing_url"):
        # Unpaywall came up empty (or never heard of this DOI) -- try the
        # repository landing page DataCite itself handed back.
        pdf_url = paper["landing_url"]
        oa_status = oa_status or "repository_landing_page"

    if not pdf_url:
        status = "doi_unknown_to_unpaywall" if data is None else "no_oa"
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi,
                     status=status, oa_status=oa_status)
        return status

    filename = f"{rp.slugify(title)}-{rp.slugify(doi)}.pdf"
    dest_path = args.outdir / filename

    try:
        # download_with_landing_page_fallback(), not the plain download_pdf()
        # bulk_retrieve_crossref.py uses -- this is specifically the rescue
        # documented as the single biggest lever for thesis retrieval's
        # much-worse-than-average success rate (retrieve_papers.py's
        # docstring), and pdf_url here is disproportionately likely to BE a
        # landing page (either Unpaywall's own or DataCite's `url` fallback).
        ok, working_url = rp.download_with_landing_page_fallback(
            session, pdf_url, dest_path, download_limiter, args, logger)
    except rp.RetrievalError as exc:
        logger.error("download error for %r: %s", title, exc)
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="error",
                     oa_status=oa_status, pdf_url=pdf_url, error=str(exc))
        return "error"

    if ok:
        logger.info("downloaded %r -> %s", title, dest_path.name)
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="downloaded",
                     oa_status=oa_status, pdf_url=working_url, file_path=str(dest_path))
        return "downloaded"

    store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="oa_url_not_pdf",
                 oa_status=oa_status, pdf_url=pdf_url)
    return "oa_url_not_pdf"


def parse_args():
    parser = argparse.ArgumentParser(description="Bulk-retrieve theses/dissertations via DataCite.")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent + mailto=")
    parser.add_argument("--keyword", dest="keywords", action="append",
                         help="DataCite query term (repeatable). Default: bulk_retrieve_crossref.py's "
                              "own computer-ethics/adjacent-fields keyword list, reused rather than "
                              "duplicated -- theses on the same topics are exactly what this project needs.")
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--max-per-keyword", type=int, default=500,
                         help="Stop harvesting candidates for a keyword after this many DataCite results "
                              "(default 500 -- dissertations are a much smaller slice of DataCite's index "
                              "than Crossref's whole works index is, so this can stay modest)")
    parser.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    parser.add_argument("--max-workers", type=int, default=8,
                         help="Concurrent fetch threads for the download phase (default 8), same reasoning "
                              "as bulk_retrieve_crossref.py")
    parser.add_argument("--dry-run", action="store_true", help="Harvest and report counts; query Unpaywall/download nothing")
    parser.add_argument("--min-interval-datacite", type=float, default=1.0, help="Seconds between DataCite requests")
    parser.add_argument("--min-interval-unpaywall", type=float, default=1.0, help="Seconds between Unpaywall requests")
    parser.add_argument("--min-interval-openalex", type=float, default=0.3,
                         help="Seconds between OpenAlex batch-lookup requests (default 0.3) -- see "
                              "bulk_retrieve_crossref.py's --min-interval-openalex for why this exists")
    parser.add_argument("--no-openalex", action="store_true",
                         help="Skip the OpenAlex batch pre-resolution pass -- see "
                              "bulk_retrieve_crossref.py's --no-openalex")
    parser.add_argument("--min-interval-download", type=float, default=0.5, help="Seconds between PDF downloads")
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_theses.log"))
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
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = logging.FileHandler(args.log_file)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    datacite_limiter = rp.RateLimiter(args.min_interval_datacite)
    unpaywall_limiter = rp.RateLimiter(args.min_interval_unpaywall)
    openalex_limiter = rp.RateLimiter(args.min_interval_openalex)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)

    seen_dois, candidates = set(), []
    for keyword in args.keywords:
        logger.info("harvesting keyword=%r, up to %d dissertation result(s)", keyword, args.max_per_keyword)
        found = new = 0
        for paper in search_datacite(session, datacite_limiter, keyword, args.max_per_keyword, args):
            found += 1
            doi_norm = rp.normalize_doi(paper["doi"])
            if not doi_norm or doi_norm.lower() in seen_dois:
                continue
            seen_dois.add(doi_norm.lower())
            if doi_norm.lower() in known_dois:
                continue
            candidates.append(paper)
            new += 1
        logger.info("  %r: %d found, %d new (not already in %s)", keyword, found, new, args.db)

    logger.info("total: %d new candidate dissertation(s) across all keywords", len(candidates))
    if args.dry_run:
        logger.info("--dry-run: stopping before any Unpaywall/download activity")
        store.conn.close()
        return
    store.conn.close()

    openalex_hits = {}
    if not args.no_openalex:
        dois = [rp.normalize_doi(p["doi"]) for p in candidates if p.get("doi")]
        n_batches = (len(dois) + rp.OPENALEX_BATCH_SIZE - 1) // rp.OPENALEX_BATCH_SIZE
        has_key = bool(os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR))
        logger.info("pre-resolving OA locations for %d candidate(s) via OpenAlex (%d batch(es) of up to %d, "
                    "%s)...", len(dois), n_batches, rp.OPENALEX_BATCH_SIZE,
                    "using OPENALEX_API_KEY" if has_key else "anonymous, no OPENALEX_API_KEY set")
        for batch in rp.chunked(dois, rp.OPENALEX_BATCH_SIZE):
            try:
                openalex_hits.update(rp.query_openalex_batch(session, batch, openalex_limiter, args, logger))
            except rp.RateLimited:
                # See bulk_retrieve_crossref.py's identical handling -- confirmed to happen
                # for real on this project's own corpus, not a defensive-only branch.
                remaining = len(dois) - len(openalex_hits)
                logger.warning("OpenAlex rate-limited us -- stopping OpenAlex lookups for the rest of this "
                                "run, %d remaining candidate(s) will use per-DOI Unpaywall instead", remaining)
                break
        logger.info("OpenAlex resolved %d/%d candidate(s) directly -- %d will fall back to per-DOI Unpaywall",
                    len(openalex_hits), len(dois), len(dois) - len(openalex_hits))

    thread_store = rp.ThreadLocalPaperStore(args.db)
    tasks = [(session, paper, thread_store, unpaywall_limiter, download_limiter, args, openalex_hits)
             for paper in candidates]
    downloaded = 0
    for _, result in rp.concurrent_fetch(tasks, lambda t: fetch_candidate(*t), max_workers=args.max_workers,
                                          max_successes=args.max_papers, logger=logger):
        if result == "downloaded":
            downloaded += 1
            if downloaded % 25 == 0:
                logger.info("downloaded %d so far", downloaded)

    logger.info("done. downloaded %d new dissertation(s)", downloaded)


if __name__ == "__main__":
    main()
