"""Second try at the PDFs a full-text run (gi_verify.sh) couldn't get, written back into that run's
state.sqlite3 so the rest of the run (paths -> extract -> load-text -> measure -> move) picks them up.

retrieve_papers.py tries one URL per paper: Unpaywall's best_oa_location (with the citation_pdf_url
landing-page fallback). This tries the rest:

  titles  papers with no DOI (status doi_not_found: Crossref's title search found no match) are looked
          up with OpenAlex's title search (filter title.search, one call each, on the OpenAlex key);
          a match needs title similarity >= --title-threshold (retrieve_papers.py's own 0.82),
          4+ title words, and the year within 1 when both are known. The DOI goes into the state row.
  copies  for those and for papers with a free copy that failed (error, oa_url_not_pdf,
          doi_unknown_to_unpaywall): every location Unpaywall lists (oa_locations, not just the best)
          and every OpenAlex location (batched, 50 DOIs a call), direct PDF links first, then landing
          pages, the URL that already failed last-tried or skipped. Up to --max-tries per paper, each
          through download_with_landing_page_fallback().

A paper still marked no_oa is not retried (in a 50-paper sample OpenAlex had a PDF link for 2).
PDFs go to the run's workdir papers/ (internal disk); gi_rescue.sh then runs the rest of the run.

Usage: gi_rescue_pdfs.py RUN [--email ADDR] [--workers 8] [--max-tries 6] [--no-titles]
"""
import argparse
import html
import json
import logging
import os
import re
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402

import gi_db  # noqa: E402

RETRY = ("error", "oa_url_not_pdf", "doi_unknown_to_unpaywall")


def title_words(t):
    return re.findall(r"[a-z0-9]+", html.unescape(t or "").lower())


def find_doi_by_title(session, limiter, args, log, title, year):
    words = title_words(title)
    if len(words) < 4:
        return None, None
    params = {"filter": "title.search:" + " ".join(words), "per-page": 5,
              "select": "doi,title,publication_year,locations", "mailto": args.email}
    key = os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR)
    if key:
        params["api_key"] = key
    resp = rp.http_get(session, rp.OPENALEX_API, params, rate_limiter=limiter, max_retries=args.max_retries,
                       timeout=args.timeout, logger=log)
    if resp.status_code != 200:
        log.warning("openalex title search HTTP %d for %r", resp.status_code, title)
        return None, None
    best, score = None, 0.0
    for w in resp.json().get("results", []):
        s = SequenceMatcher(None, " ".join(words), " ".join(title_words(w.get("title")))).ratio()
        if year and w.get("publication_year") and abs(int(year) - w["publication_year"]) > 1:
            continue
        if s > score:
            best, score = w, s
    if best and score >= args.title_threshold:
        return rp.normalize_doi(best["doi"]) if best.get("doi") else None, best
    return None, None


def openalex_urls(session, limiter, args, log, dois):
    """doi -> [pdf urls], [landing pages] from every OpenAlex location marked open access."""
    out = {}
    key = os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR)
    for batch in rp.chunked(dois, rp.OPENALEX_BATCH_SIZE):
        params = {"filter": "doi:" + "|".join(batch), "per-page": len(batch), "select": "doi,locations",
                  "mailto": args.email}
        if key:
            params["api_key"] = key
        try:
            resp = rp.http_get(session, rp.OPENALEX_API, params, rate_limiter=limiter, max_retries=args.max_retries,
                               timeout=args.timeout, logger=log)
        except rp.RetrievalError as e:
            log.warning("openalex batch failed: %s", e)
            continue
        if resp.status_code != 200:
            log.warning("openalex batch HTTP %d", resp.status_code)
            continue
        for w in resp.json().get("results", []):
            d = rp.normalize_doi(w.get("doi") or "")
            if not d:
                continue
            locs = [l for l in w.get("locations") or [] if l.get("is_oa")]
            out[d.lower()] = ([l["pdf_url"] for l in locs if l.get("pdf_url")],
                              [l["landing_page_url"] for l in locs if l.get("landing_page_url")])
    return out


