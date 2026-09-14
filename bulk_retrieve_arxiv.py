#!/usr/bin/env python3
"""Bulk-retrieve arXiv papers by category (+ keyword filter) into the same
state.sqlite3 manifest retrieve_papers.py uses -- via arXiv's own sanctioned
bulk-access channels, not the Crossref/Unpaywall per-paper flow, which (per
CLAUDE.md's retrieve_papers.py "Known gap") doesn't reliably resolve arXiv
preprints at all.

  1. Harvest candidate metadata via arXiv's OAI-PMH API (free, no auth,
     server-side category filtering via --set) -- optionally keyword-filtered
     against title+abstract for categories too broad to take unfiltered
     (--filter-set; e.g. cs.LG is huge and mostly unrelated to fairness/bias,
     unlike cs.CY which is arXiv's actual "computers and society" category).
  2. Fetch matching PDFs from arXiv's free public Google Cloud Storage
     mirror (storage.googleapis.com/arxiv-dataset). arXiv's own bulk-data
     docs explicitly ask people not to bulk-download the corpus through
     their normal endpoints ("please do not attempt to download the
     complete corpus programmatically") -- this mirror is the sanctioned
     channel for exactly that, at no cost (unlike their requester-pays S3
     bucket).
  3. Register each download in state.sqlite3 via retrieve_papers.py's own
     PaperStore/make_key/slugify, so extract_papers.py picks it up with
     zero changes -- same manifest, same status='downloaded' contract.

Unlike starting.json entries (which never carry a "doi", so make_key() falls
back to a title-slug key), bulk-retrieved papers are keyed by their real
arXiv DOI (10.48550/arXiv.<id>) -- more robust at this volume than hoping
thousands of title slugs never collide, and it's the one case where we
reliably know the true DOI upfront instead of resolving it via Crossref.

Resumable and dedup-aware: an arXiv ID whose DOI already exists in
state.sqlite3 under ANY key is skipped, so a paper already fetched by hand
(e.g. via starting.json, as several in this corpus were) is recognized and
never re-downloaded under a second key.

Each paper's first submitted version (v1) is fetched -- always present on
the mirror unless withdrawn before ever being announced, which is not a
case OAI-PMH would surface. v2/v3 are tried as a fallback for the rare
paper whose v1 object is missing. This intentionally does not chase the
*latest* version; for finding textual overlap/duplication that's not a
meaningful gap, unlike e.g. tests/cases/carlini-roadmap-2022.json, which
specifically needed a particular version.
"""

import argparse
import datetime
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import retrieve_papers as rp

OAI_BASE = "https://oaipmh.arxiv.org/oai"
GCS_BASE = "https://storage.googleapis.com/arxiv-dataset/arxiv/arxiv"  # "arxiv" appears twice: dataset root, then collection
OAI_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "arxiv": "http://arxiv.org/OAI/arXiv/"}

DEFAULT_KEYWORDS = [
    "fairness", "fair classif", "unfair", "bias", "discriminat", "disparate impact",
    "ethic", "accountab", "transparen", "equitable", "equity",
]

logger = logging.getLogger("bulk_retrieve_arxiv")


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


def matches_keywords(title, abstract, keywords):
    text = f"{title} {abstract}".lower()
    return any(kw in text for kw in keywords)


def parse_record(record):
    meta = record.find(".//arxiv:arXiv", OAI_NS)
    if meta is None:
        return None  # a deleted record -- header-only, nothing to parse

    def text(tag):
        return " ".join((meta.findtext(f"arxiv:{tag}", default="", namespaces=OAI_NS) or "").split())

    arxiv_id = text("id")
    created = text("created")
    year = int(created[:4]) if created[:4].isdigit() else None
    authors = []
    for author in meta.findall("arxiv:authors/arxiv:author", OAI_NS):
        keyname = author.findtext("arxiv:keyname", default="", namespaces=OAI_NS) or ""
        forenames = author.findtext("arxiv:forenames", default="", namespaces=OAI_NS) or ""
        authors.append(" ".join(p for p in (forenames, keyname) if p))

    return {"id": arxiv_id, "title": text("title"), "abstract": text("abstract"),
            "year": year, "authors": authors}


