"""Step 3: attach metadata to every pair, apply gi_filters, write the `leads` table (gi.sqlite3).

Reads `pairs` (gi_pairs.py --db) and `documents` (gi_db.py import-documents), plus `reviews`: a pair
reviewed as "same paper" gets reason "same work (reviewed)". Writes `leads`: one row per pair at or
above --min-containment with doi_a/doi_b (the documents' DOIs, or "gi:<dkey>" for a document with a
title but no DOI, or no metadata at all), reason (empty = worth checking), author relation and band.
Ranking columns (tier, ...) are left empty for gi_rank_leads.py.

documents already carries the metadata rules: slice (publisher) metadata when it has a DOI; else a
2022-update title, replaced by the publisher record when the slice metadata has the same DOI; else
nothing. 2022-update dates are blank (they are the PDF's date, not the publication date).

Bands are by the larger side's share (the smaller document's share of itself found in the other).

Pairs are classified in parallel: pair_id ranges (--chunk) go to --workers processes, each reading the
database read-only; the main process writes leads. Same rules and output as the serial version.

Usage: gi_report.py [--db data/gi.sqlite3] [--min-containment 0] [--min-samples 50] [--tsv OUT.tsv]
                    [--workers N] [--chunk 250000]
--tsv also writes the pre-database report TSV (same columns, too-short pairs left out) for scripts
or diffs that still want it.
"""
import argparse
import csv
import os
import sqlite3
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import gi_db
from gi_filters import author_relation, classify_pair

BANDS = [(90, 101, ">=90%"), (50, 90, "50-90%"), (15, 50, "15-50%"), (0, 15, "<15%")]
TSV_COLS = ["containment", "pct_a", "pct_b", "shared", "sampled_a", "sampled_b", "reason", "authors",
            "band", "doi_a", "year_a", "title_a", "author_a", "doi_b", "year_b", "title_b", "author_b"]


def band(pct):
    return next(label for lo, hi, label in BANDS if lo <= pct < hi)


def _init_worker(db_path, same_paper):
    global _DB, _SAME_PAPER
    _DB = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=300)  # read-only: main writes leads
    _DB.row_factory = sqlite3.Row
    _SAME_PAPER = same_paper