def unpaywall_urls(session, limiter, args, log, doi):
    try:
        data = rp.query_unpaywall(session, doi, limiter, args, log)
    except rp.RetrievalError:
        return [], []
    locs = (data or {}).get("oa_locations") or []
    return [l["url_for_pdf"] for l in locs if l.get("url_for_pdf")], [l["url"] for l in locs if l.get("url")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--email", default="arthur.j.johnston@gmail.com")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-tries", type=int, default=6)
    ap.add_argument("--title-threshold", type=float, default=0.82)
    ap.add_argument("--no-titles", action="store_true", help="skip the OpenAlex title search")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--min-interval", type=float, default=1.0)
    args = ap.parse_args()
    args.overwrite = False

    work = gi_db.DATA / f"verify_{args.run}"
    papers_dir = work / "papers"
    papers_dir.mkdir(exist_ok=True)
    log = rp.setup_logger(work / "rescue.log")
    st = sqlite3.connect(work / "state.sqlite3", check_same_thread=False)
    st.row_factory = sqlite3.Row
    db_lock = threading.Lock()
    limiter = rp.RateLimiter(args.min_interval)
    local = threading.local()

    def session():
        if not hasattr(local, "s"):
            local.s = requests.Session()
            local.s.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
        return local.s

    def try_urls(title, name_basis, urls):
        dest = papers_dir / f"{rp.slugify(name_basis or 'untitled', 140)}.pdf"
        for u in urls[:args.max_tries]:
            try:
                ok, actual = rp.download_with_landing_page_fallback(session(), u, dest, limiter, args, log)
            except rp.RetrievalError as e:
                log.info("  %s: %s", u, e)
                continue
            if ok:
                log.info("rescued %r <- %s", title, actual)
                return actual, dest
        return None

    # 1. titles
    resolved = 0
    if not args.no_titles:
        rows = st.execute("select key, title, year from papers where status = 'doi_not_found'").fetchall()
        log.info("title search for %d papers with no DOI", len(rows))

        def look(r):
            try:
                doi, work = find_doi_by_title(session(), limiter, args, log, r["title"], r["year"])
            except rp.RetrievalError as e:
                log.warning("title search failed for %r: %s", r["title"], e)
                return r, None, None
            if doi or not work:
                return r, doi, None
            # matched, but the work has no DOI (many conference papers): try its own OA copies here
            locs = [l for l in work.get("locations") or [] if l.get("is_oa")]
            urls = list(dict.fromkeys([l["pdf_url"] for l in locs if l.get("pdf_url")] +
                                      [l["landing_page_url"] for l in locs if l.get("landing_page_url")]))
            return r, None, try_urls(r["title"], r["title"], urls) if urls else None

        no_doi_matches = no_doi_got = 0
        with ThreadPoolExecutor(args.workers) as pool:
            for r, doi, got_no_doi in pool.map(look, rows):
                with db_lock:
                    if doi:
                        st.execute("update papers set doi = ?, status = 'doi_from_title', error = null where key = ?",
                                   (doi, r["key"]))
                        resolved += 1
                    elif got_no_doi:
                        url, dest = got_no_doi
                        st.execute("update papers set status = 'downloaded', pdf_url = ?, file_path = ?, error = null "
                                   "where key = ?", (url, f"papers/{dest.name}", r["key"]))
                        no_doi_got += 1
                    st.commit()
        log.info("title search: %d of %d resolved to a DOI; %d more matched a work with no DOI, downloaded from "
                 "its own copies", resolved, len(rows), no_doi_got)

    # 2. every other copy
    rows = st.execute(f"select key, title, doi, pdf_url, status from papers where doi is not null and status in "
                      f"({','.join('?' * (len(RETRY) + 1))})", (*RETRY, "doi_from_title")).fetchall()
    log.info("%d papers to retry (%s)", len(rows), ", ".join(f"{s} {sum(r['status'] == s for r in rows)}"
                                                           for s in (*RETRY, "doi_from_title")))
    oa = openalex_urls(session(), limiter, args, log, sorted({r["doi"].lower() for r in rows}))

    def rescue(r):
        doi = r["doi"].lower()
        up_pdf, up_land = unpaywall_urls(session(), limiter, args, log, doi)
        oa_pdf, oa_land = oa.get(doi, ([], []))
        tried = r["pdf_url"] if r["status"] != "doi_from_title" else None
        urls, seen = [], {tried}
        for u in up_pdf + oa_pdf + up_land + oa_land:
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        dest = papers_dir / f"{rp.slugify(r['title'] or doi, 100)}-{rp.slugify(doi, 60)}.pdf"
        for u in urls[:args.max_tries]:
            try:
                ok, actual = rp.download_with_landing_page_fallback(session(), u, dest, limiter, args, log)
            except rp.RetrievalError as e:
                log.info("  %s: %s", u, e)
                continue
            if ok:
                return r, "downloaded", actual, dest, len(urls)
        return r, "rescue_failed", None, None, len(urls)

    got = 0
    no_urls = 0
    with ThreadPoolExecutor(args.workers) as pool:
        for i, (r, status, url, dest, n) in enumerate(pool.map(rescue, rows), 1):
            no_urls += n == 0
            with db_lock:
                if status == "downloaded":
                    got += 1
                    st.execute("update papers set status = 'downloaded', pdf_url = ?, file_path = ?, error = null "
                               "where key = ?", (url, f"papers/{dest.name}", r["key"]))
                    log.info("rescued %r <- %s", r["title"], url)
                else:
                    # keep the original status (error, oa_url_not_pdf, ...) so the run reads as before;
                    # a title-found DOI with no copy is recorded as such
                    if r["status"] == "doi_from_title":
                        st.execute("update papers set status = 'no_oa' where key = ?", (r["key"],))
                st.commit()
            if i % 100 == 0:
                log.info("%d/%d tried, %d rescued", i, len(rows), got)
    log.info("done: %d DOIs found by title; %d of %d papers rescued (%d had no other URL)",
             resolved, got, len(rows), no_urls)


if __name__ == "__main__":
    main()
