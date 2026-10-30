"""JSTOR side of the General Index pilot: JSTOR's text-analysis metadata, matched to GI candidates.

JSTOR Text Analysis Support (jstor.org/ta-support, Constellate's successor since 2025-07-01) gives
full OCR text for an uploaded list of item IDs. The IDs are UUIDs (`item_id`), not DOIs, so a request
needs JSTOR's bibliographic metadata file (jstor.org/ta-support/metadata, gzip JSON Lines, all of
JSTOR) to map DOIs to them. That file has no abstracts. See data/jstor_experiment/README.md.

  load      the metadata .jsonl.gz -> data/jstor.sqlite3 `items` (one row per item)
  match     gi.sqlite3 `leads` (ranked tiers) x items -> data/jstor.sqlite3 `lead_items`: every
            JSTOR-held side of a ranked lead, with the item's review_required/licensing_status
  request   write item-ID lists (one UUID per line, the upload format) for a chosen set of leads

Usage:
  gi_jstor.py load [FILE]            default: the newest jstor_metadata_*.jsonl.gz in this folder
  gi_jstor.py match [--tiers 1 2 3 4 5]
  gi_jstor.py request --out DIR [--tiers 1 2] [--no-review-only]
"""
import argparse
import glob
import gzip
import json
import sqlite3
import sys
from pathlib import Path

import gi_db

HERE = Path(__file__).resolve().parent
JSTOR_DB = gi_db.DATA / "jstor.sqlite3"
JSTOR_PREFIX = "10.2307/"

SCHEMA = """
create table if not exists items (
    item_id text primary key, doi text, title text, is_part_of text, creators text, year integer,
    published_date text, issn text, journal_code text, content_type text, content_subtype text,
    disciplines text, languages text, review_required integer, licensing_status text, url text
);
create index if not exists idx_items_doi on items(doi);
"""


def connect():
    db = sqlite3.connect(JSTOR_DB, timeout=600)
    db.executescript(SCHEMA)
    return db


def _joined(v):
    return "; ".join(str(x) for x in v) if isinstance(v, list) else v


def item_row(r):
    ids = r.get("identifiers") or {}
    date = r.get("published_date") or ""
    year = int(date[:4]) if date[:4].isdigit() else None
    return (r["item_id"], (r.get("ithaka_doi") or "").lower() or None, r.get("title"),
            r.get("is_part_of"), r.get("creators_string"), year, date or None,
            ids.get("print_issn") or ids.get("online_issn"), ids.get("journal_code"),
            r.get("content_type"), r.get("content_subtype"), _joined(r.get("discipline_names")),
            _joined(r.get("languages")), int(bool(r.get("review_required"))),
            r.get("licensing_status"), r.get("url"))


def load(path):
    db = connect()
    db.execute("delete from items")
    n = 0
    batch = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            batch.append(item_row(json.loads(line)))
            if len(batch) == 50_000:
                db.executemany("insert or replace into items values (%s)" % ",".join("?" * 16), batch)
                n += len(batch)
                batch = []
                if n % 1_000_000 == 0:
                    print(f"{n:,} items", file=sys.stderr)
    db.executemany("insert or replace into items values (%s)" % ",".join("?" * 16), batch)
    n += len(batch)
    db.commit()
    print(f"loaded {n:,} items from {path}")


def match(tiers):
    db = connect()
    db.execute("attach ? as gi", (str(gi_db.DB_PATH),))
    db.executescript("""
        drop table if exists lead_items;
        create table lead_items (
            pair_lo text, pair_hi text, tier integer, side_doi text, item_id text,
            review_required integer, licensing_status text, other_doi text, other_is_jstor integer,
            primary key (pair_lo, pair_hi, side_doi)
        );""")
    marks = ",".join("?" * len(tiers))
    for side, other in (("pair_lo", "pair_hi"), ("pair_hi", "pair_lo")):
        db.execute(f"""
            insert or ignore into lead_items
            select l.pair_lo, l.pair_hi, min(l.tier), l.{side}, i.item_id, i.review_required,
                   i.licensing_status, l.{other}, l.{other} like '{JSTOR_PREFIX}%'
            from gi.leads l left join items i on i.doi = l.{side}
            where l.tier in ({marks}) and l.{side} like '{JSTOR_PREFIX}%'
            group by l.pair_lo, l.pair_hi""", tiers)
    db.commit()
    for row in db.execute("""
            select tier, count(*), sum(item_id is not null), sum(review_required = 0),
                   sum(other_is_jstor) from lead_items group by tier order by tier"""):
        print("tier %s: %s JSTOR sides, %s found in metadata, %s need no review, %s other side JSTOR too"
              % row)


