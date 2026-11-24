#!/usr/bin/env python3
"""Find copying leads across a whole research field from OpenAlex abstracts alone.

Harvest every abstract OpenAlex has for a field, find the pairs of works whose abstracts share a long
verbatim run, sort out the pairs that aren't copying (one work recorded twice, a shared author, OpenAlex
metadata errors, a book's blurb on every chapter...), then download both sides of what's left and measure
their full text.

  harvest   OpenAlex works matching --filter (repeatable; has_abstract:true is added), 200 per call by
            cursor, into the `works` table. Resumable: re-run the same command after an interruption.
  pairs     every pair of works whose abstracts share a run of >= --min-run (20) words, classified (see
            classify()). Rebuilds the `pairs` table each time. Needs numpy.
  fulltext  for 'lead' pairs with an open-access location on both sides: download both PDFs, extract with
            pdftotext, drop the reference lists, and count the words in shared runs of 10+ words.
            'real overlap' means 300+ such words. Resumable.
  report    leads.csv (copying side's region first: US, Europe, other Western, the rest) and counts.

Pair classes:
  same work        titles >= 0.82 similar, one title inside the other, different languages (a translation),
                   a preprint DOI on one side (preprint_doi_prefixes.tsv), or an erratum/editorial/paratext record
  shared author    an OpenAlex author id, or a surname + first initial, on both bylines
  metadata error   OpenAlex attached one work's abstract to another: one title has nothing to do with its
                   abstract while the other's fits, or near-identical abstracts on two works from one publisher
  same venue       one journal or book on both sides: usually a series/book description on every chapter
  book review      one side reviews a book, and quotes its description
  repository copy  one side is a repository, preprint server or index record of a work published elsewhere
  hub              a lead whose work is in --hub (5)+ lead/same-venue pairs: it carries stock text
  lead             everything else

Usage:
  python3 abstract_scan.py harvest  --dir scans/bioethics --filter "topics.id:T13147|T14307"
  python3 abstract_scan.py pairs    --dir scans/bioethics
  python3 abstract_scan.py fulltext --dir scans/bioethics [--limit 50]
  python3 abstract_scan.py report   --dir scans/bioethics

OPENALEX_API_KEY is read from the environment (free from openalex.org/settings/api; without one,
requests are anonymous and rate-limited sooner). Downloaded PDFs are untrusted input: they are only
passed to pdftotext, never opened any other way.
"""
import argparse
import csv
import difflib
import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("abstract_scan")
OPENALEX = "https://api.openalex.org/works"
USER_AGENT = "abstract-copy-scan/1.0"
SELECT = ("id,doi,title,publication_year,type,primary_location,authorships,best_oa_location,locations,"
          "is_retracted,abstract_inverted_index,primary_topic,language")
SHINGLE = 8          # candidate generation: 8-word shingles ...
SAMPLE = 4           # ... 1 in 4 kept, by a fixed hash. A 20-word run has 13 shingles, so a miss is ~2%.
TITLE_SAME = 0.82
FT_RUN = 10          # full-text runs: 10+ identical words ...
FT_GAP = 3           # ... merged across up to 3 differing words
FT_REAL = 300        # 'real overlap' at 300+ words of the copying side in shared runs
NOT_A_PAPER = {"erratum", "retraction", "paratext", "editorial", "letter", "peer-review", "other"}
EUROPE = frozenset("""AD AL AT BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GI GR HR HU IE IM IS IT LI LT LU LV
                      MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK""".split())
OTHER_WEST = {"CA", "JP", "AU", "NZ"}


# ---------------------------------------------------------------------------------------------- storage

def connect(d):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(d / "scan.sqlite3")
    db.executescript("""
    create table if not exists works (
        id text primary key, doi text, title text, year integer, type text, venue text, issn text,
        publisher text, authors text, oa_pdf text, oa_landing text, is_retracted integer, topic text,
        abstract text, words integer, language text);
    create table if not exists harvest_state (filter text primary key, cursor text, done integer, n integer);
    create table if not exists pairs (
        a text, b text, longest integer, cont5 real, words_a integer, words_b integer, title_sim real,
        cls text, why text, later text, later_countries text, primary key (a, b));
    create table if not exists fulltext (
        a text, b text, got text, pct_a real, pct_b real, matched integer, longest integer, runs integer,
        verdict text, longest_text text, checked text, primary key (a, b));
    """)
    return db


