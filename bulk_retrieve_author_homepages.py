#!/usr/bin/env python3
"""Find PDFs of papers we're missing on the authors' own homepages / CVs.

Every other retrieval path in this project finds a free copy through an index
(Unpaywall, OpenAlex, CORE, Europe PMC, ...). Plenty of academics -- philosophers
especially, i.e. exactly computer ethics's core authors -- post author copies on
a personal or lab "Publications" page or link them from a CV, and those indexes
often don't know about them. ~268K DOIs in computer-ethics/state.sqlite3 have no
PDF (no_oa/error/oa_url_not_pdf/doi_unknown_to_unpaywall) -- this is one more
free shot at some of them.

Pipeline, per author:
  1. Author selection: OpenAlex's `group_by=authorships.author.id` over works in
     dedicated computer-ethics journals (DEFAULT_ISSNS -- the same precise-remit
     list bulk_retrieve_crossref.py --issn was pointed at), ranked by paper count.
     Authors named in any flagged_cases/ write-up are excluded (--exclude-flagged-dir),
     so this is aimed at the field's specialists, not at already-investigated people.
  2. Homepage discovery via identifiers, never by web-searching a bare name:
     OpenAlex's author record carries the author's ORCID; ORCID's public API lists
     the websites the author themselves added to their profile (`researcher-urls`).
     Wikidata's "official website", looked up by OpenAlex author ID or ORCID, is a
     second source -- it covers senior people who never made an ORCID profile.
     Social/aggregator sites (SKIP_DOMAINS) are dropped -- they either block bots
     or aren't the author's own page.
  3. Missing works: the author's OpenAlex works with a DOI we don't already have
     a file for (bulk_retrieve_crossref.load_known_dois(), same "already have it"
     definition every bulk script uses).
  4. Crawl: each homepage, plus up to --max-pages linked pages that look like a
     publications list or CV (FOLLOW_RE), on any host. robots.txt is honored.
     HTML pages are parsed with the stdlib parser; a PDF CV's own hyperlinks are
     read from its link annotations with PyMuPDF.
  5. Match: a link is a candidate for a missing work when the text around it (the
     citation line in a publications list -- the anchor text itself is usually
     just "PDF") contains that work's title (match_title()).
  6. Fetch + verify: download via retrieve_papers.download_with_landing_page_fallback()
     (so a link to a repository landing page still resolves), then require the
     title to actually appear in the PDF's first two pages (pdftotext) --
     a lab page linking a co-author's paper or the talk slides under the same
     title is the realistic false-positive, and this rejects both.

Output is STAGED, not imported: verified PDFs land in --out-dir with a
_manifest.json in exactly list_manual_downloads.py's format, so
    python3 import_manual_downloads.py --db computer-ethics/state.sqlite3 \\
        --manual-dir <out-dir> --papers-dir computer-ethics/papers
folds them in unchanged. _report.json records, per author, how far each step got
(ORCID? homepage? pages crawled? matches? verified?) plus, per hit, whether
OpenAlex already knew an OA location -- i.e. whether the homepage actually
added anything the regular pipeline couldn't have found.
"""

import argparse
import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

import bulk_retrieve_crossref as brc
import retrieve_papers as rp

logger = logging.getLogger("bulk_retrieve_author_homepages")

# Ethics and Information Technology, Philosophy & Technology, AI and Ethics, AI & Society.
# Science and Engineering Ethics is deliberately NOT here: its top authors are research-ethics
# people (Bird, Spier, Resnik), not computer ethics specialists.
DEFAULT_ISSNS = ("1388-1957", "2210-5433", "2730-5953", "0951-5666")

ORCID_API = "https://pub.orcid.org/v3.0/{orcid}/researcher-urls"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
OPENALEX_AUTHORS_API = "https://api.openalex.org/authors/{id}"

SKIP_DOMAINS = (
    "twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com", "youtube.com",
    "scholar.google", "researchgate.net", "academia.edu", "orcid.org", "scopus.com",
    "publons.com", "webofscience.com", "mendeley.com", "semanticscholar.org", "loop.frontiersin.org",
    "bsky.app", "mastodon", "wikipedia.org", "ssrn.com", "dblp.org", "medium.com", "substack.com",
)
FOLLOW_RE = re.compile(r"publication|paper|research|writing|article|bibliograph|\bcv\b|vita|curriculum|"
                        r"resume|output|selected[-_ ]work", re.IGNORECASE)