def harvest_set(session, rate_limiter, set_spec, date_from, date_until, keywords, max_retries, timeout):
    """Yields a metadata dict per record in this OAI-PMH set within the date
    range, optionally filtered by keywords (title+abstract substring match)."""
    base_params = {"verb": "ListRecords", "set": set_spec, "metadataPrefix": "arXiv",
                    "from": date_from, "until": date_until}
    resumption_token = None

    while True:
        query = {"verb": "ListRecords", "resumptionToken": resumption_token} if resumption_token else base_params
        resp = rp.http_get(session, OAI_BASE, query, rate_limiter=rate_limiter,
                            max_retries=max_retries, timeout=timeout, logger=logger)
        if resp.status_code != 200:
            raise rp.RetrievalError(f"OAI-PMH request failed: HTTP {resp.status_code}")
        root = ET.fromstring(resp.content)  # bytes, not .text -- avoids ET choking on the XML encoding declaration

        error = root.find("oai:error", OAI_NS)
        if error is not None:
            raise rp.RetrievalError(f"OAI-PMH error ({error.get('code')}): {error.text}")

        for record in root.findall(".//oai:record", OAI_NS):
            paper = parse_record(record)
            if paper is None or not paper["id"]:
                continue
            if keywords and not matches_keywords(paper["title"], paper["abstract"], keywords):
                continue
            yield paper

        token_el = root.find(".//oai:resumptionToken", OAI_NS)
        resumption_token = token_el.text if token_el is not None and token_el.text else None
        if not resumption_token:
            break


def arxiv_doi(arxiv_id):
    return f"10.48550/arXiv.{arxiv_id}"


def gcs_pdf_url(arxiv_id, version):
    prefix = arxiv_id.split(".")[0]
    return f"{GCS_BASE}/pdf/{prefix}/{arxiv_id}v{version}.pdf"


def fetch_arxiv_pdf(session, arxiv_id, dest_path, rate_limiter, dl_args):
    for version in (1, 2, 3):
        try:
            if rp.download_pdf(session, gcs_pdf_url(arxiv_id, version), dest_path, rate_limiter, dl_args, logger):
                return gcs_pdf_url(arxiv_id, version)
        except rp.RetrievalError:
            continue
    return None


def load_known_arxiv_dois(conn):
    """Every arXiv DOI already successfully downloaded in state.sqlite3, under
    ANY key. Deliberately status='downloaded' only -- a prior 'error' row
    (e.g. a transient failure, or the wrong-URL bug this once had) must stay
    retryable, not get treated as permanently resolved."""
    rows = conn.execute(
        "SELECT doi FROM papers WHERE doi LIKE '10.48550/arXiv.%' AND status = 'downloaded'"
    ).fetchall()
    return {doi.lower() for (doi,) in rows}


