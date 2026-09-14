#!/usr/bin/env python3
"""Bulk-retrieve papers by keyword across ALL Crossref-registered publishers
(not just arXiv) into the same state.sqlite3 manifest retrieve_papers.py uses.

Why this exists: bulk_retrieve_arxiv.py only pulls from arXiv's cs.CY/cs.LG
categories -- a single preprint server, one narrow topic slice, and (per the
corpus composition check that prompted this script) 99.94% of this project's
7,806-paper corpus came from exactly that one source. That's a real
limitation for *finding duplication*, not just a diversity nicety: this
project's own documented ground-truth plagiarism cases (see thankyou.md,
tests/README.md) are mostly cross-venue -- an MDPI journal paper copying a
PhD dissertation, a retracted IJACSA paper copying a Procedia Computer
Science paper. A single-preprint-server corpus structurally can't contain
that pattern; there's no dissertation, no MDPI, no IJACSA in an arXiv-only
pull. Crossref's public works index (~180M+ records) covers virtually every
DOI-registered publisher -- journals, conference proceedings, preprint
servers other than arXiv -- so searching it directly is the natural way to
add source diversity rather than just growing the same arXiv slice further.
todo.md's original data-sources ask was "computer ethics and adjacent
fields... look for other free sources if need be... ask me before
downloading from a website though" -- this is that "ask first" step made
concrete as a runnable (but not yet run) script.

Pipeline, per --keyword:
  1. Search Crossref's /works endpoint (query.bibliographic=<keyword>,
     filter=from-pub-date/until-pub-date) with cursor-based pagination
     (Crossref's recommended approach beyond ~10k results, but used here
     regardless since it's the same amount of code either way and keeps
     --max-per-keyword bounded cheaply).
  2. For each candidate DOI not already status='downloaded' in state.sqlite3
     (any key), reuse retrieve_papers.py's query_unpaywall()/download_pdf()
     exactly as retrieve_papers.py's own process_paper() does -- same OA
     resolution logic, same PDF-sniffing download, same state.sqlite3
     schema, so extract_papers.py needs zero changes to pick these up.

Deliberately does NOT resolve_doi() (retrieve_papers.py's title/author
fuzzy-match against Crossref) -- Crossref search already hands back a DOI
directly for each candidate, same reasoning bulk_retrieve_arxiv.py gives for
skipping it with arXiv IDs.

Resumable/dedup-aware the same way bulk_retrieve_arxiv.py is: a DOI already
status='downloaded' under any key is skipped, never re-fetched.
"""

import argparse
import datetime
import json
import logging
import os
from pathlib import Path

import requests

import retrieve_papers as rp

CROSSREF_WORKS_API = "https://api.crossref.org/works"

# "Computer ethics and adjacent fields" per todo.md's original ask -- deliberately broader
# than bulk_retrieve_arxiv.py's DEFAULT_KEYWORDS (which is specifically fairness/bias/ML),
# since the whole point of this script is covering ground that one doesn't.
#
# 2026-08-13: checked library.sqlite3 title coverage against a set of candidate topics and
# found several narrow, dupe-prone application niches were thin (title substring counts out
# of 8,306 papers at the time): robot ethics=2, predictive polic=4, recidivis=10, credit
# scor=11, facial recognition=11, criminal justice=11, lending=10, employment=9,
# content moderation=20, differential privacy=16, AI safety=26 -- vs. e.g. "language model"
# already at 487 just from incidental overlap with the fairness/bias arxiv slice. These are
# exactly the kind of narrow, heavily-templated case-study topics (COMPAS/recidivism,
# Amazon's hiring tool, facial-recognition audits) that produce a lot of reused boilerplate
# across survey/case-study papers, so dedicated venue-diverse searches for them are more
# likely to surface real overlap than broader terms already well covered. Added below.
DEFAULT_KEYWORDS = [
    "computer ethics", "technology ethics", "digital ethics", "data ethics",
    "algorithmic accountability", "responsible AI", "AI governance",
    "surveillance ethics", "privacy ethics", "information ethics",
    "algorithmic bias", "algorithmic fairness", "AI ethics",
    "explainable AI", "algorithmic auditing", "trustworthy AI", "AI safety",
    "algorithmic hiring", "algorithmic lending", "criminal justice algorithms",
    "predictive policing", "facial recognition bias", "content moderation",
    "differential privacy", "robot ethics",
]

