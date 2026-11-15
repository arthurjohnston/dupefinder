"""Fill in the abstract check for worklist pairs it never covered: fetch the abstracts OpenAlex doesn't have
from Europe PMC (PubMed's abstracts), Crossref and Semantic Scholar, then compare them as gi_abstract_check.py
does (a 20+ word verbatim run = 'shared text').

The abstract check is the lead score's strongest signal (gi_lead_score.py), but gi_abstract_check.py reads
OpenAlex only, which has no abstract for many paywalled papers -- on 2026-11-14, 1,675 of the 3,018 top-30
worklist pairs were 'not checked', mostly for a missing abstract on one side. This takes the pairs of a
gi_unobtainable_sort.py worklist whose abstract_check is 'not checked' and, per DOI:
  1. OpenAlex, for a DOI never asked (gi_abstract_check.py's request);
  2. Europe PMC (gi_metadata.py's cached lookup, epmc_works: abstractText);
  3. Crossref (gi_metadata.py's cached lookup, crossref_works: abstract, JATS tags stripped);
  4. Semantic Scholar's paper batch API (500 DOIs a request, no key).
The first abstract of at least gi_abstract_check.MIN_WORDS words goes into the `abstracts` cache (with its
source in `source`), and the pair's verdict into `abstract_checks` (checked_in 'fill <date>').
gi_unobtainable_sort.py reads abstract_checks for pairs `leads` still has as 'not checked', so re-run it after
this; `leads` itself changes only when gi_rank_leads.py next runs. A pair with a no-DOI side ('gi:') can't be
looked up and stays 'not checked'.

Usage: gi_abstract_fill.py [WORKLIST.csv]   (default: the top-30 worklist)
"""
import argparse
import html
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402
import text_overlap as to  # noqa: E402

import gi_abstract_check as gac  # noqa: E402
import gi_db  # noqa: E402
import gi_metadata  # noqa: E402

log = logging.getLogger("gi_abstract_fill")
DEFAULT = gi_db.DATA / "unobtainable_western_2026-10-30_by_band_prestige_top30.csv"
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_SIZE = 500
TAG = re.compile(r"<[^>]+>")
LEAD_IN = re.compile(r"^\s*(abstract|summary)\s*[:.]?\s*", re.I)


def clean(text):
    """Markup off (JATS, HTML), entities decoded, a leading 'Abstract' heading dropped, spaces collapsed."""
    t = " ".join(html.unescape(TAG.sub(" ", text or "")).split())
    return LEAD_IN.sub("", t)


def worklist_pairs(db, path):
    """[(doi_a, doi_b)] (lower case, as in leads) of the worklist's 'not checked' pairs with a DOI on both sides."""
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        ids = sorted({int(r["pair_id"]) for r in csv.DictReader(f) if r["abstract_check"] == "not checked"})
    out = []
    for i in range(0, len(ids), 900):
        ch = ids[i:i + 900]
        for a, b in db.execute(f"select doi_a, doi_b from leads where pair_id in ({','.join('?' * len(ch))})", ch):
            if not (a.startswith("gi:") or b.startswith("gi:")):
                out.append((a.lower(), b.lower()))
    return sorted(set(out))


def have(db, dois):
    """{doi: abstract} for the DOIs whose cached abstract is long enough to compare."""
    out = {}
    for d in dois:
        r = db.execute("select abstract from abstracts where doi = ?", (d,)).fetchone()
        if r and r[0] and len(to.words(r[0])) >= gac.MIN_WORDS:
            out[d] = r[0]
    return out


def store(db, found, source):
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    db.executemany("insert or replace into abstracts (doi, words, abstract, fetched_at, source) values (?, ?, ?, ?, ?)",
                   [(d, len(to.words(t)), t, now, source) for d, t in found.items()])
    db.commit()
    log.info("%s: %d abstracts", source, len(found))


