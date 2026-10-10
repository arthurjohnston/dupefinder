"""Step 3 (abstract evidence): compare both papers' OpenAlex abstracts for leads (gi.sqlite3).

The index is mostly older, paywalled literature: both PDFs of a pair could be retrieved for under a
tenth of leads (PILOT_FINDINGS.md). OpenAlex holds abstracts for many paywalled works, 50 DOIs per
request. This picks leads from `leads` (by reason, author relation and band; all of them, or
--per-cell per band x relation), fetches both abstracts and measures their overlap.

Reads/writes:
  abstracts         per-DOI cache: abstract text, word count, when fetched. A DOI OpenAlex returned
                    no abstract for is cached as words = 0 so it isn't re-requested. Rows imported
                    from the TSV era have the word count but no text (it was never kept).
  abstract_checks   per DOI pair (as ordered in leads): verdict ('shared text' when the abstracts
                    share a verbatim run of >= 20 words, 'no shared text'), longest run, 5-gram
                    containment of the shorter abstract, word counts. Pairs already checked are
                    skipped unless --recheck.

An abstract is a weak proxy for the whole paper: a copied paper usually copies its abstract, but
a paper can reuse body text under a rewritten abstract, so a low abstract overlap is not proof
of no copying. A high one (a long shared run) is strong evidence of shared text.

OpenAlex: set OPENALEX_API_KEY (see NEXT_STEPS.md); a filter call costs ~$0.0001, 50 DOIs each.

Usage: gi_abstract_check.py --email ADDR [--db data/gi.sqlite3] [--relations "no shared author"]
         [--bands ">=90%" "50-90%" "15-50%"] [--reasons ""] [--per-cell N] [--seed 1] [--recheck]
"""
import argparse
import difflib
import logging
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402
import text_overlap as to  # noqa: E402

import gi_db  # noqa: E402
from gi_report import BANDS  # noqa: E402

RELATIONS = ("no shared author", "shared author")
SHARED_RUN = 20  # words; a shared run this long in two abstracts is copied text, not shared phrasing
MIN_WORDS = 30


def abstract_text(inverted):
    if not inverted:
        return ""
    pos = {}
    for word, idxs in inverted.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def longest_run_words(a, b):
    """Longest run of consecutive words shared verbatim, in words (text_overlap's
    longest_common_word_run() gives the same run as a fraction of the shorter text)."""
    wa, wb = to.words(a), to.words(b)
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).find_longest_match(
        0, len(wa), 0, len(wb)).size


def containment5(a, b):
    """Share of the shorter abstract's word 5-grams found in the other."""
    def grams(t):
        w = to.words(t)
        return {tuple(w[i:i + 5]) for i in range(len(w) - 4)}
    ga, gb = grams(a), grams(b)
    small, big = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
    return len(small & big) / len(small) if small else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--email", required=True)
    ap.add_argument("--per-cell", type=int, default=0, help="sample this many pairs per band x relation (0 = all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--relations", nargs="+", default=list(RELATIONS))
    ap.add_argument("--bands", nargs="+", default=[label for _, _, label in BANDS])
    ap.add_argument("--reasons", nargs="+", default=[""],
                    help="gi_filters reasons to include; default only kept pairs (empty reason)")
    ap.add_argument("--recheck", action="store_true", help="re-score pairs already in abstract_checks")
    ap.add_argument("--label", default=f"run {datetime.now():%Y-%m-%d %H:%M}")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logger = logging.getLogger("gi_abstract_check")
    db = gi_db.connect(args.db)

    done = set() if args.recheck else {(r[0], r[1]) for r in db.execute("select doi_a, doi_b from abstract_checks")}
    q = (f"select doi_a, doi_b, band, authors from leads where reason in ({','.join('?' * len(args.reasons))}) "
         f"and authors in ({','.join('?' * len(args.relations))}) and band in ({','.join('?' * len(args.bands))}) "
         f"order by pair_id")
    cells, seen, skipped = defaultdict(list), set(), 0
    for r in db.execute(q, (*args.reasons, *args.relations, *args.bands)):
        k = (r["doi_a"].lower(), r["doi_b"].lower())
        if k in seen or k[0].startswith("gi:") or k[1].startswith("gi:"):
            continue
        seen.add(k)
        if k in done:
            skipped += 1
            continue
        cells[(r["band"], r["authors"])].append(k)
    rng = random.Random(args.seed)
    picked = []
    for group in cells.values():
        picked += rng.sample(group, min(args.per_cell, len(group))) if args.per_cell else group
    logger.info("%d pairs to check (%d already checked, skipped)", len(picked), skipped)

    cache = {r["doi"]: r for r in db.execute("select * from abstracts")}
    need = sorted({d for k in picked for d in k
                   if d not in cache or (cache[d]["abstract"] is None and cache[d]["words"])})
    session, limiter = requests.Session(), rp.RateLimiter(1.0)
    req_args = argparse.Namespace(email=args.email, timeout=60)
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    for i in range(0, len(need), rp.OPENALEX_BATCH_SIZE):
        batch = need[i:i + rp.OPENALEX_BATCH_SIZE]
        results = rp._openalex_works_batch_request(session, batch, limiter, req_args, logger, "abstracts")
        if results is None:
            continue  # a failed batch: leave these uncached so the next run retries them
        got = {}
        for work in results:
            d = rp.normalize_doi(work.get("doi"))
            if d:
                got[d.lower()] = abstract_text(work.get("abstract_inverted_index"))
        db.executemany("insert or replace into abstracts (doi, words, abstract, fetched_at) values (?,?,?,?)",
                       [(d, len(to.words(got.get(d, ""))), got.get(d, ""), now) for d in batch])
        db.commit()
        if (i // rp.OPENALEX_BATCH_SIZE) % 20 == 0:
            logger.info("fetched %d/%d DOIs", min(i + len(batch), len(need)), len(need))
    cache = {r["doi"]: r for r in db.execute("select doi, words, abstract from abstracts")}

    tally, rows = Counter(), []
    for da, dbb in picked:
        a = (cache[da]["abstract"] if da in cache else None) or ""
        b = (cache[dbb]["abstract"] if dbb in cache else None) or ""
        wa, wb = len(to.words(a)), len(to.words(b))
        if wa < MIN_WORDS or wb < MIN_WORDS:
            tally["no abstract"] += 1
            continue
        run = longest_run_words(a, b)
        verdict = "shared text" if run >= SHARED_RUN else "no shared text"
        tally[verdict] += 1
        rows.append((da, dbb, *gi_db.pair_lo_hi(da, dbb), verdict, run, round(containment5(a, b), 2), wa, wb,
                     args.label))
    db.executemany("insert or replace into abstract_checks values (?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    print(f"{len(picked):,} pairs: {tally['shared text']:,} shared text, {tally['no shared text']:,} no shared text, "
          f"{tally['no abstract']:,} without both abstracts")


if __name__ == "__main__":
    main()
