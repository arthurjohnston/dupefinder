"""The General Index pilot's one database: general_index/data/gi.sqlite3, a table per pipeline step.

  step                            script                       table(s)
  1 stream + sample a slice       gi_sample_5grams.py          slice*/part_XX.bin + docs.tsv (files, not
                                                               the DB: ~1B rows/slice, read sequentially)
    metadata for the documents    gi_db.py import-documents    documents, update_dois
  2 count pairs                   gi_pairs.py --db             pairs
  3 metadata join + filters       gi_report.py                 leads (reason, authors, band)
    abstract evidence             gi_abstract_check.py         abstracts (per-DOI cache), abstract_checks
    ranking                       gi_rank_leads.py             leads (tier, shape, ..., lead_rank, primary_pair)
  4 full text                     gi_verify.py select/measure  fulltext
    downloaded PDFs               gi_verify.py paths           local_pdfs (dkey -> local_path)
    pages                         gi_write_pages.py            fulltext.page
  5 human review                  (you, by hand)               reviews   <- EDITABLE, never rebuilt
  6 Retraction Watch check        gi_db.py import-rw, gi_recall.py   retraction_watch, view rw_recall

Every script rebuilds only its own table(s). `reviews` is created if missing and otherwise only ever
read: no script deletes from it, drops it or rewrites it, so verdicts typed into it survive every rerun.

Pair keys: `pairs`/`leads` are per document pair (pair_id; the index holds some PDFs twice, so one DOI
pair can be several document pairs). Everything DOI-level (abstract_checks, fulltext, reviews) also
carries pair_lo/pair_hi, the two lowercased DOIs sorted, as an unordered key.

Usage:
  gi_db.py init                         create any missing tables and views
  gi_db.py import-documents             slice*/docs.tsv + gi_meta.sqlite3 (meta, meta_alt) -> documents
  gi_db.py import-rw                    ../retraction_watch.csv -> retraction_watch
  gi_db.py migrate                      one-off: everything above plus the pilot's existing TSVs
"""
import argparse
import csv
import glob
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DB_PATH = DATA / "gi.sqlite3"
# Some metadata sources give 1970-01-01, the Unix epoch, for an article with no date (the index's own
# metadata for several Bangladesh/Nepal JOL journals, and OpenAlex). It means "not set", never 1970.
EPOCH_DATE = "1970-01-01"


def pub_year(date):
    """The year of a metadata date ('2012-02-21', '2009-12', 2012), or None when it's missing or the epoch."""
    s = str(date or "")
    return None if not s or s.startswith(EPOCH_DATE) else s[:4]


def year_sql(col):
    """SQL for pub_year(): the first four characters of `col`, NULL for the epoch."""
    return f"(case when {col} like '{EPOCH_DATE}%' then null else substr({col}, 1, 4) end)"
ROOT = HERE.parent