def openalex(db, dois):
    """gi_abstract_check.py's OpenAlex request, for DOIs it never asked about."""
    asked = {r[0] for r in db.execute("select doi from abstracts")}
    need = [d for d in dois if d not in asked]
    session, limiter = requests.Session(), rp.RateLimiter(1.0)
    req = argparse.Namespace(email=gi_metadata.CONTACT_EMAIL, timeout=60)
    found, empty = {}, []
    for i in range(0, len(need), rp.OPENALEX_BATCH_SIZE):
        batch = need[i:i + rp.OPENALEX_BATCH_SIZE]
        res = rp._openalex_works_batch_request(session, batch, limiter, req, log, "abstracts")
        if res is None:
            continue
        got = {(rp.normalize_doi(w.get("doi")) or "").lower(): gac.abstract_text(w.get("abstract_inverted_index"))
               for w in res}
        found.update({d: got[d] for d in batch if got.get(d)})
        empty += [d for d in batch if not got.get(d)]
    db.executemany("insert or ignore into abstracts (doi, words, abstract, fetched_at, source) values (?, 0, '', ?, 'openalex')",
                   [(d, f"{datetime.now():%Y-%m-%d %H:%M}") for d in empty])
    store(db, found, "openalex")


def from_cache(db, table, field, dois):
    out = {}
    for d in dois:
        r = db.execute(f"select raw from {table} where doi = ?", (d,)).fetchone()
        t = clean((json.loads(r[0]) if r and r[0] else {}).get(field))
        if len(to.words(t)) >= gac.MIN_WORDS:
            out[d] = t
    return out


def semantic_scholar(dois):
    out = {}
    session = requests.Session()
    for i in range(0, len(dois), S2_SIZE):
        batch = dois[i:i + S2_SIZE]
        for attempt in range(6):
            resp = session.post(S2_BATCH, params={"fields": "abstract"}, json={"ids": [f"DOI:{d}" for d in batch]},
                                timeout=120)
            if resp.status_code != 429:
                break
            time.sleep(5 * (attempt + 1))
        if resp.status_code != 200:
            log.warning("Semantic Scholar: HTTP %s for a batch of %d, skipped", resp.status_code, len(batch))
            continue
        for d, rec in zip(batch, resp.json()):
            t = clean((rec or {}).get("abstract"))
            if len(to.words(t)) >= gac.MIN_WORDS:
                out[d] = t
        time.sleep(1.5)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("worklist", nargs="?", default=str(DEFAULT))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    db = gi_db.connect(str(gi_db.DB_PATH))
    if "source" not in {r[1] for r in db.execute("pragma table_info(abstracts)")}:
        db.execute("alter table abstracts add column source text")   # where the text came from; null: OpenAlex
    pairs = worklist_pairs(db, args.worklist)
    dois = sorted({d for p in pairs for d in p})
    log.info("%d 'not checked' pairs with two DOIs, %d DOIs", len(pairs), len(dois))

    openalex(db, dois)
    def missing():
        got = have(db, dois)
        return [d for d in dois if d not in got]
    mdb = sqlite3.connect(gi_db.DB_PATH, timeout=300, check_same_thread=False)   # its fetchers write from threads
    meta = gi_metadata.Metadata(mdb, argparse.Namespace(max_calls=0, workers=4, email=gi_metadata.CONTACT_EMAIL))
    need = missing()
    meta.epmc(need)
    store(db, from_cache(db, "epmc_works", "abstractText", need), "europepmc")
    need = missing()
    meta.crossref(need)
    store(db, from_cache(db, "crossref_works", "abstract", need), "crossref")
    store(db, semantic_scholar(missing()), "semanticscholar")

    text = have(db, dois)
    label = f"fill {datetime.now():%Y-%m-%d}"
    rows, tally = [], {"shared text": 0, "no shared text": 0, "still missing": 0}
    for da, dbb in pairs:
        if da not in text or dbb not in text:
            tally["still missing"] += 1
            continue
        a, b = text[da], text[dbb]
        run = gac.longest_run_words(a, b)
        verdict = "shared text" if run >= gac.SHARED_RUN else "no shared text"
        tally[verdict] += 1
        rows.append((da, dbb, *gi_db.pair_lo_hi(da, dbb), verdict, run, round(gac.containment5(a, b), 2),
                     len(to.words(a)), len(to.words(b)), label))
    db.executemany("insert or replace into abstract_checks values (?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    print(f"{len(pairs)} pairs: " + ", ".join(f"{k} {v}" for k, v in tally.items()))


if __name__ == "__main__":
    sys.exit(main())