MAX_PAGE_BYTES = 8 * 1024 * 1024
CONTEXT_CHARS = 350  # chars of page text either side of a link searched for a missing title
MIN_TITLE_WORDS = 3  # shorter titles ("Robot ethics") match too much unrelated text to trust
FUZZY_TITLE_THRESHOLD = 0.9


def normalize(text):
    text = html.unescape(text or "").lower()
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def match_title(title_norm, context_norm):
    """True if the (normalized) title appears in the (normalized) context -- exactly, or
    with minor differences (a typo, a reworded article) in an equal-length window. The
    context window usually spans neighbouring list entries too, so a link can match its
    neighbour's title -- verify_pdf() is what catches that, not this."""
    if not title_norm or len(title_norm.split()) < MIN_TITLE_WORDS:
        return False
    if title_norm in context_norm:
        return True
    words, ctx = title_norm.split(), context_norm.split()
    ctx_set = set(ctx)
    if sum(w in ctx_set for w in words) < 0.75 * len(words):
        return False  # cheap prefilter: a 0.9 fuzzy match can't be missing a quarter of the words
    n = len(words)
    for i in range(0, max(1, len(ctx) - n + 1)):
        window = " ".join(ctx[i:i + n])
        if SequenceMatcher(None, title_norm, window).ratio() >= FUZZY_TITLE_THRESHOLD:
            return True
    return False