SCHEMA = """
create table if not exists documents (
    dkey text primary key, slice integer, total_5grams integer, sampled_5grams integer,
    doi text, title text, author text, journal text, pub_date text, doc_size text,
    meta_source text            -- 'slice' (publisher metadata), '2022 update' (text-extracted), null
);
create table if not exists update_dois (doi text primary key);   -- lowercased DOIs in the 2022 update
create table if not exists pairs (
    pair_id integer primary key,  -- rank by containment, then shared (gi_pairs.py output order)
    dkey_a text, dkey_b text, shared integer, sampled_a integer, sampled_b integer,
    pct_a real, pct_b real, containment real
);
create table if not exists leads (
    pair_id integer primary key, doi_a text, doi_b text, pair_lo text, pair_hi text,
    reason text, authors text, band text,
    tier integer, shape text, book_side text, abstracts text, abs_run integer, retraction_watch text,
    lead_rank integer,            -- order within the ranked list (tier, shape, ...)
    primary_pair integer          -- 1 for the first document pair of each DOI pair among the ranked leads
);
create table if not exists abstracts (
    doi text primary key, words integer, abstract text, fetched_at text
);
create table if not exists abstract_checks (
    doi_a text, doi_b text, pair_lo text, pair_hi text, verdict text, longest_run integer,
    containment5 real, words_a integer, words_b integer, checked_in text,
    primary key (doi_a, doi_b)
);
create table if not exists fulltext (
    pair_lo text, pair_hi text, doi_a text, doi_b text, run text, page_number integer,
    retrieved text, ft_verdict text, ft_pct_a real, ft_pct_b real, ft_matched_words integer,
    ft_longest integer, ft_runs integer, wrong_pdf text, byline text, page text,
    library text, paper_a integer, paper_b integer, year_a text, year_b text,
    primary key (pair_lo, pair_hi)
);
-- Downloaded PDFs, keyed by the index's document key so a document with no DOI has one too. doi is the
-- document's own DOI, or for a no-DOI document the one retrieval resolved from its title (may be null).
-- Not rebuilt by import-documents.
create table if not exists local_pdfs (
    dkey text primary key, doi text, local_path text not null, run text, recorded_at text
);
create index if not exists idx_local_pdfs_doi on local_pdfs(lower(doi));
create table if not exists reviews (
    review_id integer primary key, doi_a text, doi_b text, pair_lo text, pair_hi text,
    verdict text,                 -- 'same paper', 'copying', 'not copying', 'legitimate reuse', 'unsure'
    note text, reviewed_at text,
    unique (pair_lo, pair_hi)
);
create table if not exists review_flags (   -- your extra judgements on a pair, beside the verdict
    pair_lo text, pair_hi text,
    flag text,                    -- 'paper mill': you think both papers came from a paper mill
    flagged_at text,
    primary key (pair_lo, pair_hi, flag)
);
create index if not exists idx_documents_doi on documents(lower(doi));
create index if not exists idx_leads_tier on leads(tier);
create index if not exists idx_leads_pair on leads(pair_lo, pair_hi);
create index if not exists idx_ac_pair on abstract_checks(pair_lo, pair_hi);

drop view if exists lead_details;
create view lead_details as
select l.*, p.shared, p.pct_a, p.pct_b, p.sampled_a, p.sampled_b, p.containment,
       da.title as title_a, (case when da.pub_date like '1970-01-01%' then null else substr(da.pub_date, 1, 4) end) as year_a,
       da.author as author_a, da.meta_source as source_a,
       db.title as title_b, (case when db.pub_date like '1970-01-01%' then null else substr(db.pub_date, 1, 4) end) as year_b,
       db.author as author_b, db.meta_source as source_b
from leads l join pairs p using (pair_id)
left join documents da on da.dkey = p.dkey_a left join documents db on db.dkey = p.dkey_b;

drop view if exists to_review;
create view to_review as
select f.*, max(f.ft_pct_a, f.ft_pct_b) as ft_pct_max from fulltext f
where f.ft_verdict = 'real overlap' and f.byline = 'no byline overlap'
  and not exists (select 1 from reviews r where r.pair_lo = f.pair_lo and r.pair_hi = f.pair_hi)
order by ft_pct_max desc, f.ft_matched_words desc;

create table if not exists retraction_watch (
    record_id integer, file_order integer primary key, original_doi text, original_doi_lower text,
    retraction_doi text, title text, journal text, reason text, is_plagiarism integer, original_date text,
    original_year integer, retraction_date text,
    in_slice_metadata integer,    -- DOI has a row in the slice metadata (gi_meta.sqlite3 meta)
    in_documents integer          -- DOI belongs to a document with 5-gram data (any metadata source)
);
create index if not exists idx_rw_doi on retraction_watch(original_doi_lower);

drop view if exists rw_recall;
create view rw_recall as
with rw as (select distinct original_doi_lower as doi from retraction_watch
            where is_plagiarism = 1 and original_year <= 2020 and in_slice_metadata = 1),
sides as (select lower(doi_a) as doi, abstracts from leads where tier is not null
          union all select lower(doi_b), abstracts from leads where tier is not null),
hits as (select doi, max(case when abstracts like 'partner unidentified%' then 0 else 1 end) as identified
         from sides where doi in (select doi from rw) group by doi)
select (select count(*) from rw) as plagiarism_papers,
       (select count(*) from hits where identified = 1) as identified_partner,
       (select count(*) from hits where identified = 0) as unidentified_only,
       (select count(*) from hits) as found;
"""


def connect(path=DB_PATH):
    db = sqlite3.connect(path, timeout=300)
    db.row_factory = sqlite3.Row
    db.execute("pragma journal_mode = wal")
    db.executescript(SCHEMA)
    return db


