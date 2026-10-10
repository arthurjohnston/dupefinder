"""Guess a DOI for General Index documents that have no metadata, from their keywords.

Each document's best keywords (gi_keywords.py) are sent to a search API -- Crossref's bibliographic
search by default, which has no daily cap; OpenAlex's search (--backend openalex) draws on a
per-IP daily budget when no OPENALEX_API_KEY is set, and that budget ran out on 2026-10-05 -- and a
candidate is accepted only if enough of its own title's significant words appear among the
document's keywords: a title is a summary of the text, so its words should be among the text's
most meaningful terms. Both sides are reduced to 5-letter stems, since the index lemmatizes
("mathematic") and titles don't.

Run on documents whose DOI is known (--controls, with META_DB) to measure accuracy before trusting
it on unnamed ones.

Usage: gi_identify.py KEYWORDS_JSONL OUT_TSV --email ADDR [--only DKEYS_JSON] [--controls DKEYS_JSON
                      --meta-db gi_meta.sqlite3] [--min-title-match 0.6] [--interval 0.15]
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time

import requests

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
STOP = set("a an the of and in on for to with by from at as is are be been was were using based its their "
           "via into new study analysis this that these those we our can which or not".split())


def stems(text):
    return {w[:5] for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP}


def query_for(keywords, max_words=12):
    words, seen = [], set()
    for phrase, _ in sorted(keywords, key=lambda k: (-len(k[0].split()), k[1])):  # multi-word first
        for w in re.findall(r"[a-z]{3,}", phrase.lower()):
            if w not in STOP and w[:5] not in seen:
                seen.add(w[:5])
                words.append(w)
        if len(words) >= max_words:
            break
    return " ".join(words[:max_words])


def search(session, args, q):
    """Up to 5 candidates as [{"doi", "title", "publication_year"}]; [] on repeated failure.
    A 429 is not retried past a minute: a daily budget (OpenAlex without a key) won't reset in time."""
    if args.backend == "crossref":
        url, params = CROSSREF, {"query.bibliographic": q, "rows": 5, "select": "DOI,title,issued",
                                 "mailto": args.email}
    else:
        url, params = OPENALEX, {"search": q, "per-page": 5, "mailto": args.email,
                                 "select": "doi,title,display_name,publication_year"}
        if os.environ.get("OPENALEX_API_KEY"):
            params["api_key"] = os.environ["OPENALEX_API_KEY"]
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=60)
            if resp.status_code == 429:
                if int(resp.headers.get("retry-after", "0") or 0) > 60:
                    raise SystemExit(f"rate limited for {resp.headers['retry-after']}s by {url}; stopping")
                time.sleep(20 * (attempt + 1))
                continue
            if resp.status_code != 200:
                return []
            data = resp.json()
            break
        except (requests.RequestException, ValueError):
            time.sleep(5 * (attempt + 1))
    else:
        return []
    if args.backend == "openalex":
        return data.get("results", [])
    out = []
    for it in data.get("message", {}).get("items", []):
        year = ((it.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        out.append({"doi": it.get("DOI", ""), "title": (it.get("title") or [""])[0], "publication_year": year})
    return out


def best_candidate(results, kw_stems, min_match):
    best = None
    for w in results:
        ts = stems(w.get("title") or w.get("display_name"))
        if len(ts) < 3:
            continue
        match = len(ts & kw_stems) / len(ts)
        if match >= min_match and (best is None or match > best[0]):
            best = (match, w)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keywords_jsonl")
    ap.add_argument("out_tsv")
    ap.add_argument("--email", required=True)
    ap.add_argument("--only", help="JSON list of dkeys to process (default: all in KEYWORDS_JSONL)")
    ap.add_argument("--controls", help="JSON list of dkeys with known DOIs, to measure accuracy")
    ap.add_argument("--meta-db")
    ap.add_argument("--min-title-match", type=float, default=0.6)
    ap.add_argument("--interval", type=float, default=0.15)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--backend", choices=["crossref", "openalex"], default="crossref")
    args = ap.parse_args()

    only = set(json.load(open(args.only))) if args.only else None
    controls = set(json.load(open(args.controls))) if args.controls else set()
    truth = {}
    if controls:
        db = sqlite3.connect(args.meta_db)
        for k in controls:
            row = db.execute("select doi from meta where dkey = ?", (k,)).fetchone()
            if row and row[0]:
                truth[k] = row[0].lower()
    session = requests.Session()
    tally = {"done": 0, "identified": 0, "correct": 0, "controls": 0, "controls_identified": 0}
    with open(args.keywords_jsonl) as f, open(args.out_tsv, "w") as out:
        w = csv.writer(out, delimiter="\t")
        w.writerow(["dkey", "query", "doi", "title", "year", "title_match", "control_truth"])
        for line in f:
            d = json.loads(line)
            k = d["dkey"]
            if only is not None and k not in only and k not in controls:
                continue
            q = query_for(d["keywords"])
            if not q:
                continue
            results = search(session, args, q)
            time.sleep(args.interval)
            kw_stems = set().union(*(stems(p) for p, _ in d["keywords"])) if d["keywords"] else set()
            best = best_candidate(results, kw_stems, args.min_title_match)
            doi = (best[1].get("doi") or "").replace("https://doi.org/", "").lower() if best else ""
            tally["done"] += 1
            tally["identified"] += bool(doi)
            if k in truth:
                tally["controls"] += 1
                tally["controls_identified"] += bool(doi)
                tally["correct"] += doi == truth[k]
            w.writerow([k, q, doi, (best[1].get("title") or "") if best else "",
                        best[1].get("publication_year") if best else "",
                        f"{best[0]:.2f}" if best else "", truth.get(k, "")])
            if tally["done"] % 200 == 0:
                out.flush()
                print(f"{tally['done']:,} done, {tally['identified']:,} identified"
                      + (f"; controls: {tally['controls_identified']}/{tally['controls']} identified, "
                     f"{tally['correct']} correct" if tally["controls"] else ""),
                      flush=True)
            if args.limit and tally["done"] >= args.limit:
                break
    print(f"done: {tally}", flush=True)


if __name__ == "__main__":
    main()
