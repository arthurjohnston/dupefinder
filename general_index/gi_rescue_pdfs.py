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
                         [--no-archives | --archives-only]
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

import fitz  # PyMuPDF
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402

import gi_db  # noqa: E402

RETRY = ("error", "oa_url_not_pdf", "doi_unknown_to_unpaywall")
WAYBACK_API = "https://archive.org/wayback/available"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PMC_ARTICLE = "https://pmc.ncbi.nlm.nih.gov/articles/{}/"
EPMC_BATCH = 25          # DOIs per Europe PMC query (DOI:"a" OR DOI:"b" ...)
MIN_PMC_WORDS = 500      # less than this on a PMC page: an abstract or a scanned article, not the text
# PMC's page furniture: search links under the byline, link labels after each reference
PMC_CHROME = re.compile(r"\[ (?:DOI|PubMed|PMC free article|Google Scholar|CrossRef|Free full text) \]")
PMC_CHROME_BLOCKS = {"Search in PMC", "Search in PubMed", "View in NLM Catalog", "Add to search",
                     "Author information", "Article notes", "Copyright and License information"}


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


def title_on_pages(path, title, pages=2):
    """Most (80%) of the title's words on the PDF's first pages. A Wayback snapshot is of a URL, and a URL
    can serve a different file than the one the index paired (a journal reusing paths, a cover sheet)."""
    words = [w for w in title_words(title) if len(w) > 2]
    if not words:
        return True
    try:
        with fitz.open(path) as doc:
            have = set(title_words(" ".join(doc[i].get_text() for i in range(min(pages, len(doc))))))
    except Exception:
        return False
    return sum(w in have for w in words) / len(words) >= 0.8


def wayback_copy(session, limiter, args, log, urls, dest, title):
    """The raw archived file (the id_ form: no Wayback toolbar) of the first URL with a 200 snapshot that is a
    PDF of this paper; that snapshot's URL, or None."""
    for u in urls[:args.max_tries]:
        try:
            resp = rp.http_get(session, WAYBACK_API, {"url": u}, rate_limiter=limiter,
                               max_retries=args.max_retries, timeout=args.timeout, logger=log)
            snap = (resp.json().get("archived_snapshots") or {}).get("closest") or {}
        except (rp.RetrievalError, ValueError) as e:
            log.info("  wayback lookup %s: %s", u, e)
            continue
        if not snap.get("available") or snap.get("status") != "200":
            continue
        raw = f"https://web.archive.org/web/{snap['timestamp']}id_/{u}"
        try:
            ok = rp.download_pdf(session, raw, dest, limiter, args, log)
        except rp.RetrievalError as e:
            log.info("  %s: %s", raw, e)
            continue
        if ok and title_on_pages(dest, title):
            return raw
        if ok:
            log.info("  %s: a PDF, but unreadable or not of %r", raw, title)
        dest.unlink(missing_ok=True)
    return None


def pmc_ids(session, limiter, args, log, dois):
    """doi -> PMCID, from Europe PMC's search (EPMC_BATCH DOIs a query)."""
    out = {}
    for batch in rp.chunked(dois, EPMC_BATCH):
        params = {"query": " OR ".join(f'DOI:"{d}"' for d in batch), "format": "json", "resultType": "lite",
                  "pageSize": 100}
        try:
            resp = rp.http_get(session, EPMC_SEARCH, params, rate_limiter=limiter, max_retries=args.max_retries,
                               timeout=args.timeout, logger=log)
            res = resp.json().get("resultList", {}).get("result", [])
        except (rp.RetrievalError, ValueError) as e:
            log.warning("Europe PMC batch failed: %s", e)
            continue
        for r in res:
            if r.get("pmcid") and r.get("doi"):
                out[r["doi"].lower()] = r["pmcid"]
    return out


def pmc_text(page):
    """(title, [(tag, text)]) from a PMC article page: the h1, then every heading and paragraph of the
    article body in order, references included (as a PDF's extracted text would have them)."""
    m = re.search(r"<article\b.*?</article>", page, re.S)
    body = m.group(0) if m else page
    t = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    clean = lambda h: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()
    blocks = [(tag, PMC_CHROME.sub("", clean(inner)).strip())
              for tag, inner in re.findall(r"<(h[2-4]|p|li)\b[^>]*>(.*?)</\1>", body, re.S)]
    return (clean(t.group(1)) if t else ""), [(tag, x) for tag, x in blocks if x and x not in PMC_CHROME_BLOCKS]