logger = logging.getLogger("bulk_retrieve_crossref")


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


def parse_crossref_item(item):
    doi = item.get("DOI")
    if not doi:
        return None
    title = " ".join(item.get("title") or []).strip()
    if not title:
        return None
    authors = []
    for a in item.get("author") or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p)
        if name:
            authors.append(name)
    year = None
    for date_field in ("published-print", "published-online", "created", "issued"):
        parts = (item.get(date_field) or {}).get("date-parts")
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    return {"doi": doi, "title": title, "authors": authors, "year": year}


def search_crossref(session, rate_limiter, keyword, date_from, date_until, max_results, args, doi_prefix=None,
                     container_title=None, issn=None):
    """Yields parsed candidate dicts for one keyword, cursor-paginated.
    doi_prefix (e.g. "10.34218") restricts results to one publisher's
    registered DOI-prefix block via Crossref's own `filter=prefix:...` --
    see --doi-prefix's help text for why this exists.

    container_title (e.g. "Security and Communication Networks") restricts
    results to one specific journal via Crossref's `query.container-title`
    free-text field -- see --container-title's help text for why this
    exists. A relevance-ranked text query, not an exact filter (Crossref has
    no `filter=container-title:...`), so it can pick up a same/similar-named
    journal at a different publisher -- combine with doi_prefix when the
    target publisher is known, same as --container-title's own help text
    recommends.

    issn (e.g. "1388-1957") restricts results to one specific journal via
    Crossref's own `filter=issn:...` -- an EXACT match, unlike container_title,
    so it can't drift onto a same/similar-named journal at a different
    publisher. See --issn's help text for why this exists.

    keyword=None omits the query.bibliographic term entirely, returning
    every work matching the filter (date range + prefix/issn, if any) with no
    bibliographic-relevance restriction at all -- this is what --whole-prefix
    and --issn use to walk a publisher's/journal's entire catalog instead of
    only the slice that happens to match one of DEFAULT_KEYWORDS. See
    --whole-prefix's help text for why a keyword-restricted crawl structurally
    can't reach 100% of a given prefix even after every keyword has been tried
    -- the same logic applies to a single journal restricted by --issn."""
    filter_parts = [f"from-pub-date:{date_from}", f"until-pub-date:{date_until}", "type:journal-article"]
    if doi_prefix:
        filter_parts.append(f"prefix:{doi_prefix}")
    if issn:
        filter_parts.append(f"issn:{issn}")
    cursor = "*"
    seen = 0
    while seen < max_results:
        params = {
            "filter": ",".join(filter_parts),
            "rows": min(200, max_results - seen),
            "cursor": cursor,
            "mailto": args.email,
        }
        if keyword:
            params["query.bibliographic"] = keyword
        if container_title:
            params["query.container-title"] = container_title
        resp = rp.http_get(session, CROSSREF_WORKS_API, params, rate_limiter=rate_limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=logger)
        if resp.status_code != 200:
            raise rp.RetrievalError(f"crossref search failed: HTTP {resp.status_code}")
        message = resp.json().get("message", {})
        items = message.get("items", [])
        if not items:
            break
        for item in items:
            paper = parse_crossref_item(item)
            if paper:
                yield paper
            seen += 1
        cursor = message.get("next-cursor")
        if not cursor:
            break


