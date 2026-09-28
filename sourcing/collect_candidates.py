#!/usr/bin/env python3
"""
Build a candidate computer-ethics corpus from OpenAlex search.

This program does NOT download papers. It:
  1. queries OpenAlex metadata/search for each line of queries.txt,
  2. scores candidates for relevance to computer ethics,
  3. writes every DOI-bearing work above --min-score to output/candidates.jsonl.

Feed that file to enrich_open_access.py, then build_oa_starting_list.py.

Python 3.10+
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import unquote

import requests

OPENALEX_WORKS = "https://api.openalex.org/works"
DEFAULT_CACHE_DIR = "output/openalex_cache"

# DOI syntax is intentionally permissive after the registrant prefix.
# We trim common punctuation afterwards.
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)

STRONG_PHRASES = [
    "computer ethics",
    "computing ethics",
    "information ethics",
    "digital ethics",
    "data ethics",
    "ai ethics",
    "artificial intelligence ethics",
    "machine learning ethics",
    "algorithmic ethics",
    "algorithmic fairness",
    "algorithmic bias",
    "responsible ai",
    "responsible artificial intelligence",
    "technology ethics",
    "software engineering ethics",
    "computer science ethics",
    "fairness accountability transparency",
    "ethical ai",
    "ethics of ai",
    "ethics of artificial intelligence",
    "ethics of algorithms",
    "ethics of computing",
]

ETHICS_TERMS = [
    "ethic", "moral", "fairness", "fair", "bias", "accountability",
    "responsib", "justice", "discrimination", "transparency",
    "privacy", "surveillance", "human rights", "values",
]

COMPUTING_TERMS = [
    "computer", "computing", "software", "algorithm", "artificial intelligence",
    " ai ", "machine learning", " ml ", "data", "digital", "internet", "online",
    "information technology", "information system", "automated decision",
    "automation", "platform", "social media", "cyber", "robot", "autonomous",
]

# Terms that often create false positives when used without computing context.
GENERIC_ONLY = [
    "medical ethics", "bioethics", "clinical ethics", "nursing ethics",
    "business ethics", "research ethics", "animal ethics",
]


def normalize_doi(value: str | None) -> Optional[str]:
    if not value:
        return None
    s = unquote(str(value)).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    m = DOI_RE.search(s)
    if not m:
        return None

    doi = m.group(0).lower()
    # Strip punctuation that is very commonly sentence/CSV decoration.
    doi = doi.rstrip(".,;:]>}\"'")
    # Be conservative with unmatched closing parens.
    while doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1]
    return doi or None


def reconstruct_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    positions = []
    for token, indexes in inv.items():
        for idx in indexes:
            positions.append((idx, token))
    positions.sort()
    return " ".join(token for _, token in positions)


def work_text(work: dict) -> str:
    parts = [
        work.get("title") or "",
        reconstruct_abstract(work.get("abstract_inverted_index")),
    ]
    for topic in work.get("topics") or []:
        parts.append(topic.get("display_name") or "")
        domain = topic.get("domain") or {}
        field = topic.get("field") or {}
        subfield = topic.get("subfield") or {}
        parts.extend(
            [
                domain.get("display_name") or "",
                field.get("display_name") or "",
                subfield.get("display_name") or "",
            ]
        )
    for kw in work.get("keywords") or []:
        parts.append(kw.get("display_name") or "")
    return " ".join(parts).lower()


def relevance_score(work: dict) -> tuple[int, list[str]]:
    """
    Heuristic score. It is deliberately inspectable rather than an opaque model.

    Typical interpretation:
      >= 6 : strong computer-ethics candidate
      4-5  : plausible candidate
      < 4  : usually too broad/noisy
    """
    text = f" {work_text(work)} "
    title = f" {(work.get('title') or '').lower()} "
    reasons: list[str] = []
    score = 0

    matched_strong = [p for p in STRONG_PHRASES if p in text]
    if matched_strong:
        score += min(6, 3 + len(set(matched_strong)))
        reasons.append("strong:" + "|".join(sorted(set(matched_strong))[:5]))

    ethics_hits = [x for x in ETHICS_TERMS if x in text]
    computing_hits = [x for x in COMPUTING_TERMS if x in text]

    if ethics_hits:
        score += min(3, len(set(ethics_hits)))
        reasons.append("ethics:" + "|".join(sorted(set(ethics_hits))[:5]))
    if computing_hits:
        score += min(3, len(set(computing_hits)))
        reasons.append("computing:" + "|".join(sorted(set(computing_hits))[:5]))

    # Title evidence is more valuable than abstract/topic-only evidence.
    if any(p in title for p in STRONG_PHRASES):
        score += 3
        reasons.append("strong-title")
    elif (
        any(x in title for x in ETHICS_TERMS)
        and any(x in title for x in COMPUTING_TERMS)
    ):
        score += 2
        reasons.append("ethics+computing-title")

    # Penalize obvious unrelated ethics domains when no strong computing phrase exists.
    if not matched_strong and any(x in text for x in GENERIC_ONLY):
        score -= 2
        reasons.append("generic-ethics-penalty")

    return score, reasons


def api_get(session: requests.Session, params: dict, retries: int = 6) -> dict:
    """Retries both HTTP-level failures (429/5xx) and connection-level ones
    (dropped/corrupted connection mid-transfer, DNS blip, etc.) -- confirmed
    for real: a `ChunkedEncodingError` from a broken connection crashed two
    multi-hour collect runs outright, since it's raised by `session.get()`
    itself and never reaches the status-code check below at all."""
    delay = 1.0
    for attempt in range(retries):
        try:
            response = session.get(OPENALEX_WORKS, params=params, timeout=60)
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(delay + random.random() * 0.25)
            delay = min(delay * 2, 30)
            continue
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt == retries - 1:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            time.sleep(delay + random.random() * 0.25)
            delay = min(delay * 2, 30)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("unreachable")


def _cache_slug(query: str, filter_str: Optional[str] = None) -> str:
    """Filesystem-safe, stable directory name for a query's cached pages.

    Keyed on the query text *and* any OpenAlex `filter` (e.g. a publication
    date range), since two different filters over the same query text are
    different result sets and must not share -- or silently overwrite --
    one another's cached pages. The unfiltered case hashes on the query text
    alone (not "query|"), unchanged from before --filter existed: this keeps
    every pre-existing unfiltered cache directory valid rather than orphaning
    it (that cache is also the most expensive to rebuild, since unfiltered
    queries paginate the deepest). Only a *filtered* run gets a new digest.
    """
    key = f"{query}|{filter_str}" if filter_str else query
    base = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:60]
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"{base}_{digest}" if base else digest


def fetch_query(
    query: str,
    api_key: Optional[str],
    max_results: Optional[int],
    cache_dir: Optional[Path] = None,
    refresh_cache: bool = False,
    filter_str: Optional[str] = None,
) -> Iterator[dict]:
    """
    Fetch OpenAlex search results for one query, paginating by cursor.

    OpenAlex API usage is metered/paid past small volumes, so each raw page
    response is cached to disk (one JSON file per page, in cursor order)
    when `cache_dir` is given. A re-run replays cached pages for free and
    only calls the API for pages beyond what's already cached -- including
    resuming a query that a previous, lower --max-per-query cut off early.
    `refresh_cache=True` ignores existing cache files and re-fetches
    everything, overwriting them.

    `filter_str`, when given, is passed through as OpenAlex's `filter` query
    param verbatim (e.g. `from_publication_date:1990-01-01,to_publication_date:1999-12-31`)
    and is folded into the cache key so a date-scoped run never collides
    with an unfiltered one over the same query text.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "computer-ethics-coverage/1.0"

    query_cache_dir = None
    if cache_dir is not None:
        query_cache_dir = Path(cache_dir) / _cache_slug(query, filter_str)
        query_cache_dir.mkdir(parents=True, exist_ok=True)

    cursor = "*"
    seen = 0
    page_num = 0
    cache_hits = 0

    while cursor:
        page_path = (
            query_cache_dir / f"page_{page_num:05d}.json"
            if query_cache_dir else None
        )

        if page_path is not None and page_path.exists() and not refresh_cache:
            payload = json.loads(page_path.read_text(encoding="utf-8"))
            cache_hits += 1
        else:
            params = {
                "search": query,
                "per_page": 100,
                "cursor": cursor,
                "select": (
                    "id,doi,title,publication_year,cited_by_count,"
                    "abstract_inverted_index,topics,keywords,type"
                ),
            }
            if filter_str:
                params["filter"] = filter_str
            if api_key:
                params["api_key"] = api_key

            payload = api_get(session, params)
            if page_path is not None:
                page_path.write_text(json.dumps(payload), encoding="utf-8")

        results = payload.get("results") or []
        if not results:
            break

        for work in results:
            yield work
            seen += 1
            if max_results is not None and seen >= max_results:
                return

        cursor = (payload.get("meta") or {}).get("next_cursor")
        page_num += 1
        if seen % 1000 == 0:
            print(f"  {query!r}: fetched {seen:,}", file=sys.stderr)

    if query_cache_dir is not None and cache_hits:
        print(
            f"  {query!r}: reused {cache_hits:,} cached page(s) from {query_cache_dir}",
            file=sys.stderr,
        )