def pair_lo_hi(a, b):
    lo, hi = sorted((a.lower(), b.lower()))
    return lo, hi


def doc_meta(row):
    """A documents row as the metadata dict gi_filters expects (source 'meta' / 'alt')."""
    if row is None or not row["doi"]:
        return None
    return {"dkey": row["dkey"], "doi": row["doi"], "title": row["title"], "author": row["author"],
            "journal": row["journal"], "pub_date": row["pub_date"], "doc_size": row["doc_size"],
            "source": "alt" if row["meta_source"] == "2022 update" else "meta"}


def shard_dir(k):
    """A shard's sample directory: the 1-in-8 copy (slice{k}_s8, made by gi_blocks.py thin) when one
    exists, else slice{k} (shards 4-15 were streamed at 1 in 8 directly)."""
    s8 = DATA / f"slice{k}_s8"
    return s8 if s8.exists() else DATA / f"slice{k}"


def available_shards():
    """Shards whose docs.tsv exists (a shard's docs.tsv is written when its stream finishes)."""
    return [k for k in range(16) if (shard_dir(k) / "docs.tsv").exists()]


def import_documents(db, meta_db=DATA / "gi_meta.sqlite3", slices=None):
    """documents from slice*/docs.tsv + gi_meta.sqlite3, with gi_report.py's (--use-alt) metadata rules:
    slice metadata when it has a DOI; else a 2022-update row with a title, replaced by the publisher
    record when the slice metadata has that DOI; else nothing. 2022-update dates are blank (they are the
    PDF's date, not the publication date)."""
    db.execute("delete from documents")
    db.execute("delete from update_dois")
    db.execute("attach database ? as gm", (str(meta_db),))
    db.execute("create temp table docs_in (dkey text primary key, slice integer, total integer, sampled integer)")
    for s in (available_shards() if slices is None else [int(x) for x in slices]):
        with open(shard_dir(s) / "docs.tsv") as f:
            db.executemany("insert or ignore into docs_in values (?, ?, ?, ?)",
                           ((p[1], s, int(p[2]), int(p[3])) for p in (l.rstrip("\n").split("\t") for l in f)))
        print(f"shard {s} docs.tsv loaded ({shard_dir(s).name})", flush=True)
    db.execute("""
        insert into documents
        select d.dkey, d.slice, d.total, d.sampled,
          case when m.doi is not null then m.doi
               when a.title is not null and a.doi is not null and pub.doi is not null then pub.doi
               when a.title is not null then coalesce(a.doi, 'gi:' || d.dkey) end,
          case when m.doi is not null then m.title when a.title is not null and a.doi is not null and pub.doi is not null
               then pub.title when a.title is not null then a.title end,
          case when m.doi is not null then m.author when a.title is not null and a.doi is not null and pub.doi is not null
               then pub.author when a.title is not null then a.author end,
          case when m.doi is not null then m.journal when a.title is not null and a.doi is not null and pub.doi is not null
               then pub.journal end,
          nullif(case when m.doi is not null then m.pub_date when a.title is not null and a.doi is not null and pub.doi is not null
               then pub.pub_date end, '1970-01-01'),   -- the epoch means no date (EPOCH_DATE)
          case when m.doi is not null then m.doc_size when a.title is not null and a.doi is not null and pub.doi is not null
               then pub.doc_size when a.title is not null then a.doc_size end,
          case when m.doi is not null then 'slice' when a.title is not null and a.doi is not null and pub.doi is not null
               then 'slice' when a.title is not null then '2022 update' end
        from docs_in d
        left join gm.meta m on m.dkey = d.dkey
        left join gm.meta_alt a on a.dkey = d.dkey and m.doi is null
        left join gm.meta pub on pub.rowid = (select rowid from gm.meta x
                                               where a.doi is not null and lower(x.doi) = lower(a.doi) limit 1)
    """)
    db.execute("insert or ignore into update_dois select lower(doi) from gm.meta_alt where doi is not null")
    db.commit()
    db.execute("detach database gm")
    n = db.execute("select count(*) from documents").fetchone()[0]
    print(f"documents: {n:,}", flush=True)