class LinkExtractor(HTMLParser):
    """Flattens a page to text while recording where each <a href> sits in it, so a
    link's context (the citation line around a bare "PDF" anchor) can be recovered."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.length, self.links, self._open, self._skip = [], 0, [], None, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            self._open = {"href": href, "start": self.length, "text": ""} if href else None
        elif tag in ("br", "p", "li", "div", "tr", "h1", "h2", "h3", "h4", "dd", "dt"):
            self._add(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._open:
            self._open["end"] = self.length
            self.links.append(self._open)
            self._open = None

    def handle_data(self, data):
        if self._skip:
            return
        self._add(data)
        if self._open:
            self._open["text"] += data

    def _add(self, s):
        self.parts.append(s)
        self.length += len(s)


def html_links(body, base_url):
    parser = LinkExtractor()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except Exception:  # malformed markup -- keep whatever was parsed before it
        pass
    text = "".join(parser.parts)
    out = []
    for link in parser.links:
        end = link.get("end", link["start"])
        context = text[max(0, link["start"] - CONTEXT_CHARS):end + CONTEXT_CHARS]
        out.append({"url": urljoin(base_url, link["href"].strip()), "anchor": link["text"].strip(), "context": context})
    return out


def pdf_links(body):
    """A CV that's itself a PDF: read its link annotations, with the text on the lines
    around each link as context."""
    import fitz
    out = []
    try:
        doc = fitz.open(stream=body, filetype="pdf")
    except Exception:
        return out
    for page in doc:
        words = page.get_text("words")
        for link in page.get_links():
            uri = link.get("uri")
            if not uri:
                continue
            r = link["from"]
            near = [w for w in words if w[1] >= r.y0 - 30 and w[3] <= r.y1 + 30]
            anchor = " ".join(w[4] for w in words if fitz.Rect(w[:4]).intersects(r))
            out.append({"url": uri, "anchor": anchor, "context": " ".join(w[4] for w in near)})
    return out


def is_skipped_domain(url):
    host = urlparse(url).netloc.lower()
    return any(d in host for d in SKIP_DOMAINS)


class Crawler:
    def __init__(self, session, limiter, args):
        self.session, self.limiter, self.args = session, limiter, args
        self._robots, self._robots_lock = {}, threading.Lock()

    def allowed(self, url):
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            rp_ = self._robots.get(base)
        if rp_ is None:
            rp_ = urllib.robotparser.RobotFileParser()
            try:
                resp = self.session.get(base + "/robots.txt", timeout=self.args.timeout)
                rp_.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except requests.RequestException:
                rp_.parse([])
            with self._robots_lock:
                self._robots[base] = rp_
        return rp_.can_fetch(self.session.headers["User-Agent"], url)

    def fetch(self, url):
        """(content_type, body) or (None, None). Capped at MAX_PAGE_BYTES."""
        if not url.startswith(("http://", "https://")) or not self.allowed(url):
            return None, None
        try:
            resp = rp.http_get(self.session, url, None, rate_limiter=self.limiter, max_retries=1,
                                timeout=self.args.timeout, logger=logger, stream=True)
        except rp.RetrievalError:
            return None, None
        try:
            if resp.status_code != 200:
                return None, None
            chunks, size = [], 0
            for chunk in resp.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    return None, None
            body = b"".join(chunks)
        except requests.RequestException:
            return None, None
        finally:
            resp.close()
        kind = "pdf" if body.lstrip()[:4] == b"%PDF" else "html"
        return kind, body

    def links_from(self, url):
        kind, body = self.fetch(url)
        if kind == "pdf":
            return pdf_links(body)
        if kind == "html":
            return html_links(body, url)
        return None

    def crawl(self, homepages):
        """All links on the homepages plus on up to --max-pages publication/CV-looking pages
        they link to. Returns (links, pages_crawled)."""
        links, visited, queue = [], set(), list(homepages)
        followed = 0
        while queue:
            url = queue.pop(0).split("#")[0]
            if url in visited:
                continue
            visited.add(url)
            page_links = self.links_from(url)
            if page_links is None:
                continue
            links.extend(page_links)
            if url not in homepages:
                continue  # only follow one level out from a homepage
            for link in page_links:
                target = link["url"].split("#")[0]
                if (followed < self.args.max_pages and target not in visited and not is_skipped_domain(target)
                        and (FOLLOW_RE.search(link["anchor"]) or FOLLOW_RE.search(urlparse(target).path))):
                    queue.append(target)
                    followed += 1
        return links, len(visited)


def openalex_get(session, limiter, url, args, params=None):
    params = dict(params or {}, mailto=args.email)
    if os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR):
        params["api_key"] = os.environ[rp.OPENALEX_API_KEY_ENV_VAR]
    resp = rp.http_get(session, url, params, rate_limiter=limiter, max_retries=args.max_retries,
                        timeout=args.timeout, logger=logger)
    resp.raise_for_status()
    return resp.json()


def flagged_author_names(flagged_dir):
    """Every normalized author-looking name appearing in a flagged_cases write-up -- matched
    against candidates' OpenAlex display names by containment, which is deliberately loose:
    wrongly excluding a specialist costs one pilot slot, wrongly including a flagged author
    defeats the point."""
    text = ""
    for path in Path(flagged_dir).rglob("*.md"):
        text += " " + path.read_text(encoding="utf-8", errors="replace")
    return normalize(text)


def select_authors(session, limiter, args):
    body = openalex_get(session, limiter, rp.OPENALEX_API, args, {
        "filter": "primary_location.source.issn:" + "|".join(args.issns),
        "group_by": "authorships.author.id", "per-page": 200,
    })
    flagged_text = flagged_author_names(args.exclude_flagged_dir) if args.exclude_flagged_dir else ""
    authors, excluded = [], []
    for group in body.get("group_by", []):
        name = group.get("key_display_name") or ""
        if flagged_text and len(normalize(name)) > 5 and f" {normalize(name)} " in f" {flagged_text} ":
            excluded.append(name)
            continue
        authors.append({"openalex_id": group["key"].rsplit("/", 1)[-1], "name": name,
                         "ethics_journal_papers": group["count"]})
        if len(authors) >= args.num_authors:
            break
    if excluded:
        logger.info("excluded %d author(s) named in flagged cases: %s", len(excluded), ", ".join(excluded))
    return authors


def orcid_homepages(session, limiter, orcid, args):
    orcid_id = orcid.rsplit("/", 1)[-1]
    session.headers["Accept"] = "application/json"  # ORCID defaults to XML; this session is ORCID-only
    resp = rp.http_get(session, ORCID_API.format(orcid=orcid_id), None, rate_limiter=limiter,
                        max_retries=args.max_retries, timeout=args.timeout, logger=logger)
    if resp.status_code != 200:
        return []
    urls = [(u.get("url") or {}).get("value") for u in resp.json().get("researcher-url", [])]
    return [u for u in urls if u and not is_skipped_domain(u)]


def wikidata_homepages(session, limiter, openalex_id, orcid, args):
    """Wikidata's "official website" (P856) for the item carrying this OpenAlex author ID
    (P10283) or ORCID (P496) -- still an identifier join, not a name search. Covers senior
    people who predate ORCID and never made a profile."""
    ids = [f'{{ ?p wdt:P10283 "{openalex_id}" }}']
    if orcid:
        ids.append(f'{{ ?p wdt:P496 "{orcid.rsplit("/", 1)[-1]}" }}')
    query = f"SELECT DISTINCT ?site WHERE {{ {' UNION '.join(ids)} ?p wdt:P856 ?site }}"
    resp = rp.http_get(session, WIKIDATA_SPARQL, {"query": query, "format": "json"}, rate_limiter=limiter,
                        max_retries=args.max_retries, timeout=args.timeout, logger=logger)
    if resp.status_code != 200:
        return []
    urls = [b["site"]["value"] for b in resp.json().get("results", {}).get("bindings", [])]
    return [u for u in urls if not is_skipped_domain(u)]


def missing_works(session, limiter, openalex_id, known_dois, args):
    works = []
    for work in rp.search_openalex_works(session, limiter, f"author.id:{openalex_id}", args.max_works, args, logger):
        paper = rp.parse_openalex_work(work)
        if paper and paper["doi"].lower() not in known_dois:
            paper["title_norm"] = normalize(paper["title"])
            works.append(paper)
    return works


def verify_pdf(path, title_norm):
    try:
        text = subprocess.run(["pdftotext", "-l", "2", str(path), "-"], capture_output=True,
                               timeout=60).stdout.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False
    return match_title(title_norm, normalize(text))


def process_author(author, session, sessions, limiters, crawler, known_dois, claimed, claimed_lock, args):
    rec = dict(author, orcid=None, homepages=[], wikidata_homepages=[], pages_crawled=0, links_found=0, missing_works=0,
               matched=[], downloaded=[], rejected=[], note=None)
    try:
        info = openalex_get(session, limiters["openalex"], OPENALEX_AUTHORS_API.format(id=author["openalex_id"]), args)
        rec["orcid"] = info.get("orcid")
        if rec["orcid"]:
            rec["homepages"] = orcid_homepages(sessions["orcid"], limiters["orcid"], rec["orcid"], args)
        wikidata = wikidata_homepages(session, limiters["wikidata"], author["openalex_id"], rec["orcid"], args)
        rec["wikidata_homepages"] = [u for u in wikidata if u not in rec["homepages"]]
        rec["homepages"] += rec["wikidata_homepages"]
        if not rec["homepages"]:
            rec["note"] = "no website on ORCID or Wikidata" if rec["orcid"] else "no ORCID; no website on Wikidata"
            return rec
        works = missing_works(session, limiters["openalex"], author["openalex_id"], known_dois, args)
        rec["missing_works"] = len(works)
        if not works:
            rec["note"] = "nothing missing"
            return rec
        links, rec["pages_crawled"] = crawler.crawl(rec["homepages"])
        rec["links_found"] = len(links)
    except (rp.RetrievalError, requests.RequestException, ValueError) as exc:
        rec["note"] = f"error: {exc}"
        return rec

    # a DOI link is the publisher copy we already failed to get
    links = [(link["url"], normalize(link["context"])) for link in links
             if not is_skipped_domain(link["url"]) and "doi.org/" not in link["url"]]
    for work in works:
        candidates = [url for url, context in links if match_title(work["title_norm"], context)]
        candidates = list(dict.fromkeys(candidates))[:args.max_links_per_work]
        if not candidates:
            continue
        rec["matched"].append({"doi": work["doi"], "title": work["title"], "urls": candidates})
        with claimed_lock:
            if work["doi"].lower() in claimed:
                continue  # a co-author's page already got this one
            claimed.add(work["doi"].lower())
        paper = {"key": rp.make_key({"doi": work["doi"]}), "title": work["title"], "authors": work["authors"],
                 "year": work["year"], "doi": work["doi"]}
        filename = f"{rp.slugify(work['title'])}-{rp.slugify(work['doi'])}.pdf"
        dest = args.out_dir / filename
        for url in candidates:
            try:
                ok, final_url = rp.download_with_landing_page_fallback(session, url, dest, limiters["download"],
                                                                       args, logger)
            except rp.RetrievalError as exc:
                rec["rejected"].append({"doi": work["doi"], "url": url, "why": str(exc)})
                continue
            if not ok:
                rec["rejected"].append({"doi": work["doi"], "url": url, "why": "not a PDF"})
                continue
            if not verify_pdf(dest, work["title_norm"]):
                dest.unlink(missing_ok=True)
                rec["rejected"].append({"doi": work["doi"], "url": url, "why": "title not on first 2 pages"})
                continue
            rec["downloaded"].append(dict(paper, filename=filename, source_url=final_url,
                                          openalex_knew_oa=bool(work["pdf_url"])))
            logger.info("  %s: got %r from %s", author["name"], work["title"][:70], final_url)
            break
        else:
            with claimed_lock:
                claimed.discard(work["doi"].lower())
    return rec


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True)
    p.add_argument("--db", type=Path, default=Path("computer-ethics/state.sqlite3"))
    p.add_argument("--out-dir", type=Path, default=Path("computer-ethics/homepage_downloads"))
    p.add_argument("--issn", dest="issns", action="append", default=None,
                    help=f"journal(s) whose top authors to pilot on (default: {', '.join(DEFAULT_ISSNS)})")
    p.add_argument("--exclude-flagged-dir", default="computer-ethics/flagged_cases",
                    help="skip authors named in any .md under this dir ('' to disable)")
    p.add_argument("--num-authors", type=int, default=100)
    p.add_argument("--max-works", type=int, default=2000, help="OpenAlex works examined per author")
    p.add_argument("--max-pages", type=int, default=8, help="publication/CV pages followed per author")
    p.add_argument("--max-links-per-work", type=int, default=3)
    p.add_argument("--max-workers", type=int, default=6)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--log-file", type=Path, default=Path("bulk_retrieve_author_homepages.log"))
    args = p.parse_args()
    args.issns = args.issns or list(DEFAULT_ISSNS)
    return args


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler(args.log_file)):
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    session = requests.Session()
    session.headers["User-Agent"] = rp.USER_AGENT_TEMPLATE.format(email=args.email)
    adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    sessions = {"orcid": requests.Session()}
    sessions["orcid"].headers["User-Agent"] = session.headers["User-Agent"]
    limiters = {"openalex": rp.RateLimiter(0.2), "orcid": rp.RateLimiter(0.5), "wikidata": rp.RateLimiter(1.0),
                "download": rp.RateLimiter(1.0)}
    crawler = Crawler(session, rp.RateLimiter(1.0), args)

    store = rp.PaperStore(args.db)
    known_dois = brc.load_known_dois(store.conn)
    store.conn.close()

    authors = select_authors(session, limiters["openalex"], args)
    logger.info("piloting %d author(s), %d DOIs already on file", len(authors), len(known_dois))

    claimed, claimed_lock, records = set(), threading.Lock(), []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = [pool.submit(process_author, a, session, sessions, limiters, crawler, known_dois,
                                claimed, claimed_lock, args)
                   for a in authors]
        for fut in as_completed(futures):
            rec = fut.result()
            records.append(rec)
            logger.info("[%d/%d] %s: homepages=%d missing=%d pages=%d matched=%d downloaded=%d %s",
                        len(records), len(authors), rec["name"], len(rec["homepages"]), rec["missing_works"],
                        rec["pages_crawled"], len(rec["matched"]), len(rec["downloaded"]), rec["note"] or "")

    records.sort(key=lambda r: -r["ethics_journal_papers"])
    manifest_path = args.out_dir / "_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for rec in records:
        for d in rec["downloaded"]:
            manifest[d["filename"]] = {k: d[k] for k in ("key", "title", "year", "doi")} | \
                                      {"authors": json.dumps(d["authors"]), "status": "homepage", "error": None}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (args.out_dir / "_report.json").write_text(json.dumps(records, indent=2), encoding="utf-8")

    n = len(records)
    stat = lambda pred: sum(1 for r in records if pred(r))
    got = [d for r in records for d in r["downloaded"]]
    logger.info("done. %d authors: %d with ORCID, %d with a homepage (ORCID or Wikidata), %d with >=1 match, "
                "%d with >=1 download", n, stat(lambda r: r["orcid"]), stat(lambda r: r["homepages"]),
                stat(lambda r: r["matched"]), stat(lambda r: r["downloaded"]))
    logger.info("missing works across homepage authors: %d; matched: %d; verified PDFs: %d (%d of which OpenAlex "
                "already listed an OA location for)", sum(r["missing_works"] for r in records),
                sum(len(r["matched"]) for r in records), len(got), sum(d["openalex_knew_oa"] for d in got))
    logger.info("staged in %s -- import with: python3 import_manual_downloads.py --db %s --manual-dir %s "
                "--papers-dir computer-ethics/papers", args.out_dir, args.db, args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
