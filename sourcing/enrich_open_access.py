#!/usr/bin/env python3
"""
Enrich candidates.jsonl with OpenAlex's own legal-open-access signal
(open_access.is_oa / oa_status / oa_url), so only works with a legally free
copy go on to build_oa_starting_list.py.

Cheap by construction: batches candidates by their already-known
openalex_id (filter=ids.openalex:ID1|ID2|...), asking for only
`id,open_access,best_oa_location,locations` -- no need to re-run the
expensive full-text-search `collect` pagination just to add a few fields.
Each lookup is cached to disk (output/open_access_cache.jsonl, one line per
OpenAlex ID) so a re-run after collect adds more candidates only pays for
the new ones.

Also captures `oa_alt_urls`: OpenAlex's `locations` array often lists more
than one place a work is hosted, but `open_access.oa_url` only ever surfaces
the single "best" one -- which retrieve_papers.py has found dead/bot-blocked
often enough in practice that it always falls back to a live Unpaywall call
when it fails (rate-limited to one shared host, the actual bottleneck on a
large batch -- see CLAUDE.md/todo.md). A concrete example: OpenAlex's best
oa_url for SSRN paper 10.2139/ssrn.2477899 was just the SSRN landing page
(blocked for bots); a second, non-"best" location OpenAlex already had on
file pointed at a UC Berkeley repository that Unpaywall was separately able
to resolve to a working PDF. `oa_alt_urls` surfaces every other location
with a pdf_url (deduped, excluding whatever's already in oa_url) so
retrieve_papers.py can try them -- data already paid for in this same
request -- before ever spending a rate-limited Unpaywall call. Not a
guaranteed substitute for Unpaywall (OpenAlex's own locations aren't always
marked with a usable pdf_url even when a real one exists elsewhere -- see
the same example above), just more free shots on goal first.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

import requests

OPENALEX_WORKS = "https://api.openalex.org/works"
DEFAULT_CACHE = "output/open_access_cache.jsonl"


def short_id(openalex_id: str) -> str:
    """'https://openalex.org/W123' -> 'W123'."""
    return openalex_id.rsplit("/", 1)[-1]


def load_cache(path: Path) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if not path.exists():
        return cache
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cache[rec["id"]] = rec
    return cache


def api_get(session: requests.Session, params: dict, retries: int = 6) -> dict:
    delay = 1.0
    for attempt in range(retries):
        resp = session.get(OPENALEX_WORKS, params=params, timeout=60)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == retries - 1:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            time.sleep(delay + random.random() * 0.25)
            delay = min(delay * 2, 30)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")


def alt_urls(work: dict, oa_url: Optional[str]) -> list[str]:
    """Every other pdf_url in this work's `locations` besides the one
    open_access.oa_url already gave us, deduped and order-preserving
    (locations is roughly best-to-worst as OpenAlex ranks it, so trying them
    in this order is a reasonable heuristic even though it's not guaranteed)."""
    seen = {oa_url} if oa_url else set()
    urls = []
    for loc in work.get("locations") or []:
        url = loc.get("pdf_url")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def fetch_batch(session: requests.Session, ids: list[str], api_key: Optional[str]) -> dict[str, dict]:
    params = {
        "filter": "ids.openalex:" + "|".join(ids),
        "select": "id,open_access,best_oa_location,locations",
        "per_page": len(ids),
    }
    if api_key:
        params["api_key"] = api_key
    payload = api_get(session, params)
    out = {}
    for work in payload.get("results") or []:
        full_id = work.get("id") or ""
        oa = work.get("open_access") or {}
        oa_url = oa.get("oa_url")
        out[short_id(full_id)] = {
            "id": short_id(full_id),
            "is_oa": oa.get("is_oa"),
            "oa_status": oa.get("oa_status"),
            "oa_url": oa_url,
            "oa_alt_urls": alt_urls(work, oa_url),
        }
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidates", default="output/candidates.jsonl")
    p.add_argument("--api-key-file", default="openalex_api_key")
    p.add_argument("--api-key", default=None, help="Overrides --api-key-file; otherwise uses OPENALEX_API_KEY.")
    p.add_argument("--cache", default=DEFAULT_CACHE)
    p.add_argument("--batch-size", type=int, default=50)
    args = p.parse_args()

    api_key = args.api_key or os.environ.get("OPENALEX_API_KEY")
    if not api_key and Path(args.api_key_file).exists():
        api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip()

    candidates_path = Path(args.candidates)
    records = []
    with candidates_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    all_ids = sorted({short_id(r["openalex_id"]) for r in records if r.get("openalex_id")})
    print(f"{len(records):,} candidates, {len(all_ids):,} unique OpenAlex IDs", file=sys.stderr)

    cache_path = Path(args.cache)
    cache = load_cache(cache_path)
    todo = [i for i in all_ids if i not in cache]
    print(f"{len(all_ids) - len(todo):,} already cached, {len(todo):,} to fetch", file=sys.stderr)

    if todo:
        session = requests.Session()
        session.headers["User-Agent"] = "computer-ethics-coverage/1.0"
        cache_f = cache_path.open("a", encoding="utf-8")
        try:
            for i in range(0, len(todo), args.batch_size):
                batch = todo[i : i + args.batch_size]
                result = fetch_batch(session, batch, api_key)
                for oid in batch:
                    rec = result.get(oid, {"id": oid, "is_oa": None, "oa_status": None, "oa_url": None, "oa_alt_urls": []})
                    cache[oid] = rec
                    cache_f.write(json.dumps(rec) + "\n")
                cache_f.flush()
                print(f"  fetched {min(i + args.batch_size, len(todo)):,}/{len(todo):,}", file=sys.stderr)
        finally:
            cache_f.close()

    n_oa = 0
    out_records = []
    for r in records:
        oa = cache.get(short_id(r["openalex_id"])) if r.get("openalex_id") else None
        r["is_oa"] = oa.get("is_oa") if oa else None
        r["oa_status"] = oa.get("oa_status") if oa else None
        r["oa_url"] = oa.get("oa_url") if oa else None
        # .get(..., []) not oa["oa_alt_urls"] -- cache rows written before this field existed
        # don't have it; treat "unknown" the same as "no alternates" rather than KeyError.
        r["oa_alt_urls"] = (oa.get("oa_alt_urls") if oa else None) or []
        if r["is_oa"]:
            n_oa += 1
        out_records.append(r)

    backup = candidates_path.with_suffix(candidates_path.suffix + ".pre_oa_enrich.bak")
    candidates_path.replace(backup)
    print(f"Backed up pre-enrichment file to {backup}", file=sys.stderr)

    with candidates_path.open("w", encoding="utf-8") as f:
        for r in out_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nEnriched {len(out_records):,} candidates with OpenAlex open_access status.")
    print(f"Legally open access (is_oa=true): {n_oa:,} ({100 * n_oa / len(out_records):.1f}%)")


if __name__ == "__main__":
    main()