def load_known_dois(conn):
    """Every DOI already accounted for in state.sqlite3, under ANY key --
    same reasoning as bulk_retrieve_arxiv.py's load_known_arxiv_dois.

    Deliberately `file_path IS NOT NULL`, not `status = 'downloaded'`: a row
    filter_low_relevance_papers.py excluded (status 'excluded_crank'/
    'excluded_offtopic') still has its (relocated) file_path set -- see that
    script's docstring -- and MUST be treated as already-handled here too, or
    every bulk_retrieve_*.py script sharing this function would just
    re-download the exact crank/off-topic paper that was deliberately pulled
    out of the pipeline, silently undoing that work on the very next run.
    A DOI that was only ever attempted and failed for a real retrieval reason
    (status 'error'/'no_oa'/'doi_unknown_to_unpaywall'/'oa_url_not_pdf', no
    file_path) is correctly still absent here and gets retried, unchanged."""
    rows = conn.execute("SELECT doi FROM papers WHERE doi IS NOT NULL AND file_path IS NOT NULL").fetchall()
    return {doi.lower() for (doi,) in rows if doi}


def fetch_candidate(session, paper, store, unpaywall_limiter, download_limiter, args, openalex_hits=None):
    """openalex_hits: {doi.lower(): {"oa_status", "pdf_url"}}, pre-resolved by a
    batched rp.query_openalex_batch() call in main() before this ever runs -- see
    its docstring. A hit here skips query_unpaywall() entirely (the whole point:
    Unpaywall has no batch endpoint and is the actual bottleneck at bulk-retrieval
    scale); a miss falls back to the original one-DOI-at-a-time Unpaywall lookup,
    unchanged.

    Downloads via download_with_landing_page_fallback() (not plain download_pdf()) --
    added 2026-08-25 after a live bulk_retrieve_author_works.py run (this function
    reused there unchanged) landed 721 of ~3,700 candidates in oa_url_not_pdf: a
    real OA URL that turned out not to be a direct PDF, the exact landing-page
    case retrieve_papers.py's citation_pdf_url fallback already exists for and
    bulk_retrieve_theses.py already relies on for the same reason (see that
    fallback's own docstring: confirmed to rescue real downloads, not just
    theoretically). Applying it here benefits both callers of this shared
    function (bulk_retrieve_crossref.py's own keyword/whole-prefix crawls hit
    arbitrary publisher sites too, not just institutional thesis repositories).

    Live pre-check + atomic claim (added after a real cross-process corruption
    incident -- see bulk_retrieve_theses.py's fetch_candidate() docstring for
    the full writeup, and PaperStore.claim()'s own docstring / todo.md's
    "Cross-process retrieval dedup race" entry): this function is shared by
    both bulk_retrieve_crossref.py and bulk_retrieve_author_works.py, either
    of which can run for tens of minutes concurrently with another retrieval
    process against the same state.sqlite3 -- main()'s known_dois is only a
    startup-time snapshot, not re-checked per candidate, so without a live
    check here this call's own final upsert() could silently overwrite a
    'downloaded' row another process wrote in the meantime."""
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

        if data is None:
            store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi,
                         status="doi_unknown_to_unpaywall")
            return "doi_unknown_to_unpaywall"

        oa_status = data.get("oa_status")
        best_loc = data.get("best_oa_location") or {}
        pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")
        if not pdf_url:
            store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi,
                         status="no_oa", oa_status=oa_status)
            return "no_oa"

    filename = f"{rp.slugify(title)}-{rp.slugify(doi)}.pdf"
    dest_path = args.outdir / filename

    try:
        ok, worked_url = rp.download_with_landing_page_fallback(session, pdf_url, dest_path, download_limiter,
                                                                  args, logger)
    except rp.RetrievalError as exc:
        logger.error("download error for %r: %s", title, exc)
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="error",
                     oa_status=oa_status, pdf_url=pdf_url, error=str(exc))
        return "error"

    if ok:
        logger.info("downloaded %r -> %s", title, dest_path.name)
        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="downloaded",
                     oa_status=oa_status, pdf_url=worked_url, file_path=str(dest_path))
        return "downloaded"

    store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi, status="oa_url_not_pdf",
                 oa_status=oa_status, pdf_url=pdf_url)
    return "oa_url_not_pdf"