def partners(email, tiers):
    """OpenAlex work records (OA copy, year, venue, abstract) for both sides of the matched leads,
    so a JSTOR full-text request can be limited to pairs whose other side can be had too."""
    import logging
    from datetime import datetime

    import requests
    sys.path.insert(0, str(HERE.parent))
    import retrieve_papers as rp
    import text_overlap as to
    from gi_abstract_check import abstract_text

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logger = logging.getLogger("gi_jstor")
    db = connect()
    db.executescript("""
        create table if not exists openalex (
            doi text primary key, found integer, year integer, venue text, work_type text,
            is_oa integer, oa_pdf_url text, oa_landing_url text, words integer, abstract text,
            fetched_at text
        );""")
    marks = ",".join("?" * len(tiers))
    dois = {d for r in db.execute(f"select side_doi, other_doi from lead_items where tier in ({marks})", tiers)
            for d in r if d and not d.startswith("gi:")}
    done = {r[0] for r in db.execute("select doi from openalex")}
    need = sorted(dois - done)
    logger.info("%d DOIs to look up (%d already cached)", len(need), len(dois & done))
    session, limiter = requests.Session(), rp.RateLimiter(1.0)
    req_args = argparse.Namespace(email=email, timeout=60)
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    for i in range(0, len(need), rp.OPENALEX_BATCH_SIZE):
        batch = need[i:i + rp.OPENALEX_BATCH_SIZE]
        results = rp._openalex_works_batch_request(session, batch, limiter, req_args, logger, "jstor partners")
        if results is None:
            continue  # leave uncached so the next run retries
        got = {}
        for w in results:
            d = rp.normalize_doi(w.get("doi"))
            if not d:
                continue
            oa = w.get("best_oa_location") or {}
            text = abstract_text(w.get("abstract_inverted_index"))
            got[d.lower()] = (1, w.get("publication_year"),
                              ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                              w.get("type"), int(bool((w.get("open_access") or {}).get("is_oa"))),
                              oa.get("pdf_url"), oa.get("landing_page_url"), len(to.words(text)), text)
        db.executemany("insert or replace into openalex values (?,?,?,?,?,?,?,?,?,?,?)",
                       [(d, *got.get(d, (0,) + (None,) * 8), now) for d in batch])
        db.commit()
        if (i // rp.OPENALEX_BATCH_SIZE) % 50 == 0:
            logger.info("looked up %d/%d", min(i + len(batch), len(need)), len(need))


# JSTOR journal names whose publisher-side record (OpenAlex venue) goes by another name.
VENUE_ALIASES = {"bmj": ("british medical journal", "association medical journal",
                         "provincial medical surgical journal"),
                 # renamed journals: the JSTOR run under the old title, OpenAlex under the new
                 "mathematics computation": ("mathematical tables and other aids to computation",),
                 "occupational environmental medicine": ("british journal of industrial medicine",)}


def _venue_words(s):
    s = "".join(c if c.isalnum() else " " for c in (s or "").lower())
    return {w for w in s.split() if w not in {"the", "of", "and", "journal", "for", "in", "on"}}


def same_venue(j_journal, o_venue):
    """The JSTOR item and its partner are in one journal (JSTOR mirrors the publisher's DOI, or two
    items share a printed page). Either name's significant words contained in the other's, or an
    alias. Most JSTOR-sided candidates are this: PNAS and BMJ alone are ~3,500 of ~10,700."""
    if not j_journal or not o_venue:
        return False
    jl, ol = (" ".join("".join(c if c.isalnum() else " " for c in s.lower()).split()) for s in (j_journal, o_venue))
    for new, olds in VENUE_ALIASES.items():
        names = (jl, ol)
        if any(new in x for x in names) and any(o in x for o in olds for x in names):
            return True
    a, b = _venue_words(jl), _venue_words(ol)
    return bool(a and b) and (a <= b or b <= a)


def mark_same_venue(db):
    cols = {r[1] for r in db.execute("pragma table_info(jstor_pairs)")}
    if "same_venue" not in cols:
        db.execute("alter table jstor_pairs add column same_venue integer")
    rows = db.execute("select rowid, j_journal, o_venue from jstor_pairs").fetchall()
    db.executemany("update jstor_pairs set same_venue = ? where rowid = ?",
                   [(int(same_venue(j, o)), rid) for rid, j, o in rows])
    db.commit()


def screen(tiers):
    """One row per JSTOR-sided DOI pair (best-ranked document pair): GI shares, years, titles, the
    other side's OA copy, and the gi_abstract_check test where OpenAlex has both abstracts."""
    from gi_abstract_check import MIN_WORDS, SHARED_RUN, containment5, longest_run_words

    db = connect()
    db.row_factory = sqlite3.Row
    db.execute("attach ? as gi", (str(gi_db.DB_PATH),))
    marks = ",".join("?" * len(tiers))
    oa = {r["doi"]: r for r in db.execute("select * from openalex")}
    rows = []
    for r in db.execute(f"""
            select li.*, d.lead_rank, d.shared, d.doi_a, d.pct_a, d.pct_b, d.title_a, d.title_b,
                   d.year_a, d.year_b, d.authors, d.shape, i.title j_title, i.is_part_of j_journal,
                   i.year j_year, i.disciplines
            from lead_items li
            join gi.lead_details d on d.pair_lo = li.pair_lo and d.pair_hi = li.pair_hi and d.primary_pair = 1
            left join items i on i.item_id = li.item_id
            where li.tier in ({marks})""", tiers):
        j_is_a = r["doi_a"].lower() == r["side_doi"]
        jo, oo = oa.get(r["side_doi"]), oa.get(r["other_doi"])
        ja, ob = (jo["abstract"] if jo else "") or "", (oo["abstract"] if oo else "") or ""
        run = cont = None
        if len(ja.split()) >= MIN_WORDS and len(ob.split()) >= MIN_WORDS:
            run, cont = longest_run_words(ja, ob), round(containment5(ja, ob), 2)
        o_year = (oo["year"] if oo else None) or (r["year_b"] if j_is_a else r["year_a"])
        j_year = r["j_year"] or (r["year_a"] if j_is_a else r["year_b"])
        rows.append(dict(
            pair_lo=r["pair_lo"], pair_hi=r["pair_hi"], tier=r["tier"], lead_rank=r["lead_rank"],
            shape=r["shape"], shared=r["shared"], authors=r["authors"],
            j_doi=r["side_doi"], j_item=r["item_id"], j_review=r["review_required"],
            j_pct=r["pct_a"] if j_is_a else r["pct_b"], j_year=j_year,
            j_title=r["j_title"] or (r["title_a"] if j_is_a else r["title_b"]), j_journal=r["j_journal"],
            j_disc=r["disciplines"], j_abs_words=jo["words"] if jo else None,
            o_doi=r["other_doi"], o_jstor=r["other_is_jstor"], o_pct=r["pct_b"] if j_is_a else r["pct_a"],
            o_year=o_year, o_title=r["title_b"] if j_is_a else r["title_a"],
            o_venue=oo["venue"] if oo else None, o_oa=oo["is_oa"] if oo else None,
            o_pdf=(oo["oa_pdf_url"] or oo["oa_landing_url"]) if oo else None,
            o_abs_words=oo["words"] if oo else None,
            direction=None if not (j_year and o_year) or int(j_year) == int(o_year)
            else ("jstor earlier" if int(j_year) < int(o_year) else "jstor later"),
            abs_run=run, abs_cont=cont,
            abs_verdict=None if run is None else ("shared text" if run >= SHARED_RUN else "no shared text")))
    db.execute("drop table if exists jstor_pairs")
    cols = list(rows[0])
    db.execute("create table jstor_pairs (%s, primary key (pair_lo, pair_hi, j_doi))" % ", ".join(cols))
    db.executemany("insert or ignore into jstor_pairs values (%s)" % ",".join("?" * len(cols)),
                   [tuple(x.values()) for x in rows])
    db.commit()
    for q, label in [
            ("select count(*) from jstor_pairs", "DOI pairs"),
            ("select count(*) from jstor_pairs where j_item is not null", "JSTOR side eligible for text analysis"),
            ("select count(*) from jstor_pairs where o_oa = 1", "other side has an OA copy"),
            ("select count(*) from jstor_pairs where j_item is not null and (o_oa = 1 or o_jstor and o_doi in "
             "(select doi from items))", "...both obtainable (JSTOR + OA or JSTOR + JSTOR)"),
            ("select count(*) from jstor_pairs where abs_run is not null", "both abstracts in OpenAlex"),
            ("select count(*) from jstor_pairs where abs_verdict = 'shared text'", "...sharing a 20+ word run"),
            ("select direction || ': ' || count(*) from jstor_pairs group by direction", "direction")]:
        print(f"{label}: " + ", ".join(str(v[0]) for v in db.execute(q)))


TRIAGE_KINDS = ["index artifact", "reprint or anthology", "translation", "review or commentary on the other", "same work",
                "shared page or issue", "erratum or reply", "possible copying", "unclear"]
TRIAGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pairs"], "properties": {
    "pairs": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "kind", "worth_full_text", "reason"],
        "properties": {"id": {"type": "integer"}, "kind": {"type": "string", "enum": TRIAGE_KINDS},
                       "worth_full_text": {"type": "boolean"}, "reason": {"type": "string"}}}}}}