def words(text):
    return (text or "").lower().split()


# ---------------------------------------------------------------------------------------------- harvest

def http_get(url, params=None, timeout=120, accept=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **({"Accept": accept} if accept else {})})
    return urllib.request.urlopen(req, timeout=timeout)


def openalex(params):
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params = {**params, "api_key": key}
    for attempt in range(6):
        try:
            with http_get(OPENALEX, params) as r:
                return json.load(r), r.headers
        except urllib.error.HTTPError as e:
            if e.code == 429:
                sys.exit(f"429 from OpenAlex (Retry-After {e.headers.get('Retry-After')}); "
                         "stopping. Re-run the same command later to resume.")
            log.warning("OpenAlex HTTP %s, retrying", e.code)
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("OpenAlex: %s, retrying", e)
        time.sleep(5 * (attempt + 1))
    sys.exit("OpenAlex kept failing")


def abstract_text(inv):
    if not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs:
            pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))


def work_row(w):
    pl = w.get("primary_location") or {}
    src = pl.get("source") or {}
    oa = w.get("best_oa_location") or {}
    pdf, landing = oa.get("pdf_url"), oa.get("landing_page_url")
    if not pdf:   # another OA location may have a direct PDF
        for loc in w.get("locations") or []:
            if loc.get("is_oa") and loc.get("pdf_url"):
                pdf = loc["pdf_url"]
                break
    authors = []
    for a in w.get("authorships") or []:
        au = a.get("author") or {}
        authors.append([au.get("id"), au.get("display_name"),
                        sorted({c for c in (a.get("countries") or []) if c}),
                        [i.get("display_name") for i in a.get("institutions") or [] if i.get("display_name")]])
    ab = abstract_text(w.get("abstract_inverted_index"))
    return (w["id"].rsplit("/", 1)[-1], (w.get("doi") or "").replace("https://doi.org/", "").lower() or None,
            w.get("title"), w.get("publication_year"), w.get("type"), src.get("display_name"),
            src.get("issn_l"), src.get("host_organization_name"), json.dumps(authors), pdf, landing,
            int(bool(w.get("is_retracted"))), (w.get("primary_topic") or {}).get("display_name"),
            ab, len(words(ab)), w.get("language"))


