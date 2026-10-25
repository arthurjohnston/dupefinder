"""Floor experiment, calibration: Retraction Watch recall below the 15% floor. Read-only on gi.sqlite3.

rw_recall (gi_db.py) counts a plagiarism retraction (is_plagiarism = 1, original_year <= 2020,
in_slice_metadata = 1) as found when it is a side of a ranked lead. This counts, per band below 15%, the
plagiarism-retraction DOIs that are a side of a would-be lead there (gi_rank_leads.py's candidate rows:
reason '' with no shared author, or 'no metadata') and of NO ranked lead at >= 15% -- what a lower floor
would add to recall -- and writes every such pair (both RW definitions: the rw_recall one and any
is_plagiarism) to data/floor_experiment/floor.sqlite3 table rw_pairs, the calibration set.

Usage: floor_rw_recall.py
"""
import sqlite3
import time
from collections import Counter, defaultdict

import floor_common as fc
from floor_volume import fine_band


def main():
    db = fc.ro()
    plag_all = {r[0] for r in db.execute("select distinct original_doi_lower from retraction_watch "
                                          "where is_plagiarism = 1 and original_doi_lower is not null")}
    plag_recall = {r[0] for r in db.execute("select distinct original_doi_lower from retraction_watch where "
                                             "is_plagiarism = 1 and original_year <= 2020 and in_slice_metadata = 1")}
    found = set()
    for a, b in db.execute("select lower(doi_a), lower(doi_b) from leads where tier is not null"):
        found.update((a, b))
    print(f"plagiarism DOIs: {len(plag_all):,} (rw_recall definition {len(plag_recall):,}); "
          f"found at >= 15% (rw_recall def.): {len(found & plag_recall):,}", flush=True)
    start = fc.first_id_below(db, 0.15 - 1e-9)
    hi = db.execute("select max(pair_id) from pairs").fetchone()[0]
    exp = sqlite3.connect(fc.EXP_DB)
    exp.execute("drop table if exists rw_pairs")
    exp.execute("""create table rw_pairs (pair_id integer primary key, band text, doi_a text, doi_b text, pair_lo text,
                   pair_hi text, reason text, rw_side text, in_recall_def integer, already_found integer,
                   shared integer, pct_a real, pct_b real, dkey_a text, dkey_b text)""")
    first_band = {}  # plagiarism DOI -> highest sub-15 band it appears in
    t0 = time.time()
    rows_out = []
    for s in range(start, hi + 1, 2_000_000):
        for r in db.execute("""select l.pair_id, l.doi_a, l.doi_b, l.pair_lo, l.pair_hi, l.reason, l.authors,
                                      p.shared, p.pct_a, p.pct_b, p.dkey_a, p.dkey_b
                               from leads l join pairs p using (pair_id) where l.pair_id >= ? and l.pair_id < ?""",
                            (s, s + 2_000_000)):
            pid, da, dbb, plo, phi, reason, authors, shared, pa, pb, ka, kb = r
            if not (reason == "no metadata" or (reason == "" and authors == "no shared author")):
                continue
            sides = [x for x, d in (("A", da.lower()), ("B", dbb.lower())) if d in plag_all]
            if not sides:
                continue
            band = fine_band(max(pa, pb))
            for x in sides:
                d = (da if x == "A" else dbb).lower()
                first_band.setdefault(d, band)
            dd = [(da if x == "A" else dbb).lower() for x in sides]
            rows_out.append((pid, band, da, dbb, plo, phi, reason, ",".join(sides),
                             int(any(d in plag_recall for d in dd)), int(all(d in found for d in dd)), shared, pa, pb, ka, kb))
        print(f"  {s:,}: {len(rows_out):,} rows, {time.time() - t0:.0f}s", flush=True)
    exp.executemany("insert into rw_pairs values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_out)
    exp.commit()
    new = Counter(b for d, b in first_band.items() if d in plag_recall and d not in found)
    anyb = Counter(b for d, b in first_band.items() if d in plag_recall)
    print("rw_recall-definition plagiarism DOIs with a sub-15% would-be lead, by highest sub-15 band:")
    for b in ["12.5-15", "10-12.5", "7.5-10", "5-7.5", "2.5-5", "0-2.5"]:
        print(f"  {b}: {anyb[b]:,} DOIs, {new[b]:,} not found at >= 15%")


if __name__ == "__main__":
    main()