def import_rw(db, csv_path=ROOT / "retraction_watch.csv", meta_db=DATA / "gi_meta.sqlite3"):
    """Derived data, so rebuilt outright. file_order keeps the CSV's order: when a DOI has several
    records, the ranking uses the last one, as gi_rank_leads.py always has."""
    db.execute("drop view if exists rw_recall")
    db.execute("drop table if exists retraction_watch")
    db.executescript(SCHEMA)
    rows = []
    with open(csv_path, encoding="latin-1") as f:
        for i, r in enumerate(csv.DictReader(f)):
            doi = (r["OriginalPaperDOI"] or "").strip()
            y = (r["OriginalPaperDate"] or "").split("/")[-1][:4]
            rid = (r.get("Record ID") or "").strip()
            rows.append((int(rid) if rid.isdigit() else None, i, doi, doi.lower() if doi.lower().startswith("10.") else None,
                         r["RetractionDOI"], r["Title"], r["Journal"], r["Reason"],
                         int("Plagiarism" in (r["Reason"] or "")), r["OriginalPaperDate"],
                         int(y) if y.isdigit() else None, r["RetractionDate"]))
    db.executemany("insert or replace into retraction_watch values (?,?,?,?,?,?,?,?,?,?,?,?,null,null)", rows)
    # One indexed lookup per DOI. (A correlated "exists (... where lower(doi) = outer.col)" scanned the
    # whole lower(doi) index once per row here -- 72k rows x 26M index entries.)
    gm = sqlite3.connect(meta_db)
    dois = sorted({r[3] for r in rows if r[3]})
    in_meta = {d for d in dois if gm.execute("select 1 from meta where lower(doi) = ? limit 1", (d,)).fetchone()}
    in_docs = {d for d in dois if db.execute("select 1 from documents where lower(doi) = ? limit 1", (d,)).fetchone()}
    db.executemany("update retraction_watch set in_slice_metadata = ?, in_documents = ? where original_doi_lower = ?",
                   ((int(d in in_meta), int(d in in_docs), d) for d in dois))
    db.commit()
    print(f"retraction_watch: {len(rows):,} records", flush=True)


def _tsv(path):
    with open(path) as f:
        yield from csv.DictReader(f, delimiter="\t")


def import_pairs_tsv(db, path):
    db.execute("delete from pairs")
    rows = ((i, r["dkey_a"], r["dkey_b"], int(r["shared"]), int(r["sampled_a"]), int(r["sampled_b"]),
             float(r["pct_a"]), float(r["pct_b"]), float(r["containment"])) for i, r in enumerate(_tsv(path), 1))
    db.executemany("insert into pairs values (?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    print(f"pairs: {db.execute('select count(*) from pairs').fetchone()[0]:,}", flush=True)


def import_abstract_checks(db, paths):
    """Pair-level verdicts from *.abstracts.tsv, later files overriding earlier ones (the order
    gi_rank_leads.py --abstracts used). Per-DOI word counts go into the abstracts cache; the
    abstract text itself was never kept by the TSV-era runs, so those cache rows have abstract = null."""
    for path in paths:
        name = os.path.basename(path)
        rows, words = [], {}
        for r in _tsv(path):
            if r["abs_verdict"] == "no abstract":
                continue
            lo, hi = pair_lo_hi(r["doi_a"], r["doi_b"])
            rows.append((r["doi_a"].lower(), r["doi_b"].lower(), lo, hi, r["abs_verdict"],
                         int(r["abs_longest_run"] or 0), float(r["abs_containment5"] or 0),
                         int(r["abs_words_a"] or 0), int(r["abs_words_b"] or 0), name))
            words[r["doi_a"].lower()] = int(r["abs_words_a"] or 0)
            words[r["doi_b"].lower()] = int(r["abs_words_b"] or 0)
        db.executemany("insert or replace into abstract_checks values (?,?,?,?,?,?,?,?,?,?)", rows)
        db.executemany("insert or ignore into abstracts (doi, words, abstract, fetched_at) values (?, ?, null, ?)",
                       ((d, w, f"before 2026-10-06 ({name})") for d, w in words.items()))
        print(f"abstract_checks <- {name}: {len(rows):,}", flush=True)
    db.commit()


def import_fulltext(db, verified, real_overlap, pages_index, library, run):
    byline, page, number, orient = {}, {}, {}, {}
    for r in _tsv(real_overlap):
        byline.setdefault(pair_lo_hi(r["doi_a"], r["doi_b"]), r.get("byline"))
    if Path(pages_index).exists():
        for r in _tsv(pages_index):
            k = pair_lo_hi(r["doi_a"], r["doi_b"])
            if r.get("page"):
                page[k] = r["page"]
            number.setdefault(k, int(r["n"]))
            orient.setdefault(k, r["doi_a"])
    lib = sqlite3.connect(library)
    ids = {d.lower(): (i, y) for i, d, y in lib.execute("select id, doi, year from papers where doi is not null")}
    # Every selected pair, retrieved or not; where a DOI pair appears twice (the index holds some PDFs
    # twice), the row with a verdict wins over one without, else the first.
    chosen = {}
    for r in _tsv(verified):
        k = pair_lo_hi(r["doi_a"], r["doi_b"])
        if k not in chosen or (r.get("verdict") and not chosen[k].get("verdict")):
            chosen[k] = r
    seen = set(chosen)
    for k, r in chosen.items():
        if k in orient and orient[k].lower() != r["doi_a"].lower():
            # keep the existing page's A/B order: swap the row (and its per-side figures) to match
            r = {**r, "doi_a": r["doi_b"], "doi_b": r["doi_a"],
                 "real_pct_a": r["real_pct_b"], "real_pct_b": r["real_pct_a"]}
        pa, ya = ids.get(r["doi_a"].lower(), (None, None))
        pb, yb = ids.get(r["doi_b"].lower(), (None, None))
        num = lambda v, c=float: c(v) if v not in (None, "") else None
        db.execute("insert or replace into fulltext values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (k[0], k[1], r["doi_a"], r["doi_b"], run, number.get(k), r.get("retrieved") or None, r["verdict"] or None,
                    num(r["real_pct_a"]), num(r["real_pct_b"]), num(r["matched_words"], int),
                    num(r["longest"], int), None, r.get("wrong_pdf") or None, byline.get(k), page.get(k),
                    str(library), pa, pb, str(ya) if ya else None, str(yb) if yb else None))
    db.commit()
    print(f"fulltext <- {os.path.basename(verified)}: {len(seen)} pairs", flush=True)