TRIAGE_PROMPT = """You are triaging candidate text-reuse pairs from the General Index, a 5-gram index of
scholarly articles. Each pair has one side held by JSTOR. The index says the two documents share many
rare 5-grams; "pct" is the share of each document's sampled 5-grams found in the other. We can only get
full text for a limited number, so decide from the metadata alone which pairs are worth it.

Most pairs are legitimate: a journal article reprinted in a book or anthology (Cambridge/Oxford eBooks,
"Companion", "Readings", collected essays), a translation, a review of a book that quotes it, a
commentary or reply, the same work under two records, items printed on one page or issue, or an
erratum. Mark those worth_full_text=false.

"index artifact": the index sometimes files one scanned file under two records, so two unrelated works
(different topics, decades, fields) show a very high pct on BOTH sides (roughly 70%+ each). Real
reuse cannot make two unrelated papers ~90% identical, so do not explain such a pair as shared
quotations or terminology: call it an index artifact, worth_full_text=false. Ordinary quotation and
shared terminology produce low pct (a few percent), not tens of percent. Mark worth_full_text=true when the later side looks like a
different, unrelated work in a different venue (different authors, retitled) that may have copied the
earlier one -- e.g. a later paper in a low-scrutiny journal or conference whose title is a variant of an
earlier JSTOR article's. Author strings may be incomplete or missing; that alone is not evidence.
Give one short reason per pair. Return every id."""