def _date_filter(from_year: Optional[int], to_year: Optional[int]) -> Optional[str]:
    if from_year is None and to_year is None:
        return None
    parts = []
    if from_year is not None:
        parts.append(f"from_publication_date:{from_year}-01-01")
    if to_year is not None:
        parts.append(f"to_publication_date:{to_year}-12-31")
    return ",".join(parts)


def cmd_collect(args: argparse.Namespace) -> None:
    api_key = args.api_key or os.environ.get("OPENALEX_API_KEY")
    filter_str = _date_filter(args.from_year, args.to_year)

    queries = []
    with Path(args.queries).open("r", encoding="utf-8") as f:
        for line in f:
            q = line.strip()
            if q and not q.startswith("#"):
                queries.append(q)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    by_doi: dict[str, dict] = {}

    cache_dir = None if args.no_cache else Path(args.cache_dir)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    for i, query in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] Querying {query!r}", file=sys.stderr)
        for work in fetch_query(
            query,
            api_key,
            args.max_per_query,
            cache_dir=cache_dir,
            refresh_cache=args.refresh_cache,
            filter_str=filter_str,
        ):
            doi = normalize_doi(work.get("doi"))
            if not doi:
                continue
            score, reasons = relevance_score(work)
            if score < args.min_score:
                continue

            existing = by_doi.get(doi)
            record = {
                "doi": doi,
                "openalex_id": work.get("id"),
                "title": work.get("title"),
                "year": work.get("publication_year"),
                "cited_by_count": work.get("cited_by_count", 0),
                "type": work.get("type"),
                "relevance_score": score,
                "relevance_reasons": reasons,
                "matched_queries": [query],
            }
            if existing:
                if query not in existing["matched_queries"]:
                    existing["matched_queries"].append(query)
                if score > existing["relevance_score"]:
                    existing["relevance_score"] = score
                    existing["relevance_reasons"] = reasons
            else:
                by_doi[doi] = record

    records = sorted(
        by_doi.values(),
        key=lambda x: (-x["relevance_score"], -(x["cited_by_count"] or 0), x["doi"]),
    )

    with out.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Wrote {len(records):,} unique candidate works to {out}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a candidate computer-ethics corpus from OpenAlex."
    )
    sub = p.add_subparsers(dest="command", required=True)

    collect = sub.add_parser(
        "collect",
        help="Build a candidate computer-ethics corpus from OpenAlex.",
    )
    collect.add_argument("--queries", default="queries.txt")
    collect.add_argument("--output", default="output/candidates.jsonl")
    collect.add_argument(
        "--api-key",
        default=None,
        help="OpenAlex API key; otherwise uses OPENALEX_API_KEY.",
    )
    collect.add_argument(
        "--max-per-query",
        type=int,
        default=5000,
        help="Maximum raw OpenAlex search results fetched per query. "
             "Use 0 for unlimited.",
    )
    collect.add_argument(
        "--min-score",
        type=int,
        default=4,
        help="Minimum heuristic relevance score (default: 4).",
    )
    collect.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIR,
        help=(
            "Directory to cache raw OpenAlex page responses in, keyed by "
            f"query (default: {DEFAULT_CACHE_DIR}). Re-running collect "
            "reuses cached pages instead of re-querying (and re-paying "
            "for) the API."
        ),
    )
    collect.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the on-disk response cache entirely (always hit the API).",
    )
    collect.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing cached pages and re-fetch everything, overwriting the cache.",
    )
    collect.add_argument(
        "--from-year",
        type=int,
        default=None,
        help="Restrict results to publication_date >= this year (OpenAlex "
             "from_publication_date filter). Combine with --to-year to scope "
             "a run to one era -- e.g. so an older, low-volume period isn't "
             "crowded out of a query's --max-per-query cap by recent papers.",
    )
    collect.add_argument(
        "--to-year",
        type=int,
        default=None,
        help="Restrict results to publication_date <= this year (OpenAlex "
             "to_publication_date filter).",
    )
    collect.set_defaults(func=cmd_collect)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "max_per_query", None) == 0:
        args.max_per_query = None
    args.func(args)


if __name__ == "__main__":
    main()