def write_text_pdf(dest, title, blocks):
    """A plain A4 PDF of title + blocks, laid out by PyMuPDF's Story."""
    esc = html.escape
    doc_html = f"<h1>{esc(title)}</h1>" + "".join(
        f"<h3>{esc(x)}</h3>" if tag[0] == "h" else f"<p>{esc(x)}</p>" for tag, x in blocks)
    story = fitz.Story(html=doc_html, user_css="body{font-size:10pt} h1{font-size:14pt}")
    tmp = dest.with_suffix(".pdf.part")
    writer = fitz.DocumentWriter(str(tmp))
    page, more = fitz.paper_rect("a4"), 1
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(page + (50, 50, -50, -50))
        story.draw(dev)
        writer.end_page()
    writer.close()
    tmp.rename(dest)


def pmc_copy(session, limiter, args, log, pmcid, dest, title):
    """PMC's article page as a text PDF, when it is this paper (title similarity >= --title-threshold) and
    carries the full text (MIN_PMC_WORDS+); the page's URL, or None."""
    url = PMC_ARTICLE.format(pmcid)
    try:
        resp = rp.http_get(session, url, None, rate_limiter=limiter, max_retries=args.max_retries,
                           timeout=args.timeout, logger=log)
    except rp.RetrievalError as e:
        log.info("  %s: %s", url, e)
        return None
    if resp.status_code != 200:
        log.info("  %s: HTTP %d", url, resp.status_code)
        return None
    got_title, blocks = pmc_text(resp.text)
    sim = SequenceMatcher(None, " ".join(title_words(title)), " ".join(title_words(got_title))).ratio()
    words = sum(len(x.split()) for _, x in blocks)
    if sim < args.title_threshold or words < MIN_PMC_WORDS:
        log.info("  %s: not used (title similarity %.2f, %d words)", url, sim, words)
        return None
    write_text_pdf(dest, got_title, blocks)
    return url


def archives(st, db_lock, session, limiter, args, log, papers_dir):
    """Step 3: Wayback, then PMC, for every paper still missing (PMC alone for no_oa ones)."""
    rows = st.execute(f"select key, title, doi, pdf_url, status from papers where doi is not null and status in "
                      f"({','.join('?' * (len(RETRY) + 1))})", (*RETRY, "no_oa")).fetchall()
    log.info("archives: %d papers (%d no_oa: PMC only)", len(rows), sum(r["status"] == "no_oa" for r in rows))
    dois = sorted({r["doi"].lower() for r in rows})
    oa = openalex_urls(session(), limiter, args, log, [d for r in rows if r["status"] != "no_oa"
                                                       for d in [r["doi"].lower()]])
    pmc = pmc_ids(session(), limiter, args, log, dois)
    log.info("archives: %d of %d DOIs have a PMC copy", len(pmc), len(dois))

    def one(r):
        doi = r["doi"].lower()
        base = f"{rp.slugify(r['title'] or doi, 100)}-{rp.slugify(doi, 60)}"
        if r["status"] != "no_oa":
            pdf, land = oa.get(doi, ([], []))
            urls = list(dict.fromkeys(u for u in [r["pdf_url"], *pdf, *land] if u))
            got = wayback_copy(session(), limiter, args, log, urls, papers_dir / f"{base}.pdf", r["title"] or "")
            if got:
                return r, "wayback", got, papers_dir / f"{base}.pdf"
        if doi in pmc:
            dest = papers_dir / f"{base}-pmc.pdf"
            got = pmc_copy(session(), limiter, args, log, pmc[doi], dest, r["title"] or "")
            if got:
                return r, "pmc", got, dest
        return r, None, None, None

    got = {"wayback": 0, "pmc": 0}
    with ThreadPoolExecutor(args.workers) as pool:
        for i, (r, how, url, dest) in enumerate(pool.map(one, rows), 1):
            if how:
                got[how] += 1
                with db_lock:
                    st.execute("update papers set status = 'downloaded', pdf_url = ?, file_path = ?, error = null "
                               "where key = ?", (url, f"papers/{dest.name}", r["key"]))
                    st.commit()
                log.info("rescued (%s) %r <- %s", how, r["title"], url)
            if i % 100 == 0:
                log.info("archives: %d/%d tried, %s", i, len(rows), got)
    log.info("archives done: %d from the Wayback Machine, %d from PMC, of %d papers",
             got["wayback"], got["pmc"], len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--email", default="arthur.j.johnston@gmail.com")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-tries", type=int, default=6)
    ap.add_argument("--title-threshold", type=float, default=0.82)
    ap.add_argument("--no-titles", action="store_true", help="skip the OpenAlex title search")
    ap.add_argument("--no-archives", action="store_true", help="skip the Wayback Machine / PMC step")
    ap.add_argument("--archives-only", action="store_true",
                    help="only the Wayback Machine / PMC step (a run whose rescue already ran)")
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

    if args.archives_only:
        archives(st, db_lock, session, limiter, args, log, papers_dir)
        return

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
    if not args.no_archives:
        archives(st, db_lock, session, limiter, args, log, papers_dir)


if __name__ == "__main__":
    main()