def triage(model, limit, batch_size, max_cost):
    import anthropic
    from concurrent.futures import ThreadPoolExecutor

    from gi_agent_review import PRICES, client

    db = connect()
    db.row_factory = sqlite3.Row
    db.execute("attach ? as gi", (str(gi_db.DB_PATH),))
    db.executescript("""
        create table if not exists triage (
            pair_lo text, pair_hi text, j_doi text, model text, kind text, worth_full_text integer,
            reason text, at text, primary key (pair_lo, pair_hi, j_doi, model));""")
    rows = db.execute("""
        select p.rowid id, p.*, i.creators j_creators from jstor_pairs p left join items i on i.item_id = p.j_item
        where p.j_item is not null and not p.same_venue
          and (p.o_oa = 1 or (p.o_jstor and p.o_doi in (select doi from items)))
          and max(p.j_pct, p.o_pct) >= 50 or (p.j_item is not null and not p.same_venue
          and (p.o_oa = 1 or (p.o_jstor and p.o_doi in (select doi from items))) and min(p.j_pct, p.o_pct) >= 15)
        order by min(p.j_pct, p.o_pct) desc""").fetchall()
    seen, uniq = set(), []
    for r in rows:  # a JSTOR-JSTOR pair has a row per side; triage it once
        if (r["pair_lo"], r["pair_hi"]) not in seen:
            seen.add((r["pair_lo"], r["pair_hi"]))
            uniq.append(r)
    rows = uniq
    done = {(r[0], r[1], r[2]) for r in db.execute("select pair_lo, pair_hi, j_doi from triage where model = ?", (model,))}
    rows = [r for r in rows if (r["pair_lo"], r["pair_hi"], r["j_doi"]) not in done][:limit or None]
    print(f"{len(rows)} pairs to triage with {model}")

    def other_author(doi):
        r = db.execute("select author from gi.documents where lower(doi) = ? limit 1", (doi,)).fetchone()
        return r[0] if r else None

    def other_journal(doi):
        r = db.execute("select is_part_of from items where doi = ?", (doi,)).fetchone()
        return r[0] if r else None

    def line(r):
        return json.dumps({"id": r["id"], "pct_jstor": r["j_pct"], "pct_other": r["o_pct"],
                           "jstor": {"year": r["j_year"], "title": r["j_title"], "journal": r["j_journal"],
                                     "authors": r["j_creators"]},
                           "other": {"year": r["o_year"], "title": r["o_title"], "venue": r["o_venue"] or other_journal(r["o_doi"]),
                                     "authors": other_author(r["o_doi"]), "doi": r["o_doi"]}}, ensure_ascii=False)

    api = client()
    price_in, price_out = PRICES.get(model, (4.00, 20.00))
    batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]
    packets = [[line(r) for r in b] for b in batches]

    def call(packet):
        for _ in range(5):
            try:
                return api.messages.create(
                    model=model, max_tokens=8000, system=TRIAGE_PROMPT,
                    messages=[{"role": "user", "content": "\n".join(packet)}],
                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}})
            except anthropic.RateLimitError:
                import time
                time.sleep(60)
            except anthropic.APIStatusError as e:
                return e
        return None

    spent, by_id = 0.0, {r["id"]: r for r in rows}
    now = f"{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}"
    with ThreadPoolExecutor(4) as pool:
        for resp in pool.map(call, packets):
            if resp is None or isinstance(resp, anthropic.APIStatusError):
                print(f"  batch failed: {resp}")
                continue
            u = resp.usage
            spent += u.input_tokens * price_in / 1e6 + u.output_tokens * price_out / 1e6
            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                out = json.loads(text)["pairs"]
            except (json.JSONDecodeError, KeyError):
                print(f"  unparseable batch (stop_reason {resp.stop_reason})")
                continue
            db.executemany("insert or replace into triage values (?,?,?,?,?,?,?,?)",
                           [(by_id[v["id"]]["pair_lo"], by_id[v["id"]]["pair_hi"], by_id[v["id"]]["j_doi"], model,
                             v["kind"], int(v["worth_full_text"]), v["reason"], now)
                            for v in out if v["id"] in by_id])
            db.commit()
            if spent > max_cost:
                print(f"  stopping: spent ${spent:.2f} > --max-cost")
                break
    print(f"spent ~${spent:.2f}")
    for r in db.execute("select kind, count(*), sum(worth_full_text) from triage where model = ? group by 1 order by 2 desc",
                        (model,)):
        print("  %s: %s (%s worth full text)" % tuple(r))


