#!/usr/bin/env python3
"""Metadata-level audit of a publisher family's whole catalogue, for two questions the
text pipeline can't answer cheaply:

  1. **Is the same work being republished?** Cluster every work under a DOI prefix by
     normalized title (exact after normalization, plus a sorted-token key that catches
     reordered/lightly-reworded titles), and report clusters spanning more than one DOI.
     A cluster whose members share an author is self-republication; one whose members
     have disjoint author sets is the stolen/resold-manuscript pattern this project's
     flagged cases document.
  2. **Who is publishing there?** A paper mill running up a backlog under invented names
     looks very different from one selling slots to real academics. For each author,
     count their works inside these prefixes vs. anywhere else, and whether they carry an
     ORCID or a recorded institution (--enrich, via OpenAlex).

Metadata only: Crossref's `filter=prefix:...` cursor-paginated, no PDFs, no embeddings.
That's the point -- the whole catalogue of every family here is ~38k works, far more than
is worth downloading, and both questions above are answerable from titles and bylines.
bulk_retrieve_crossref.py --doi-prefix --whole-prefix is the complement when the actual
text is needed (it reuses state.sqlite3 and the normal pipeline); this script never
touches either database.

    python3 mill_prefix_audit.py harvest --email you@example.com --prefix 10.34218 --prefix 10.63282
    python3 mill_prefix_audit.py report --min-cluster 2
    python3 mill_prefix_audit.py enrich --email you@example.com --min-papers 3   # OpenAlex author lookup
    python3 mill_prefix_audit.py verify --library-db computer-ethics/library.sqlite3  # text-check the clusters
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import requests

import retrieve_papers as rp

CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_AUTHORS_API = "https://api.openalex.org/authors"
ROWS_PER_PAGE = 1000  # Crossref's documented cursor-paging maximum
STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "and", "or", "to", "with", "using",
              "based", "study", "case", "approach", "review", "analysis", "towards", "toward"}
MIN_TITLE_WORDS = 4  # shorter titles ("Front Cover") cluster meaninglessly -- see find_duplicate_papers.py


def normalize_title(title):
    text = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def token_key(normalized):
    """Sorted significant words -- collapses reordered/reworded-but-same-content titles
    ("AI in Education: Educators' Perspectives" vs "Educators' Perspectives on AI in
    Education") that an exact normalized match misses."""
    words = sorted(set(normalized.split()) - STOPWORDS)
    return " ".join(words)


# Several of these publishers deposit an affiliation string into Crossref's author field
# ("Samarkand State Medical University, Uzbekistan" as the author of 47 papers). Not a person.
INSTITUTION_RE = re.compile(r"universit|institut|college|academy|department|faculty|hospital|"
                             r"laborator|research cent|independent researcher|school of|ministry", re.I)


def is_institution(name_key):
    return bool(INSTITUTION_RE.search(name_key)) or len(name_key.split()) > 6


# Honorifics and degrees are deposited inconsistently ("Dr Sheshang Degadwala" and "Sheshang
# Degadwala" are one person filing 135 papers, not two filing 90 and 45), and they also break an
# exact-name lookup against OpenAlex, which never carries them.
HONORIFIC_RE = re.compile(r"^(dr|prof|professor|mr|mrs|ms|miss|sir|assoc|asst|er|adv|advocate|"
                           r"md|phd|engr)\b\s*", re.I)
DEGREE_RE = re.compile(r"\s*\b(phd|ph d|md|msc|m sc|bsc|b sc|mba|llm|ll m|llb|ll b|mtech|m tech|"
                        r"btech|b tech|mphil|m phil|dsc|d sc)\b\s*$", re.I)


def normalize_author(name):
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    text = " ".join(re.sub(r"[^a-z ]+", " ", text).split())
    while True:
        stripped = DEGREE_RE.sub("", HONORIFIC_RE.sub("", text)).strip()
        if stripped == text:
            return text
        text = stripped


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS works (
            doi TEXT PRIMARY KEY, prefix TEXT NOT NULL, title TEXT, container TEXT,
            published TEXT, authors TEXT, norm_title TEXT, token_key TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_works_norm ON works(norm_title);
        CREATE INDEX IF NOT EXISTS idx_works_token ON works(token_key);
        CREATE TABLE IF NOT EXISTS authors_openalex (
            name_key TEXT PRIMARY KEY, display_name TEXT, openalex_id TEXT, works_count INTEGER,
            cited_by_count INTEGER, orcid TEXT, institution TEXT, checked_at TEXT
        );
        """
    )
    conn.commit()


def crossref_works(session, limiter, prefix, args):
    cursor, seen = "*", 0
    while cursor:
        params = {"filter": f"prefix:{prefix}", "rows": ROWS_PER_PAGE, "cursor": cursor,
                   "select": "DOI,title,container-title,author,published", "mailto": args.email}
        resp = rp.http_get(session, CROSSREF_API, params, rate_limiter=limiter,
                            max_retries=args.max_retries, timeout=args.timeout, logger=LOGGER)
        if resp.status_code != 200:
            LOGGER.warning("crossref HTTP %d for prefix %s, stopping", resp.status_code, prefix)
            return
        message = resp.json()["message"]
        items = message.get("items", [])
        if not items:
            return
        for item in items:
            yield item
        seen += len(items)
        LOGGER.info("  %s: %d works fetched", prefix, seen)
        cursor = message.get("next-cursor")


def harvest(conn, args):
    session = requests.Session()
    session.headers["User-Agent"] = rp.USER_AGENT_TEMPLATE.format(email=args.email)
    limiter = rp.RateLimiter(args.min_interval)
    for prefix in args.prefixes:
        rows = []
        for item in crossref_works(session, limiter, prefix, args):
            title = (item.get("title") or [""])[0]
            norm = normalize_title(title)
            authors = [" ".join(filter(None, (a.get("given"), a.get("family")))) or a.get("name") or ""
                       for a in (item.get("author") or [])]
            published = "-".join(str(p) for p in
                                 ((item.get("published") or {}).get("date-parts") or [[None]])[0] if p)
            rows.append((item["DOI"].lower(), prefix, title, (item.get("container-title") or [""])[0],
                          published, json.dumps([a for a in authors if a]), norm, token_key(norm)))
            if len(rows) >= 2000:
                _insert(conn, rows)
                rows = []
        _insert(conn, rows)
        LOGGER.info("%s: done", prefix)


def _insert(conn, rows):
    conn.executemany("INSERT OR REPLACE INTO works VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()


def author_counts(conn):
    """{normalized author name: works in these prefixes}, institutions excluded."""
    counts = Counter()
    for (authors,) in conn.execute("SELECT authors FROM works"):
        for name in {normalize_author(a) for a in json.loads(authors) if a}:
            if name and not is_institution(name):
                counts[name] += 1
    return counts


def title_clusters(conn, min_cluster, key_column):
    """{key: [work rows]} for every title key shared by >= min_cluster distinct DOIs."""
    clusters = defaultdict(list)
    for doi, prefix, title, container, published, authors, norm, tkey in conn.execute(
            "SELECT doi, prefix, title, container, published, authors, norm_title, token_key FROM works"):
        key = norm if key_column == "norm_title" else tkey
        if key and len(key.split()) >= MIN_TITLE_WORDS:
            clusters[key].append({"doi": doi, "prefix": prefix, "title": title, "container": container,
                                   "published": published, "authors": json.loads(authors)})
    return {k: v for k, v in clusters.items() if len(v) >= min_cluster}


def classify_cluster(works):
    """'same-author' (every member shares at least one author with the first), 'mixed', or
    'different-author' (no author appears in more than one member)."""
    sets = [{normalize_author(a) for a in w["authors"] if a} for w in works]
    if any(not s for s in sets):
        return "unknown-authors"
    shared_with_first = [bool(s & sets[0]) for s in sets[1:]]
    if all(shared_with_first):
        return "same-author"
    if any(shared_with_first) or any(a & b for i, a in enumerate(sets) for b in sets[i + 1:]):
        return "mixed"
    return "different-author"


def report(conn, args):
    total = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    by_prefix = dict(conn.execute("SELECT prefix, COUNT(*) FROM works GROUP BY prefix"))
    print(f"{total} works across {len(by_prefix)} prefix(es): "
          + ", ".join(f"{p}={n}" for p, n in sorted(by_prefix.items(), key=lambda kv: -kv[1])))

    for label, column in (("identical title (after normalization)", "norm_title"),
                          ("same significant words, any order", "token_key")):
        clusters = title_clusters(conn, args.min_cluster, column)
        kinds = Counter(classify_cluster(w) for w in clusters.values())
        n_works = sum(len(w) for w in clusters.values())
        print(f"\n== {label}: {len(clusters)} cluster(s) covering {n_works} works")
        for kind, n in kinds.most_common():
            print(f"   {kind}: {n} cluster(s)")
        ranked = sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for key, works in ranked[:args.show]:
            kind = classify_cluster(works)
            print(f"\n   [{kind}] {len(works)} works -- {works[0]['title'][:90]}")
            for w in sorted(works, key=lambda w: w["published"]):
                print(f"      {w['published'] or '?':10} {w['doi']:45} {', '.join(w['authors'])[:60]}")
                print(f"      {'':10} {w['container'][:70]}")

    print("\n== authors by number of works in these prefixes")
    counts = author_counts(conn)
    print(f"   {len(counts)} distinct author names (institution strings deposited in the author "
          f"field excluded); "
          f"{sum(1 for n in counts.values() if n == 1)} appear once, "
          f"{sum(1 for n in counts.values() if n >= 3)} appear 3+ times, "
          f"{sum(1 for n in counts.values() if n >= 10)} appear 10+ times")
    enriched = {row[0]: row for row in conn.execute("SELECT * FROM authors_openalex")}
    for name, n in counts.most_common(args.show):
        row = enriched.get(name)
        extra = ""
        if row:
            _, display, _, works_count, cited, orcid, inst, _ = row
            outside = (works_count or 0) - n
            extra = (f" | OpenAlex: {works_count} works ({outside} outside these prefixes), "
                     f"{cited} citations, ORCID {'yes' if orcid else 'no'}, {inst or 'no institution'}")
        print(f"   {n:4} {name}{extra}")


def enrich(conn, args):
    """Look up the most prolific author names in OpenAlex -- a real academic with a career
    outside these journals is a different finding than a name that exists only here."""
    counts = author_counts(conn)
    todo = [n for n, c in counts.items() if c >= args.min_papers
            and not conn.execute("SELECT 1 FROM authors_openalex WHERE name_key = ?", (n,)).fetchone()]
    LOGGER.info("%d author name(s) with >= %d works to look up", len(todo), args.min_papers)

    session = requests.Session()
    session.headers["User-Agent"] = rp.USER_AGENT_TEMPLATE.format(email=args.email)
    limiter = rp.RateLimiter(args.min_interval)
    api_key = os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    for i, name in enumerate(todo, 1):
        # 10, not 1: a common name ("Amit Kumar") can return a different person first, and
        # recording "no record" for them would overstate how many mill authors are untraceable.
        params = {"search": name, "per-page": 10, "mailto": args.email}
        if api_key:
            params["api_key"] = api_key
        try:
            resp = rp.http_get(session, OPENALEX_AUTHORS_API, params, rate_limiter=limiter,
                                max_retries=args.max_retries, timeout=args.timeout, logger=LOGGER)
            results = resp.json().get("results", []) if resp.status_code == 200 else []
        except (rp.RetrievalError, ValueError):
            results = []
        row = None
        for candidate in results:
            if normalize_author(candidate["display_name"]) == name:  # exact match only, never a near-name
                insts = candidate.get("last_known_institutions") or []
                row = (name, candidate["display_name"], candidate["id"], candidate["works_count"],
                        candidate["cited_by_count"], candidate.get("orcid"),
                        (insts[0].get("display_name") if insts else None), now)
                break
        conn.execute("INSERT OR REPLACE INTO authors_openalex VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      row or (name, None, None, None, None, None, None, now))
        if i % 25 == 0:
            conn.commit()
            LOGGER.info("  looked up %d/%d", i, len(todo))
    conn.commit()


def verify(conn, args):
    """Text-check every different-author title cluster against the corpus's extracted text.

    A shared title is only a lead -- two papers can carry the same generic title and share
    nothing. This resolves each cluster's DOIs to papers already extracted into library.sqlite3
    and runs compare_two_papers.py's exact word-shingle matcher across the two complete
    documents, so what comes out is ranked by actual verbatim overlap rather than by title.
    Pairs are reported, not judged: REVIEWING.md's checks (byline read from the PDF, content
    matching its own title, citation check) still have to happen before anything is a case."""
    import compare_two_papers as ctp
    lib = sqlite3.connect(args.library_db, timeout=120)
    ids = {}
    for pid, doi in lib.execute("SELECT id, doi FROM papers WHERE doi IS NOT NULL"):
        ids[doi.lower()] = pid

    seen_pairs, results = set(), []
    for column in ("norm_title", "token_key"):
        for key, works in title_clusters(conn, 2, column).items():
            if classify_cluster(works) != "different-author":
                continue
            resolved = [(w, ids[w["doi"]]) for w in works if w["doi"] in ids]
            for i, (work_a, pid_a) in enumerate(resolved):
                for work_b, pid_b in resolved[i + 1:]:
                    pair = tuple(sorted((pid_a, pid_b)))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    words_a = ctp.load_paper_words(lib, pid_a)
                    words_b = ctp.load_paper_words(lib, pid_b)
                    if len(words_a) < args.min_doc_words or len(words_b) < args.min_doc_words:
                        continue
                    runs = ctp.find_shingle_matches(words_a, words_b, shingle_size=args.shingle_size,
                                                     x_drop=args.x_drop)
                    if not runs:
                        continue
                    covered = set()
                    for r in runs:
                        covered.update(range(r[5], r[6]))
                    total = len(covered)
                    if total < args.min_words:
                        continue
                    results.append({"paper_a": pid_a, "paper_b": pid_b, "runs": len(runs),
                                     "matched_words": total, "longest": runs[0][0],
                                     "pct_of_shorter": 100 * total / min(len(words_a), len(words_b)),
                                     "work_a": work_a, "work_b": work_b})
            LOGGER.debug("cluster done: %s", key[:60])
    results.sort(key=lambda r: -r["matched_words"])
    LOGGER.info("%d pair(s) compared, %d with >= %d matched words", len(seen_pairs), len(results), args.min_words)
    for r in results[:args.show]:
        a, b = r["work_a"], r["work_b"]
        print(f"\n{r['matched_words']:6} matched words | {r['runs']:3} runs | longest {r['longest']:4} | "
              f"{r['pct_of_shorter']:.0f}% of shorter | ids {r['paper_a']}/{r['paper_b']}")
        print(f"   {a['title'][:88]}")
        for w in (a, b):
            print(f"     {w['published'] or '?':10} {w['doi']:44} {', '.join(w['authors'])[:44]:44} {w['container'][:34]}")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        LOGGER.info("wrote %s", args.out)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("harvest", "report", "enrich", "verify"))
    p.add_argument("--db", type=Path, default=Path("computer-ethics/mill_metadata.sqlite3"))
    p.add_argument("--prefix", dest="prefixes", action="append", default=None,
                    help="DOI prefix to harvest (repeatable); harvest only")
    p.add_argument("--email", default=None, help="contact address for Crossref/OpenAlex (harvest/enrich)")
    p.add_argument("--min-cluster", type=int, default=2, help="report: minimum works sharing a title key")
    p.add_argument("--show", type=int, default=25, help="report: how many clusters/authors to print")
    p.add_argument("--min-papers", type=int, default=3, help="enrich: look up authors with >= this many works")
    p.add_argument("--library-db", type=Path, default=Path("computer-ethics/library.sqlite3"),
                    help="verify: corpus database holding the extracted paragraph text")
    p.add_argument("--min-words", type=int, default=150, help="verify: minimum matched words to report a pair")
    p.add_argument("--min-doc-words", type=int, default=300, help="verify: skip papers with less text than this")
    p.add_argument("--shingle-size", type=int, default=10, help="verify: exact-match window, in words")
    p.add_argument("--x-drop", type=int, default=3, help="verify: tolerance for isolated substituted words")
    p.add_argument("--out", default=None, help="verify: also write full results as JSON here")
    p.add_argument("--min-interval", type=float, default=0.5)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args()
    if args.command in ("harvest", "enrich") and not args.email:
        p.error(f"--email is required for {args.command} (Crossref/OpenAlex polite-pool terms)")
    if args.command == "harvest" and not args.prefixes:
        p.error("--prefix is required for harvest")
    return args


def main():
    args = parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db, timeout=120)
    init_db(conn)
    {"harvest": harvest, "report": report, "enrich": enrich, "verify": verify}[args.command](conn, args)
    conn.close()


LOGGER = rp.logging.getLogger("mill_prefix_audit")
if __name__ == "__main__":
    rp.logging.basicConfig(level=rp.logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%H:%M:%S")
    sys.exit(main())