def import_reviews(db, path):
    """Adds reviewed_pairs.tsv rows that aren't in `reviews` yet; never changes or removes existing rows."""
    n = 0
    for r in _tsv(path):
        lo, hi = pair_lo_hi(r["doi_a"], r["doi_b"])
        n += db.execute("insert or ignore into reviews (doi_a, doi_b, pair_lo, pair_hi, verdict, note, reviewed_at) "
                        "values (?,?,?,?,?,?,?)", (r["doi_a"], r["doi_b"], lo, hi, r["verdict"], r.get("note"),
                                                   r.get("reviewed"))).rowcount
    db.commit()
    print(f"reviews: {n} added", flush=True)


def reviewed_same_paper(db):
    return {(r["pair_lo"], r["pair_hi"]) for r in
            db.execute("select pair_lo, pair_hi from reviews where lower(trim(verdict)) = 'same paper'")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["init", "import-documents", "import-rw", "migrate", "migrate-rest"])
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()
    db = connect(args.db)
    if args.command == "import-documents":
        import_documents(db)
    elif args.command == "import-rw":
        import_rw(db)
    elif args.command in ("migrate", "migrate-rest"):
        if args.command == "migrate":
            import_documents(db)
            import_pairs_tsv(db, DATA / "pairs_s0123.tsv")
        import_rw(db)
        # Same order (later overrides earlier) as the gi_rank_leads.py --abstracts run that produced
        # leads_ranked_s0123_alt.tsv, so the migrated tiers reproduce it.
        import_abstract_checks(db, [str(DATA / f) for f in (
            "leads_s0123_alt.abstracts.tsv", "leads_s01_alt.abstracts.tsv", "leads_s01.abstracts.tsv",
            "leads_s1.abstracts.tsv")])
        import_fulltext(db, DATA / "leads12_s0123.verified.tsv", DATA / "leads12_s0123.real_overlap.tsv",
                        DATA / "fulltext_pages_s0123" / "_index.tsv",
                        DATA / "verify_leads12_s0123" / "library.sqlite3", "leads12_s0123")
        import_reviews(db, DATA / "reviewed_pairs.tsv")
    print("ok")


if __name__ == "__main__":
    main()