def index_check(state_path, out_path):
    """Before any JSTOR text: compare each downloaded later-side PDF with the index's own sampled
    5-gram hashes (gi_check_index_text.py's method) for both documents of its pair.
      later_own  share of the later document's index samples found in its PDF (is the index's text
                 for that record really this PDF?)
      jstor_in   share of the JSTOR document's index samples found in the later PDF (how much of the
                 JSTOR article, as the index holds it, the later paper contains)
    The index lemmatizes and this doesn't, so even the right text scores well under 100%; compare
    jstor_in against later_own. Both sides are cut to hashes divisible by 8, the sparsest shard rate,
    so slices sampled 1-in-4 and 1-in-8 compare alike."""
    import csv
    import hashlib
    import re

    import numpy as np
    import pymupdf

    from gi_check_index_text import REC, WORD

    db = connect()
    db.row_factory = sqlite3.Row
    db.execute("attach ? as gi", (str(gi_db.DB_PATH),))
    st = sqlite3.connect(state_path)
    st.row_factory = sqlite3.Row
    papers = Path(state_path).parent / "papers"
    pdfs = {r["doi"].lower(): papers / Path(r["file_path"]).name
            for r in st.execute("select doi, file_path from papers where status = 'downloaded'")}
    pairs = db.execute("""select p.* from jstor_pairs p join triage t using (pair_lo, pair_hi, j_doi)
                          where t.worth_full_text""").fetchall()
    pairs = [p for p in pairs if p["o_doi"] in pdfs and pdfs[p["o_doi"]].exists()]
    dkeys = {}
    for p in pairs:
        for d in (p["j_doi"], p["o_doi"]):
            dkeys[d] = [r[0] for r in db.execute("select dkey from gi.documents where lower(doi) = ?", (d,))]
    want = {k for ks in dkeys.values() for k in ks}
    loc = {}
    for s in gi_db.available_shards():
        with open(gi_db.shard_dir(s) / "docs.tsv") as f:
            for line in f:
                did, dk, *_ = line.split("\t", 3)
                if dk in want:
                    loc[dk] = (s, int(did))
    print(f"{len(pairs)} pairs, {len(want)} index documents, {len(loc)} in the shard samples", flush=True)
    samples = {k: set() for k in loc}
    for s in sorted({v[0] for v in loc.values()}):
        ids = {v[1]: k for k, v in loc.items() if v[0] == s}
        idarr = np.array(sorted(ids), dtype=np.uint32)
        for part in sorted(gi_db.shard_dir(s).glob("part_*.bin")):
            a = np.fromfile(part, dtype=REC)
            m = a[np.isin(a["d"], idarr)]
            for h, d in zip(m["h"].tolist(), m["d"].tolist()):
                if h % 8 == 0:
                    samples[ids[d]].add(h)
        print(f"shard {s} read", flush=True)

    def pdf_hashes(path):
        words = WORD.findall(" ".join(pg.get_text() for pg in pymupdf.open(path)).lower())
        hs = (int.from_bytes(hashlib.blake2b(" ".join(words[i:i + 5]).encode(), digest_size=8).digest(), "little")
              for i in range(len(words) - 4))
        return {h for h in hs if h % 8 == 0}, len(words)

    out = []
    for p in pairs:
        text, n = pdf_hashes(pdfs[p["o_doi"]])
        sj = set().union(*(samples.get(k, set()) for k in dkeys[p["j_doi"]])) if dkeys[p["j_doi"]] else set()
        so = set().union(*(samples.get(k, set()) for k in dkeys[p["o_doi"]])) if dkeys[p["o_doi"]] else set()
        pct = lambda s: round(100 * len(s & text) / len(s), 1) if s else None  # noqa: E731
        out.append({"j_doi": p["j_doi"], "o_doi": p["o_doi"], "gi_j_pct": p["j_pct"], "gi_o_pct": p["o_pct"],
                    "later_words": n, "jstor_samples": len(sj), "later_samples": len(so),
                    "later_own": pct(so), "jstor_in": pct(sj),
                    "j_year": p["j_year"], "j_title": p["j_title"], "o_year": p["o_year"], "o_title": p["o_title"],
                    "o_venue": p["o_venue"]})
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter="\t")
        w.writeheader()
        w.writerows(sorted(out, key=lambda r: -(r["jstor_in"] or 0)))
    print(f"wrote {len(out)} rows -> {out_path}")