def parse_args():
    ten_years_ago = (datetime.date.today() - datetime.timedelta(days=3653)).isoformat()
    parser = argparse.ArgumentParser(description="Bulk-retrieve papers by keyword across all Crossref publishers.")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent + mailto=")
    parser.add_argument("--keyword", dest="keywords", action="append",
                         help="Crossref query.bibliographic term (repeatable). Default: a computer-ethics/"
                              "adjacent-fields list distinct from bulk_retrieve_arxiv.py's fairness-specific one.")
    parser.add_argument("--doi-prefix", dest="doi_prefixes", action="append",
                         help="Restrict the search to one publisher's registered DOI-prefix block "
                              "(repeatable), combined with each --keyword via Crossref's filter=prefix:... "
                              "-- e.g. --doi-prefix 10.34218 (IAEME) or --doi-prefix 10.63282. Added after "
                              "the 2026-08-21 full-corpus plagiarism audit found this project's two "
                              "templated/paper-mill-flavored near-dupes both came from low-scrutiny "
                              "publisher families that keyword search only turned up by accident -- walking "
                              "a known lower-scrutiny publisher's own catalog on purpose, the way this flag "
                              "does, is a more direct way to find more of that pattern than hoping keyword "
                              "search stumbles onto it again. See todo.md's 'Full-corpus plagiarism audit' "
                              "section. Default (omitted): no prefix restriction, search all publishers, "
                              "same as before this flag existed.")
    parser.add_argument("--whole-prefix", action="store_true",
                         help="Crawl every work under --doi-prefix directly (no query.bibliographic term at "
                              "all), instead of the usual per-keyword search. Requires --doi-prefix; "
                              "incompatible with --keyword (a keyword-restricted crawl and a whole-catalog "
                              "crawl are different operations, not additive). Added because even trying every "
                              "one of DEFAULT_KEYWORDS against a --doi-prefix leaves real gaps -- a live check "
                              "against 10.34218 (IAEME) found only 56% coverage (2,844 of 5,088 works actually "
                              "registered under that prefix) after a full keyword sweep, because a publisher "
                              "with dozens of unrelated sub-journals (engineering, business, cloud computing, "
                              "...) has plenty of content no AI-ethics keyword will ever match -- including, "
                              "plausibly, the other half of more template/paper-mill duplicate pairs. "
                              "--max-per-keyword still caps how many results this pulls (raise it to the "
                              "prefix's actual total work count, checkable via "
                              "https://api.crossref.org/works?filter=prefix:X&rows=0, to get everything).")
    parser.add_argument("--container-title", dest="container_titles", action="append",
                         help="Restrict the search to one specific journal (repeatable) via Crossref's "
                              "query.container-title free-text field, instead of a --keyword bibliographic "
                              "search -- e.g. --container-title 'Security and Communication Networks'. "
                              "Combine with --doi-prefix for precision (query.container-title is a "
                              "relevance-ranked text match, not an exact filter -- Crossref has no "
                              "filter=container-title:..., so an unscoped journal-name query can pick up a "
                              "same/similar-named journal at a different publisher). Added to target specific "
                              "journals named in Wiley's own 2023-2024 Hindawi mass-retraction disclosure "
                              "(11,300+ papers, confirmed systemic paper-mill infiltration) -- pulling "
                              "--doi-prefix 10.1155's entire ~546K-work catalog via --whole-prefix would "
                              "include huge amounts of unrelated medicine/chemistry content outside what that "
                              "disclosure actually implicated; this targets just the named journals instead. "
                              "Incompatible with --keyword and --whole-prefix (both are different scoping "
                              "mechanisms for the same 'no bibliographic keyword' mode --container-title uses).")
    parser.add_argument("--issn", dest="issns", action="append",
                         help="Restrict the search to one specific journal (repeatable) via Crossref's "
                              "filter=issn:... -- an EXACT match, unlike --container-title's free-text "
                              "query, so it can't drift onto a same/similar-named journal at a different "
                              "publisher. Prefer this over --container-title whenever the target journal's "
                              "ISSN is known. Defaults keywords to a single unrestricted pass (same as "
                              "--whole-prefix) rather than requiring --keyword too -- a paper published IN "
                              "a dedicated ethics-of-technology journal is on-topic by construction, so "
                              "restricting it further by a bibliographic keyword would just reintroduce the "
                              "'only found what the keyword happened to match' gap --whole-prefix exists to "
                              "avoid, applied here to journal scope instead of publisher scope. Combine with "
                              "--from-date/--until-date to bound how much of the journal's back catalog gets "
                              "pulled; combine with --doi-prefix too if the publisher is also known, though "
                              "issn: alone is already exact. Added to target precise-remit computer/AI/tech-"
                              "ethics journals (Ethics and Information Technology, Philosophy & Technology, "
                              "Science and Engineering Ethics, AI and Ethics, AI & SOCIETY) after a review "
                              "found the DEFAULT_KEYWORDS-driven corpus was heavily off-topic -- see todo.md.")
    parser.add_argument("--from-date", default=ten_years_ago)
    parser.add_argument("--until-date", default=datetime.date.today().isoformat())
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--max-per-keyword", type=int, default=1000,
                         help="Stop harvesting candidates for a keyword after this many Crossref results (bounds "
                              "network use against Crossref's ~180M-record index; default 1000)")
    parser.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers total")
    parser.add_argument("--max-workers", type=int, default=8,
                         help="Concurrent fetch threads for the download phase (default 8) -- candidates go to "
                              "hundreds of different hosts, so this is real parallelism, not just queuing against "
                              "the same rate limit; a shared per-host RateLimiter still protects any single host")
    parser.add_argument("--dry-run", action="store_true", help="Harvest and report counts; query Unpaywall/download nothing")
    parser.add_argument("--min-interval-crossref", type=float, default=1.0, help="Seconds between Crossref requests")
    parser.add_argument("--min-interval-unpaywall", type=float, default=1.0, help="Seconds between Unpaywall requests")
    parser.add_argument("--min-interval-openalex", type=float, default=0.3,
                         help="Seconds between OpenAlex batch-lookup requests (default 0.3 -- well under "
                              "OpenAlex's documented 10 req/sec ceiling; each request resolves up to "
                              f"{rp.OPENALEX_BATCH_SIZE} DOIs at once, so this is not comparable to "
                              "--min-interval-unpaywall's per-DOI pacing)")
    parser.add_argument("--no-openalex", action="store_true",
                         help="Skip the OpenAlex batch pre-resolution pass, go straight to Unpaywall "
                              "per-DOI lookups for everything (the old behavior). OpenAlex has no batch "
                              "endpoint... wait, it does (filter=doi:a|b|c, up to 50 at once) -- Unpaywall "
                              "doesn't, which is what makes it the bottleneck at bulk-retrieval scale (tens "
                              "of thousands of DOIs at ~1/sec = many hours). OpenAlex ingests Unpaywall's "
                              "own data plus more, so this isn't a coverage tradeoff -- a DOI OpenAlex "
                              "doesn't resolve still falls back to Unpaywall automatically either way; "
                              "--no-openalex is for debugging/comparison, not normal use.")
    parser.add_argument("--min-interval-download", type=float, default=0.5, help="Seconds between PDF downloads")
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_crossref.log"))
    parser.add_argument("--no-key-ok", action="store_true",
                         help="Accepted but otherwise unused here -- exists so the require_api_keys.py "
                              "PreToolUse hook's documented escape hatch (append this flag to run "
                              "anonymously/slower without the usual API key) is valid argv for this "
                              "script instead of an 'unrecognized arguments' error.")
    args = parser.parse_args()
    if args.whole_prefix:
        if not args.doi_prefixes:
            parser.error("--whole-prefix requires --doi-prefix (it crawls a specific publisher's whole catalog)")
        if args.keywords:
            parser.error("--whole-prefix is incompatible with --keyword -- it deliberately skips "
                          "query.bibliographic entirely, so a keyword given alongside it would be silently ignored")
        if args.container_titles:
            parser.error("--whole-prefix is incompatible with --container-title -- --whole-prefix wants the "
                          "WHOLE prefix with no restriction, --container-title wants a specific journal; "
                          "pick one scoping mechanism")
        args.keywords = [None]  # one harvest pass per prefix, no bibliographic term
    elif args.container_titles:
        if args.keywords:
            parser.error("--container-title is incompatible with --keyword -- both are different scoping "
                          "mechanisms for the same 'no bibliographic keyword' mode; pick one")
        args.keywords = [None]
    elif args.issns:
        if args.keywords:
            parser.error("--issn is incompatible with --keyword -- a journal restricted by exact ISSN is "
                          "on-topic by construction, so also requiring a keyword match would just reintroduce "
                          "the coverage gap --whole-prefix/--issn exist to avoid; pick one scoping mechanism")
        args.keywords = [None]
    elif not args.keywords:
        args.keywords = DEFAULT_KEYWORDS
    return args


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    crossref_limiter = rp.RateLimiter(args.min_interval_crossref)
    unpaywall_limiter = rp.RateLimiter(args.min_interval_unpaywall)
    openalex_limiter = rp.RateLimiter(args.min_interval_openalex)
    download_limiter = rp.RateLimiter(args.min_interval_download)

    store = rp.PaperStore(args.db)
    known_dois = load_known_dois(store.conn)

    # None as the sole entry means "no prefix/container-title/issn restriction" -- the loop body
    # below is identical either way, just with doi_prefix/container_title/issn=None passed through
    # unchanged.
    prefixes = args.doi_prefixes or [None]
    container_titles = args.container_titles or [None]
    issns = args.issns or [None]

    seen_dois, candidates = set(), []
    for doi_prefix in prefixes:
        for container_title in container_titles:
            for issn in issns:
                for keyword in args.keywords:
                    label = (f"keyword={keyword!r}" if keyword else
                             (f"container_title={container_title!r}" if container_title else
                              (f"issn={issn!r}" if issn else "whole-prefix"))) + \
                            (f" doi_prefix={doi_prefix!r}" if doi_prefix else "")
                    logger.info("harvesting %s [%s .. %s], up to %d result(s)",
                                label, args.from_date, args.until_date, args.max_per_keyword)
                    found = new = 0
                    for paper in search_crossref(session, crossref_limiter, keyword, args.from_date,
                                                  args.until_date, args.max_per_keyword, args,
                                                  doi_prefix=doi_prefix, container_title=container_title,
                                                  issn=issn):
                        found += 1
                        doi_norm = rp.normalize_doi(paper["doi"])
                        if not doi_norm or doi_norm.lower() in seen_dois:
                            continue
                        seen_dois.add(doi_norm.lower())
                        if doi_norm.lower() in known_dois:
                            continue
                        candidates.append(paper)
                        new += 1
                    logger.info("  %s: %d found, %d new (not already in %s)", label, found, new, args.db)

    logger.info("total: %d new candidate paper(s) across all keyword/prefix combination(s)", len(candidates))
    if args.dry_run:
        logger.info("--dry-run: stopping before any Unpaywall/download activity")
        store.conn.close()
        return
    store.conn.close()  # harvest phase is single-threaded; download phase uses its own per-thread connections

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
                # Confirmed to happen for real (see todo.md), not a defensive-only branch:
                # once OpenAlex starts 429-ing us, every remaining batch would hit the same
                # wall -- stop calling it entirely rather than retrying batch after batch
                # (each fast-failing per query_openalex_batch's max_retries=0, but still
                # thousands of wasted round-trips, and continuing to hammer a service that
                # just told us to back off is bad manners regardless of speed).
                remaining = len(dois) - len(openalex_hits)
                logger.warning("OpenAlex rate-limited us -- stopping OpenAlex lookups for the rest of this "
                                "run, %d remaining candidate(s) will use per-DOI Unpaywall instead", remaining)
                break
        logger.info("OpenAlex resolved %d/%d candidate(s) directly -- %d will fall back to per-DOI Unpaywall",
                    len(openalex_hits), len(dois), len(dois) - len(openalex_hits))

    # Candidates go to hundreds of different hosts -- one at a time serialized total wall-clock
    # time across all of them even though RateLimiter is already per-host and nobody was actually
    # being throttled against each other. concurrent_fetch() bounds real parallelism instead
    # (see its docstring in retrieve_papers.py).
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

    logger.info("done. downloaded %d new paper(s)", downloaded)


if __name__ == "__main__":
    main()