def parse_args():
    ten_years_ago = (datetime.date.today() - datetime.timedelta(days=3653)).isoformat()
    parser = argparse.ArgumentParser(description="Bulk-retrieve arXiv papers by category into state.sqlite3.")
    parser.add_argument("--email", required=True, help="Contact email sent as part of the User-Agent")
    parser.add_argument("--set", dest="sets", action="append", required=True,
                         help="OAI-PMH set spec, e.g. cs:cs:CY (repeatable). See --list-sets.")
    parser.add_argument("--filter-set", dest="filter_sets", action="append", default=[],
                         help="A --set value to ALSO keyword-filter (title+abstract). "
                              "Sets not listed here are taken unfiltered.")
    parser.add_argument("--keyword", dest="keywords", action="append",
                         help="Keyword for --filter-set matching (repeatable). Default: a bias/fairness/ethics list.")
    parser.add_argument("--from-date", default=ten_years_ago)
    parser.add_argument("--until-date", default=datetime.date.today().isoformat())
    parser.add_argument("--outdir", type=Path, default=Path("papers"))
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--max-papers", type=int, default=None, help="Stop after downloading this many new papers")
    parser.add_argument("--dry-run", action="store_true", help="Harvest and report counts; download nothing")
    parser.add_argument("--min-interval-oai", type=float, default=3.0, help="Seconds between OAI-PMH requests")
    parser.add_argument("--min-interval-gcs", type=float, default=0.3, help="Seconds between GCS PDF fetches")
    parser.add_argument("--max-retries", type=int, default=rp.DEFAULT_MAX_RETRIES)
    parser.add_argument("--timeout", type=float, default=rp.DEFAULT_TIMEOUT)
    parser.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_arxiv.log"))
    args = parser.parse_args()
    if not args.keywords:
        args.keywords = DEFAULT_KEYWORDS
    return args


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    setup_logger(args.log_file)

    session = requests.Session()
    session.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
    oai_limiter = rp.RateLimiter(args.min_interval_oai)
    gcs_limiter = rp.RateLimiter(args.min_interval_gcs)

    store = rp.PaperStore(args.db)
    known_dois = load_known_arxiv_dois(store.conn)

    seen_ids, candidates = set(), []
    for set_spec in args.sets:
        keywords = args.keywords if set_spec in args.filter_sets else None
        logger.info("harvesting set=%s [%s .. %s]%s", set_spec, args.from_date, args.until_date,
                    " (keyword-filtered)" if keywords else "")
        found = new = 0
        for paper in harvest_set(session, oai_limiter, set_spec, args.from_date, args.until_date,
                                  keywords, args.max_retries, args.timeout):
            found += 1
            if paper["id"] in seen_ids:
                continue
            seen_ids.add(paper["id"])
            if arxiv_doi(paper["id"]).lower() in known_dois:
                continue
            candidates.append(paper)
            new += 1
        logger.info("  %s: %d matched, %d new (not already in %s)", set_spec, found, new, args.db)

    logger.info("total: %d new candidate paper(s) across all sets", len(candidates))
    if args.dry_run:
        logger.info("--dry-run: stopping before any download")
        store.conn.close()
        return

    downloaded = 0
    dl_args = argparse.Namespace(max_retries=args.max_retries, timeout=args.timeout)
    for paper in candidates:
        if args.max_papers and downloaded >= args.max_papers:
            logger.info("reached --max-papers %d, stopping", args.max_papers)
            break

        arxiv_id, title = paper["id"], paper["title"]
        doi = arxiv_doi(arxiv_id)
        key = "doi:" + doi.lower()
        filename = f"{rp.slugify(title)}-{rp.slugify(doi)}.pdf"
        dest_path = args.outdir / filename

        try:
            pdf_url = fetch_arxiv_pdf(session, arxiv_id, dest_path, gcs_limiter, dl_args)
        except Exception:
            logger.exception("unexpected error fetching %s (%r)", arxiv_id, title)
            continue

        authors_json = json.dumps(paper["authors"])
        if not pdf_url:
            logger.warning("no PDF found on GCS mirror for %s (%r), tried v1-v3", arxiv_id, title)
            store.upsert(key, title=title, authors=authors_json, year=paper["year"],
                         doi=doi, status="error", error="no PDF on GCS mirror (tried v1-v3)")
            continue

        store.upsert(key, title=title, authors=authors_json, year=paper["year"], doi=doi,
                     status="downloaded", oa_status="green", pdf_url=pdf_url, file_path=str(dest_path))
        downloaded += 1
        if downloaded % 25 == 0:
            logger.info("downloaded %d/%d", downloaded, min(len(candidates), args.max_papers or len(candidates)))

    store.conn.close()
    logger.info("done. downloaded %d new paper(s)", downloaded)


if __name__ == "__main__":
    main()