def request(out, tiers, no_review_only):
    db = connect()
    marks = ",".join("?" * len(tiers))
    where = f"tier in ({marks}) and item_id is not null" + (" and review_required = 0" if no_review_only else "")
    ids = [r[0] for r in db.execute(f"select distinct item_id from lead_items where {where} order by item_id", tiers)]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    name = "items_t%s%s.txt" % ("".join(map(str, tiers)), "_noreview" if no_review_only else "")
    (out / name).write_text("".join(i + "\n" for i in ids), encoding="utf-8")
    print(f"{len(ids):,} item IDs -> {out / name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("load")
    p.add_argument("file", nargs="?")
    p = sub.add_parser("match")
    p.add_argument("--tiers", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    p = sub.add_parser("partners")
    p.add_argument("--email", required=True)
    p.add_argument("--tiers", type=int, nargs="+", default=[1, 2, 3, 4])
    p = sub.add_parser("screen")
    p.add_argument("--tiers", type=int, nargs="+", default=[1, 2, 3, 4])
    p = sub.add_parser("triage")
    p.add_argument("--model", default="claude-sonnet-5-5")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--max-cost", type=float, default=10.0)
    p = sub.add_parser("index-check")
    p.add_argument("--state", default=str(gi_db.DATA / "jstor_experiment" / "state.sqlite3"))
    p.add_argument("--out", default=str(gi_db.DATA / "jstor_experiment" / "index_check.tsv"))
    p = sub.add_parser("request")
    p.add_argument("--out", required=True)
    p.add_argument("--tiers", type=int, nargs="+", default=[1, 2])
    p.add_argument("--no-review-only", action="store_true")
    a = ap.parse_args()
    if a.cmd == "load":
        path = a.file or max(glob.glob(str(HERE / "jstor_metadata_*.jsonl.gz")))
        load(path)
    elif a.cmd == "match":
        match(a.tiers)
    elif a.cmd == "partners":
        partners(a.email, a.tiers)
    elif a.cmd == "screen":
        screen(a.tiers)
    elif a.cmd == "index-check":
        index_check(a.state, a.out)
    elif a.cmd == "triage":
        triage(a.model, a.limit, a.batch_size, a.max_cost)
    else:
        request(a.out, a.tiers, a.no_review_only)


if __name__ == "__main__":
    main()