def harvest(db, args):
    for flt in args.filter:
        full = flt + ",has_abstract:true"
        row = db.execute("select cursor, done, n from harvest_state where filter = ?", (full,)).fetchone()
        if row and row[1]:
            log.info("%s: already harvested (%d works)", full, row[2])
            continue
        cursor, n = (row[0], row[2]) if row else ("*", 0)
        pages = 0
        while cursor:
            j, headers = openalex({"filter": full, "per_page": 200, "cursor": cursor, "select": SELECT})
            if pages == 0:
                log.info("%s: %d works match", full, j["meta"]["count"])
            rows = [work_row(w) for w in j["results"]]
            db.executemany("insert or replace into works values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
            pages += 1
            cursor = j["meta"].get("next_cursor") if rows else None
            db.execute("insert or replace into harvest_state values (?,?,?,?)", (full, cursor, int(not cursor), n))
            db.commit()
            if n % 10000 < 200:
                log.info("%s: %d / %d (USD remaining today: %s)", full, n, j["meta"]["count"],
                         headers.get("X-RateLimit-Remaining-USD"))
            if args.max_pages and pages >= args.max_pages:
                log.info("%s: stopped after --max-pages %d (resumable)", full, pages)
                break
        log.info("%s: %d works so far", full, n)
    log.info("works in db: %d", db.execute("select count(*) from works").fetchone()[0])


# ---------------------------------------------------------------------------------------------- classify

def plain(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def norm_title(t):
    return " ".join(re.findall(r"[a-z0-9]+", plain(t)))


def name_keys(authors):
    """'surname f' for each author; a 'Last, First' name (repository records) is flipped first."""
    keys = set()
    for _, name, _, _ in authors:
        name = plain(name)
        if "," in name:
            last, first = name.split(",", 1)
            name = first + " " + last
        parts = re.findall(r"[a-z]+", name)
        if len(parts) >= 2:
            keys.add(parts[-1] + " " + parts[0][0])
    return keys


def publisher_key(p):
    """First significant word of a publisher name: 'BMJ' == 'BMJ Publishing Group', 'Wiley' == 'Wiley-Blackwell'."""
    ws = [w for w in re.findall(r"[a-z]+", plain(p)) if w not in ("the", "of")]
    return ws[0] if ws else None


def preprint_rules():
    """{prefix: compiled suffix regex or None (= every DOI under the prefix)}."""
    rules = {}
    with open(HERE / "preprint_doi_prefixes.tsv", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rx = (r.get("suffix_regex") or "").strip()
            rules[r["prefix"].strip()] = re.compile(rx, re.I) if rx else None
    return rules


def is_preprint(doi, rules):
    if not doi or "/" not in doi:
        return False
    prefix, suffix = doi.split("/", 1)
    if prefix not in rules:
        return False
    rx = rules[prefix]
    return rx is None or bool(rx.match(suffix))


REPOSITORY = re.compile(r"repositor|archive|ssrn|arxiv|zenodo|researchgate|digital ?commons|scholarworks|"
                        r"dspace|eprints|digitalgeorgetown|pubmed|europe pmc|philsci|hal\b|riunb|dialnet|"
                        r"\bdoaj\b|redalyc|la referencia|irdb|figshare|osf|preprints", re.I)
# "Name. Title. Publisher, 2014", "Title, by X", "(ed.)", "(Hg.)", "Name (2020): Title", "Review of ..."
BOOK_REVIEW = re.compile(r"(?i:\((?:eds?|hg|hrsg|dir)\.?\))|\b[Bb]y [A-Z][a-z]+|^[A-Z][^:.]{2,40}[.,] ?\(?(?:19|20)\d\d\)?[:.]|"
                         r"(?i:\b(?:press|verlag|éditions|editions|publishing|publishers),? (?:19|20)\d\d)|"
                         r"(?i:^review of\b|^book review)")
# Publisher stock sentences that ride along in OpenAlex abstracts, removed before matching.
STOCK = re.compile(r"supplemental digital content|direct url citations|declares? no (?:competing|conflicts?)|"
                   r"no conflicts? of interest|protected by copyright|all rights reserved|copyright ©|©|"
                   r"this is an open access article|creative commons", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+")
STOP = set("""the and for with from into this that their between among within about after under over towards toward
              what when where which while does should could would study case analysis review approach""".split())


def strip_stock(text):
    return " ".join(x for x in SENTENCE.split(text or "") if not STOCK.search(x))


def title_in_abstract(title, abstract):
    """Share of the title's content words (4+ letters, not stopwords) found in the work's own abstract."""
    tw = {w for w in re.findall(r"[a-z]{4,}", plain(title)) if w not in STOP}
    if len(tw) < 2:
        return None
    aw = set(re.findall(r"[a-z]{4,}", plain(abstract)))
    stems = {w[:6] for w in aw}
    return sum(w in aw or w[:6] in stems for w in tw) / len(tw)


def classify(wa, wb, title_sim, preprints, cont):
    """(class, why). 'hub' is assigned afterwards, from how many pairs a work is in."""
    if title_sim >= TITLE_SAME:
        return "same work", f"titles {title_sim:.2f} similar"
    ta, tb = norm_title(wa["title"]), norm_title(wb["title"])
    short, long_ = sorted((ta, tb), key=len)
    if len(short.split()) >= 4 and short in long_:
        return "same work", "one title contains the other"
    if wa.get("language") and wb.get("language") and wa["language"] != wb["language"]:
        return "same work", f"translation? ({wa['language']} / {wb['language']})"
    for w in (wa, wb):
        if is_preprint(w["doi"], preprints):
            return "same work", f"preprint DOI {w['doi']}"
        if w["type"] in NOT_A_PAPER:
            return "same work", f"{w['type']} record"
    if {a[0] for a in wa["authors"] if a[0]} & {a[0] for a in wb["authors"] if a[0]}:
        return "shared author", "same OpenAlex author"
    common = name_keys(wa["authors"]) & name_keys(wb["authors"])
    if common:
        return "shared author", "same name: " + ", ".join(sorted(common))
    # An abstract OpenAlex attached to the wrong work: one title has nothing to do with the abstract it carries
    # while the other's fits. A retitled copy still has a title about its own abstract, so it isn't caught here.
    fa, fb = title_in_abstract(wa["title"], wa["abstract"]), title_in_abstract(wb["title"], wb["abstract"])
    if fa is not None and fb is not None and min(fa, fb) <= 0.15 and max(fa, fb) >= 0.5:
        return "metadata error", f"title fits its abstract {fa:.2f} / {fb:.2f}"
    pk = publisher_key(wa["publisher"])
    if cont >= 0.9 and pk and pk == publisher_key(wb["publisher"]):
        return "metadata error", f"same abstract on two {wa['publisher']} works"
    if wa["venue"] and wa["venue"] == wb["venue"]:
        return "same venue", wa["venue"]
    for w in (wa, wb):
        if w["type"] == "book-review" or BOOK_REVIEW.search(w["title"] or ""):
            return "book review", (w["title"] or "")[:80]
    for w in (wa, wb):
        if w["venue"] and REPOSITORY.search(w["venue"]):
            return "repository copy", w["venue"]
    if not wa["authors"] or not wb["authors"]:
        return "lead", "a byline is missing: shared author unknown"
    return "lead", ""


# ---------------------------------------------------------------------------------------------- pairs

def candidate_pairs(texts, max_postings):
    """Pairs (i, j) sharing at least one sampled 8-word shingle, from a sorted (hash, doc) array."""
    import numpy as np
    hs, ds = [], []
    for i, t in enumerate(texts):
        w = words(t)
        seen = set()
        for k in range(len(w) - SHINGLE + 1):
            h = int.from_bytes(hashlib.blake2b(" ".join(w[k:k + SHINGLE]).encode(), digest_size=8).digest(),
                               "little", signed=True)
            if h % SAMPLE == 0 and h not in seen:
                seen.add(h)
                hs.append(h)
                ds.append(i)
    h = np.array(hs, dtype=np.int64)
    d = np.array(ds, dtype=np.int32)
    del hs, ds
    order = np.argsort(h, kind="stable")
    h, d = h[order], d[order]
    bounds = np.flatnonzero(np.diff(h)) + 1
    starts = np.concatenate(([0], bounds))
    ends = np.concatenate((bounds, [len(h)]))
    sizes = ends - starts
    pairs, skipped = Counter(), 0
    for s, e, n in zip(starts, ends, sizes):
        if n < 2:
            continue
        if n > max_postings:      # stock phrasing shared by many abstracts
            skipped += 1
            continue
        docs = sorted(set(d[s:e].tolist()))
        for x in range(len(docs)):
            for y in range(x + 1, len(docs)):
                pairs[(docs[x], docs[y])] += 1
    log.info("%d sampled shingles, %d shared, %d skipped as stock phrasing (> %d abstracts); %d candidate pairs",
             len(h), int((sizes >= 2).sum()), skipped, max_postings, len(pairs))
    return pairs


def load_works(db, ids=None, abstracts=False):
    q = ("select id, doi, title, year, type, venue, publisher, authors, oa_pdf, oa_landing, is_retracted, language"
         + (", abstract" if abstracts else "") + " from works")
    out = {}
    for r in db.execute(q):
        if ids is not None and r[0] not in ids:
            continue
        out[r[0]] = {"id": r[0], "doi": r[1], "title": r[2], "year": r[3], "type": r[4], "venue": r[5],
                     "publisher": r[6], "authors": json.loads(r[7]), "oa_pdf": r[8], "oa_landing": r[9],
                     "retracted": r[10], "language": r[11], "abstract": r[12] if abstracts else None}
    return out


def pairs_cmd(db, args):
    rows = db.execute("select id, abstract from works where words >= ?", (args.min_words,)).fetchall()
    ids = [r[0] for r in rows]
    texts = [strip_stock(r[1]) for r in rows]
    log.info("%d abstracts of >= %d words", len(ids), args.min_words)
    cands = candidate_pairs(texts, args.max_postings)
    hits = []
    for n, (i, j) in enumerate(cands, 1):
        wa, wb = words(texts[i]), words(texts[j])
        run = difflib.SequenceMatcher(None, wa, wb, autojunk=False).find_longest_match(0, len(wa), 0, len(wb)).size
        if run >= args.min_run:
            hits.append((i, j, run, wa, wb))
        if n % 200000 == 0:
            log.info("measured %d / %d candidates, %d hits", n, len(cands), len(hits))
    works = load_works(db, {ids[i] for i, *_ in hits} | {ids[j] for _, j, *_ in hits}, abstracts=True)
    preprints = preprint_rules()
    out = []
    for i, j, run, wa, wb in hits:
        a, b = ids[i], ids[j]
        A, B = works[a], works[b]
        ga = {tuple(wa[k:k + 5]) for k in range(len(wa) - 4)}
        gb = {tuple(wb[k:k + 5]) for k in range(len(wb) - 4)}
        cont = len(ga & gb) / max(min(len(ga), len(gb)), 1)    # 5-gram containment of the shorter abstract
        ts = difflib.SequenceMatcher(None, norm_title(A["title"]), norm_title(B["title"])).ratio()
        cls, why = classify(A, B, ts, preprints, cont)
        later = None
        if A["year"] and B["year"] and A["year"] != B["year"]:
            later = a if A["year"] > B["year"] else b
        lc = " ".join(sorted({c for au in works[later]["authors"] for c in au[2]})) if later else ""
        out.append([a, b, run, round(cont, 3), len(wa), len(wb), round(ts, 3), cls, why, later, lc])
    # A work whose abstract matches many others' is carrying stock text (a declaration, a series blurb, a
    # publisher's standard abstract), not its own.
    deg = Counter()
    for r in out:
        if r[7] in ("lead", "same venue"):
            deg[r[0]] += 1
            deg[r[1]] += 1
    for r in out:
        k = max(deg[r[0]], deg[r[1]])
        if r[7] == "lead" and k >= args.hub:
            r[7], r[8] = "hub", f"abstract matches {k} others"
    db.execute("delete from pairs")
    db.executemany("insert into pairs values (?,?,?,?,?,?,?,?,?,?,?)", out)
    db.commit()
    log.info("%d pairs share a %d+ word run: %s", len(out), args.min_run, dict(Counter(r[7] for r in out)))


# ---------------------------------------------------------------------------------------------- full text

META = re.compile(rb"<meta\b[^>]*>", re.I)
META_NAME = re.compile(rb"""name\s*=\s*["']?citation_pdf_url""", re.I)
META_CONTENT = re.compile(rb"""content\s*=\s*["']([^"']+)["']""", re.I)


def download(url, dest, depth=0):
    """Save url to dest if it is a PDF (by magic bytes). An HTML landing page is searched once for a
    citation_pdf_url meta tag. Returns True on success."""
    try:
        with http_get(url, timeout=60, accept="application/pdf,text/html;q=0.9,*/*;q=0.5") as r:
            head = r.read(1024)
            if head.lstrip()[:4] == b"%PDF":
                tmp = dest.with_suffix(".part")
                with open(tmp, "wb") as f:
                    f.write(head)
                    while chunk := r.read(65536):
                        f.write(chunk)
                tmp.rename(dest)
                return True
            if depth:
                return False
            page = head + r.read(2_000_000)
            base = r.geturl()
    except Exception as e:  # dead links, bot blocks and timeouts are ordinary outcomes here
        log.debug("%s: %s", url, e)
        return False
    for tag in META.findall(page):
        if META_NAME.search(tag) and (m := META_CONTENT.search(tag)):
            pdf = urllib.parse.urljoin(base, html.unescape(m.group(1).decode("utf-8", "replace")))
            return download(pdf, dest, depth=1)
    return False


def fetch_pdf(w, dest):
    if dest.exists():
        return True
    for url in (w["oa_pdf"], w["oa_landing"]):
        if url and download(url, dest):
            return True
        time.sleep(1)
    return False


REFS = re.compile(r"^\s*(references|bibliography|works cited|literature cited|references and notes)\s*$", re.I | re.M)


def body_words(path):
    """Lower-cased words of the PDF's text, the reference list cut off at the last References heading."""
    out = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True, text=True, timeout=120)
    text = out.stdout if out.returncode == 0 else ""
    heads = list(REFS.finditer(text))
    if heads and heads[-1].start() > len(text) * 0.5:
        text = text[:heads[-1].start()]
    text = re.sub(r"-\s*\n\s*(?=[a-z])", "", text)
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.lower())


def shared_runs(a, b, n=FT_RUN, gap=FT_GAP):
    """Runs of n+ identical words, merged across <= gap differing words in both documents:
    [(start_a, start_b, len_a, len_b)]. The same measurement as shared_runs.py."""
    index = {}
    for i in range(len(a) - n + 1):
        index.setdefault(tuple(a[i:i + n]), []).append(i)
    runs = []
    for j in range(len(b) - n + 1):
        for i in index.get(tuple(b[j:j + n]), ()):
            if i > 0 and j > 0 and a[i - 1] == b[j - 1]:
                continue                                  # inside a run already found
            k = n
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            runs.append((i, j, k))
    out = []
    for i, j, k in sorted(runs):
        if out:
            si, sj, la, lb = out[-1]
            da, db_ = i - (si + la), j - (sj + lb)
            if 0 <= da <= gap and 0 <= db_ <= gap:
                out[-1] = (si, sj, i + k - si, j + k - sj)
                continue
        out.append((i, j, k, k))
    return out


def fulltext(db, args):
    pdir = Path(args.dir) / "pdfs"
    pdir.mkdir(exist_ok=True)
    rows = db.execute("""select p.a, p.b, p.later, p.later_countries, p.longest from pairs p
                         left join fulltext f on f.a = p.a and f.b = p.b
                         where p.cls = 'lead' and f.a is null""").fetchall()
    works = load_works(db, {r[0] for r in rows} | {r[1] for r in rows})
    rows = [r for r in rows if all(works[x]["oa_pdf"] or works[x]["oa_landing"] for x in r[:2])]
    rows.sort(key=lambda r: (country_tier(r[3])[0], -r[4]))
    if args.limit:
        rows = rows[:args.limit]
    log.info("%d lead pairs with an open-access location on both sides to check", len(rows))
    for n, (a, b, later, _, _) in enumerate(rows, 1):
        got = "".join(x if fetch_pdf(works[y], pdir / f"{y}.pdf") else "-" for x, y in (("A", a), ("B", b)))
        pct_a = pct_b = matched = longest = nruns = None
        snippet, verdict = "", "not retrieved"
        if got == "AB" and (pdir / f"{a}.pdf").read_bytes() == (pdir / f"{b}.pdf").read_bytes():
            verdict = "same pdf"     # one file served for both works: retrieval, not copying
        elif got == "AB":
            wa, wb = body_words(pdir / f"{a}.pdf"), body_words(pdir / f"{b}.pdf")
            if len(wa) < 300 or len(wb) < 300:
                verdict = "no text"
            else:
                runs = shared_runs(wa, wb)
                ca, cb = set(), set()
                for i, j, la, lb in runs:
                    ca.update(range(i, i + la))
                    cb.update(range(j, j + lb))
                pct_a, pct_b = round(100 * len(ca) / len(wa), 1), round(100 * len(cb) / len(wb), 1)
                matched = len(cb) if later == b else len(ca)     # words of the copying side
                longest, nruns = max((r[2] for r in runs), default=0), len(runs)
                if runs:
                    i, _, la, _ = max(runs, key=lambda r: r[2])
                    snippet = " ".join(wa[i:i + min(la, 50)])
                verdict = "real overlap" if matched >= FT_REAL else "no real overlap"
        db.execute("insert or replace into fulltext values (?,?,?,?,?,?,?,?,?,?,?)",
                   (a, b, got, pct_a, pct_b, matched, longest, nruns, verdict, snippet,
                    datetime.now().isoformat(timespec="seconds")))
        db.commit()
        log.info("[%d/%d] %s %s %s %s %s", n, len(rows), a, b, got, verdict,
                 f"{pct_a}% / {pct_b}%" if pct_a is not None else "")


# ---------------------------------------------------------------------------------------------- report

def country_tier(countries):
    cs = set((countries or "").split())
    if "US" in cs:
        return 0, "US"
    if cs & EUROPE:
        return 1, "Europe"
    if cs & OTHER_WEST:
        return 2, "other Western"
    if cs:
        return 3, "non-Western"
    return 4, "no country"


def report(db, args):
    works = load_works(db)
    q = """select p.*, f.got, f.pct_a, f.pct_b, f.matched, f.verdict from pairs p
           left join fulltext f on f.a = p.a and f.b = p.b"""
    cols = [c[0] for c in db.execute(q).description]
    rows = [dict(zip(cols, r)) for r in db.execute(q)]
    leads = [r for r in rows if r["cls"] == "lead"]
    for r in leads:
        r["tier"], r["region"] = country_tier(r["later_countries"])
    leads.sort(key=lambda r: (r["verdict"] != "real overlap", r["tier"], -(r["longest"] or 0)))
    out = Path(args.dir) / "leads.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["later_region", "later_countries", "abstract_longest_run", "abstract_cont5", "fulltext",
                    "ft_pct_later", "ft_pct_earlier", "ft_words", "no_oa_copy", "why",
                    "year_a", "doi_a", "title_a", "venue_a", "authors_a",
                    "year_b", "doi_b", "title_b", "venue_b", "authors_b", "openalex_a", "openalex_b"])
        for r in leads:
            A, B = works[r["a"]], works[r["b"]]
            need = " ".join(x["doi"] or x["id"] for x in (A, B) if not (x["oa_pdf"] or x["oa_landing"]))
            pl, pe = (r["pct_a"], r["pct_b"]) if r["later"] == r["a"] else (r["pct_b"], r["pct_a"])
            w.writerow([r["region"] if r["later"] else "order unknown", r["later_countries"], r["longest"],
                        r["cont5"], r["verdict"] or "", pl, pe, r["matched"], need, r["why"],
                        A["year"], A["doi"], A["title"], A["venue"], "; ".join(a[1] or "" for a in A["authors"]),
                        B["year"], B["doi"], B["title"], B["venue"], "; ".join(a[1] or "" for a in B["authors"]),
                        r["a"], r["b"]])
    print(f"works: {len(works)}   pairs with a shared {args.min_run}+ word abstract run: {len(rows)}")
    print("by class:", dict(Counter(r["cls"] for r in rows).most_common()))
    print("leads by copying (later) side:",
          dict(Counter(r["region"] if r["later"] else "order unknown" for r in leads).most_common()))
    print("leads, full text:", dict(Counter(r["verdict"] or "unchecked" for r in leads).most_common()))
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["harvest", "pairs", "fulltext", "report"])
    ap.add_argument("--dir", required=True, help="working folder for this field (database, PDFs, leads.csv)")
    ap.add_argument("--filter", action="append", default=[], help="OpenAlex works filter (harvest; repeatable)")
    ap.add_argument("--max-pages", type=int, help="harvest: stop each filter after this many 200-work pages")
    ap.add_argument("--min-words", type=int, default=30, help="pairs: ignore abstracts shorter than this")
    ap.add_argument("--min-run", type=int, default=20, help="pairs: shared abstract run length, in words")
    ap.add_argument("--max-postings", type=int, default=50, help="pairs: skip shingles in more abstracts than this")
    ap.add_argument("--hub", type=int, default=5, help="pairs: a work in this many lead/same-venue pairs is a hub")
    ap.add_argument("--limit", type=int, help="fulltext: check at most this many pairs this run")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    db = connect(args.dir)
    if args.cmd == "harvest":
        if not args.filter:
            ap.error("harvest needs --filter")
        harvest(db, args)
    elif args.cmd == "pairs":
        pairs_cmd(db, args)
    elif args.cmd == "fulltext":
        fulltext(db, args)
    else:
        report(db, args)


if __name__ == "__main__":
    main()