def _report_range(job):
    """Classify the pairs with pair_id in [lo, hi). Runs in a worker process; returns the leads rows,
    the TSV rows (if wanted) and the counters, for the main process to write."""
    lo, hi, min_containment, min_samples, want_tsv = job
    doc = lambda k: _DB.execute("select * from documents where dkey = ?", (k,)).fetchone()
    reasons, kept_by, out_rows, tsv_rows = Counter(), Counter(), [], []
    for p in _DB.execute("select * from pairs where pair_id >= ? and pair_id < ? and containment >= ? "
                         "order by pair_id", (lo, hi, min_containment)):
        sa, sb = p["sampled_a"], p["sampled_b"]
        bd = band(max(p["pct_a"], p["pct_b"]))
        a, b = gi_db.doc_meta(doc(p["dkey_a"])), gi_db.doc_meta(doc(p["dkey_b"]))
        if min(sa, sb) < min_samples:
            reasons["too short"] += 1
            da = a["doi"] if a else f"gi:{p['dkey_a']}"
            dbb = b["doi"] if b else f"gi:{p['dkey_b']}"
            out_rows.append((p["pair_id"], da, dbb, *gi_db.pair_lo_hi(da, dbb), "too short", None, bd))
            continue
        if not (a and b):
            # Kept, not dropped: a strong match against an unidentified document is still a lead when
            # the other side is known (e.g. a retracted paper).
            reasons["no metadata"] += 1
            blank = lambda m, k: m or {"doi": f"gi:{k}", "title": "", "author": "", "pub_date": ""}
            a, b = blank(a, p["dkey_a"]), blank(b, p["dkey_b"])
            reason, rel = "no metadata", "authors unknown"
        else:
            reason = classify_pair(a, b, sa, sb, p["pct_a"], p["pct_b"])
            if gi_db.pair_lo_hi(a["doi"], b["doi"]) in _SAME_PAPER:
                reason = "same work (reviewed)"
            rel = author_relation(a, b)
            reasons[reason or "kept"] += 1
            if not reason:
                kept_by[(bd, rel)] += 1
        out_rows.append((p["pair_id"], a["doi"], b["doi"], *gi_db.pair_lo_hi(a["doi"], b["doi"]), reason, rel, bd))
        if want_tsv:
            tsv_rows.append([f"{p['containment']:.3f}", f"{p['pct_a']:.1f}", f"{p['pct_b']:.1f}", p["shared"], sa, sb,
                             reason, rel, bd, a["doi"], gi_db.pub_year(a["pub_date"]) or "", a["title"], a["author"],
                             b["doi"], gi_db.pub_year(b["pub_date"]) or "", b["title"], b["author"]])
    return out_rows, tsv_rows, reasons, kept_by


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--min-containment", type=float, default=0)
    ap.add_argument("--min-samples", type=int, default=50,
                    help="pairs where either side has fewer sampled 5-grams get reason 'too short'")
    ap.add_argument("--tsv", help="also write the pre-database report TSV here")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 4),
                    help="worker processes classifying pair_id ranges in parallel (default: cores - 4)")
    ap.add_argument("--chunk", type=int, default=250_000, help="pair_ids per job")
    args = ap.parse_args()

    db = gi_db.connect(args.db)
    same_paper = gi_db.reviewed_same_paper(db)
    db.execute("delete from leads")
    db.commit()
    lo, hi = db.execute("select min(pair_id), max(pair_id) from pairs").fetchone()
    jobs = [(i, i + args.chunk, args.min_containment, args.min_samples, bool(args.tsv))
            for i in range(lo or 0, (hi or 0) + 1, args.chunk)]

    # Rows are staged in a side file while the workers read, then copied into leads in one pass. Writing
    # leads into the database the workers are reading starves the WAL checkpoint: with 10 readers there
    # is always one on an old snapshot, so the WAL can never restart. At 16 shards (2026-10-08) it grew
    # ~0.6 GB per chunk (66 GB by chunk 130 of 469) and every read got slower as the WAL index grew.
    stage_path = f"{args.db}.leads_stage"
    if os.path.exists(stage_path):
        os.remove(stage_path)
    stage = sqlite3.connect(stage_path)
    stage.execute("pragma journal_mode = off")
    stage.execute("pragma synchronous = off")
    stage.execute("create table s (pair_id integer primary key, doi_a, doi_b, pair_lo, pair_hi, reason, authors, band)")

    reasons, kept_by = Counter(), Counter()
    tsv = open(args.tsv, "w") if args.tsv else None
    w = csv.writer(tsv, delimiter="\t") if tsv else None
    if w:
        w.writerow(TSV_COLS)
    t0 = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init_worker, initargs=(args.db, same_paper)) as pool:
        # map() keeps job order, so the TSV comes out in pair_id order as before
        for n, (rows, tsv_rows, r, k) in enumerate(pool.map(_report_range, jobs), 1):
            stage.executemany("insert into s values (?,?,?,?,?,?,?,?)", rows)
            stage.commit()
            if w:
                w.writerows(tsv_rows)
            reasons.update(r)
            kept_by.update(k)
            if n % 10 == 0 or n == len(jobs):
                print(f"  {n}/{len(jobs)} chunks, {time.time() - t0:.0f}s", flush=True)
    if tsv:
        tsv.close()
    stage.close()
    # No readers now. Indexes are dropped and rebuilt after: a sorted build instead of 117M random inserts.
    t1 = time.time()
    db.execute("drop index if exists idx_leads_tier")
    db.execute("drop index if exists idx_leads_pair")
    db.execute("attach database ? as st", (stage_path,))
    db.execute("insert into leads (pair_id, doi_a, doi_b, pair_lo, pair_hi, reason, authors, band) "
               "select * from st.s order by pair_id")
    db.commit()
    db.execute("detach database st")
    db.executescript(gi_db.SCHEMA)  # recreates the two indexes
    db.execute("pragma wal_checkpoint(truncate)")
    os.remove(stage_path)
    print(f"  leads written from the stage file, {time.time() - t1:.0f}s", flush=True)

    lines =[f"pairs at containment >= {args.min_containment} by reason:"]
    lines += [f"  {k:<18} {v:>9,}" for k, v in reasons.most_common()]
    lines.append("kept pairs by band of max(pct_a, pct_b) and author relation:")
    for _, _, label in BANDS:
        lines.append(f"  {label:<7} " + "  ".join(f"{rel}: {kept_by[(label, rel)]:,}"
                                                   for rel in ("no shared author", "shared author",
                                                               "authors unknown")))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
