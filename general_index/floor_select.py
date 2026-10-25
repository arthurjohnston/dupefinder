"""Floor experiment, part 3a: a random sample of sub-15% cross-author pairs, stratified by band, for full text.

Same eligibility as gi_verify.py select --per-band (filters kept, no shared author, both sides titled, not
an identical-abstract artifact), plus what the floor question needs:
  - neither side is a Retraction Watch retraction, for any reason (tiers 1/3 would be test sets only);
  - the DOI pair has NO document pair at >= 15% (else it is already above the floor: a lead or filtered);
  - not already in gi.sqlite3's fulltext (measured elsewhere);
  - one row per DOI pair.
Draws random pair_ids inside each band's pair_id range (pairs are sorted by containment, floor_common).
Reads gi.sqlite3 read-only; writes data/floor_experiment/floor.sqlite3 (table sample) and
data/floor_experiment/<run>.starting.json / <run>.keys.json for retrieve_papers.py.

Usage: floor_select.py --run floor_s1 --per-band 1200 [--seed 7]
"""
import argparse
import html
import json
import random
import sqlite3
import sys
from pathlib import Path

import floor_common as fc
from gi_filters import venue

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402

SAMPLE_BANDS = [(0, 5), (5, 10), (10, 12.5), (12.5, 15)]
SCHEMA = """
create table if not exists sample (
  run text, band text, pair_id integer, pair_lo text, pair_hi text, doi_a text, doi_b text, dkey_a text, dkey_b text,
  title_a text, title_b text, author_a text, author_b text, year_a text, year_b text,
  shared integer, sampled_a integer, sampled_b integer, pct_a real, pct_b real,
  abs_verdict text, abs_run integer, primary key (run, pair_lo, pair_hi));
create table if not exists draws (run text, band text, drawn integer, eligible integer, chosen integer,
  excl_reason text, excl_rw integer, excl_above_floor integer, excl_done integer, excl_title integer, excl_artifact integer);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--per-band", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--rw-calibration", action="store_true",
                    help="instead: every identified pair in floor_rw_recall.py's rw_pairs whose plagiarism-retraction "
                         "side is not found at >= 15% (a TEST SET, never part of the precision sample)")
    args = ap.parse_args()
    db = fc.ro()
    rng = random.Random(args.seed)
    rw = {r[0] for r in db.execute("select original_doi_lower from retraction_watch where original_doi_lower is not null")}
    done = {(r[0], r[1]) for r in db.execute("select pair_lo, pair_hi from fulltext")}
    ranges = fc.band_ranges(db)
    exp = sqlite3.connect(fc.EXP_DB)
    exp.executescript(SCHEMA)
    if exp.execute("select count(*) from sample where run = ?", (args.run,)).fetchone()[0]:
        sys.exit(f"run {args.run} already selected")
    chosen = []
    if args.rw_calibration:
        seen = set()
        for pid, in exp.execute("select pair_id from rw_pairs where already_found = 0 and reason = '' order by pair_id"):
            r = db.execute("select ld.*, p.dkey_a, p.dkey_b from lead_details ld join pairs p using (pair_id) "
                           "where ld.pair_id = ?", (pid,)).fetchone()
            key = (r["pair_lo"], r["pair_hi"])
            if key in seen or key in done or not (r["title_a"] or "").strip() or not (r["title_b"] or "").strip():
                continue
            seen.add(key)
            mx = max(r["pct_a"], r["pct_b"])
            band = next(b for b in [(0, 2.5), (2.5, 5), (5, 7.5), (7.5, 10), (10, 12.5), (12.5, 15)] if b[0] <= mx < b[1])
            chosen.append((band, r, None))
    for band in ([] if args.rw_calibration else SAMPLE_BANDS):
        lo, hi = ranges[band]
        need, seen = args.per_band, set()
        st = dict(drawn=0, eligible=0, reason=0, rw=0, above=0, done=0, title=0, artifact=0)
        while need > 0:
            ids = sorted({rng.randrange(lo, hi) for _ in range(2000)})
            st["drawn"] += len(ids)
            rows = db.execute(f"""select ld.*, p.dkey_a, p.dkey_b from lead_details ld join pairs p using (pair_id)
                                  where ld.pair_id in ({','.join('?' * len(ids))})""", ids).fetchall()
            rng.shuffle(rows)
            for r in rows:
                if need <= 0:
                    break
                if (r["reason"] or "") != "" or r["authors"] != "no shared author":
                    st["reason"] += 1
                    continue
                da, dbb = r["doi_a"].lower(), r["doi_b"].lower()
                key = (r["pair_lo"], r["pair_hi"])
                if da in rw or dbb in rw:
                    st["rw"] += 1
                    continue
                if not (r["title_a"] or "").strip() or not (r["title_b"] or "").strip():
                    st["title"] += 1
                    continue
                if key in done:
                    st["done"] += 1
                    continue
                if key in seen:
                    continue
                mx = db.execute("""select max(max(p.pct_a, p.pct_b)) from leads l join pairs p using (pair_id)
                                   where l.pair_lo = ? and l.pair_hi = ?""", key).fetchone()[0]
                if mx >= 15:
                    st["above"] += 1
                    continue
                ab = db.execute("select verdict, longest_run, containment5 from abstract_checks where doi_a = ? "
                                "and doi_b = ?", (da, dbb)).fetchone()
                if ab and ab[0] == "shared text" and (ab[2] or 0) >= 0.99 and \
                        venue({"journal": None, "doi": da}) == venue({"journal": None, "doi": dbb}):
                    st["artifact"] += 1
                    continue
                st["eligible"] += 1
                seen.add(key)
                need -= 1
                chosen.append((band, r, ab))
        bl = f"{band[0]}-{band[1]}"
        exp.execute("insert into draws values (?,?,?,?,?,?,?,?,?,?,?)",
                    (args.run, bl, st["drawn"], st["eligible"], args.per_band, st["reason"], st["rw"], st["above"],
                     st["done"], st["title"], st["artifact"]))
        print(f"band {bl}: {st}", flush=True)

    want, keys = {}, {}
    for band, r, ab in chosen:
        exp.execute("insert into sample values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (args.run, f"{band[0]}-{band[1]}", r["pair_id"], r["pair_lo"], r["pair_hi"], r["doi_a"], r["doi_b"],
                     r["dkey_a"], r["dkey_b"], r["title_a"], r["title_b"], r["author_a"], r["author_b"], r["year_a"],
                     r["year_b"], r["shared"], r["sampled_a"], r["sampled_b"], r["pct_a"], r["pct_b"],
                     ab[0] if ab else None, ab[1] if ab else None))
        for side in ("a", "b"):  # gi_verify.write_selection's retrieval entry, verbatim
            y, doi = r[f"year_{side}"], r[f"doi_{side}"].lower()
            entry = want.setdefault(doi, {
                "title": html.unescape(r[f"title_{side}"] or ""),
                "authors": [s.strip() for s in (r[f"author_{side}"] or "").split(";") if s.strip()],
                "year": int(y) if (y or "").isdigit() else None,
                **({} if doi.startswith("gi:") else {"doi": doi})})
            ks = keys.setdefault(rp.make_key(entry), [])
            if r[f"dkey_{side}"] not in ks:
                ks.append(r[f"dkey_{side}"])
    exp.commit()
    (fc.OUT / f"{args.run}.starting.json").write_text(json.dumps(list(want.values()), indent=1, ensure_ascii=False))
    (fc.OUT / f"{args.run}.keys.json").write_text(json.dumps(keys, indent=1))
    print(f"run {args.run}: {len(chosen)} pairs, {len(want)} papers "
          f"({sum(d.startswith('gi:') for d in want)} without a DOI)")


if __name__ == "__main__":
    main()
