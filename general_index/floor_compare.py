"""Floor experiment, comparison: the existing random tier-4 samples (strat_s16a, strat4_s16) by band, from
gi.sqlite3's fulltext (read-only), with floor_measure.py's run-shape metrics recomputed on their libraries
(opened read-only), so a 15-30% real pair can be compared with a sub-15% one.

Writes data/floor_experiment/floor.sqlite3 table compare. Usage: floor_compare.py
"""
import sqlite3

import floor_common as fc
import floor_measure as fm


def band_of(mx):
    for lo, hi in [(0, 5), (5, 10), (10, 12.5), (12.5, 15), (15, 30), (30, 50), (50, 70), (70, 90), (90, 101)]:
        if lo <= mx < hi:
            return f"{lo}-{hi}"


def main():
    db = fc.ro()
    exp = sqlite3.connect(fc.EXP_DB)
    exp.execute("drop table if exists compare")
    exp.execute("""create table compare (run text, pair_lo text, pair_hi text, band text, retrieved text, ft_verdict text,
                   byline text, ft_pct_a real, ft_pct_b real, ft_matched_words integer, ft_longest integer, ft_runs integer,
                   words_a integer, words_b integer, runs_ge30 integer, runs_ge50 integer, runs_ge100 integer,
                   block_a integer, block_b integer, block_a_start real, block_b_start real, longest_text text,
                   rw integer)""")
    rw = {r[0] for r in db.execute("select original_doi_lower from retraction_watch where original_doi_lower is not null")}
    libs = {}
    for f in db.execute("select * from fulltext where run in ('strat_s16a', 'strat4_s16')").fetchall():
        mx = db.execute("select max(max(p.pct_a, p.pct_b)) from leads l join pairs p using (pair_id) "
                        "where l.pair_lo = ? and l.pair_hi = ?", (f["pair_lo"], f["pair_hi"])).fetchone()[0]
        row = dict(run=f["run"], pair_lo=f["pair_lo"], pair_hi=f["pair_hi"], band=band_of(mx), retrieved=f["retrieved"],
                   ft_verdict=f["ft_verdict"], byline=f["byline"], ft_pct_a=f["ft_pct_a"], ft_pct_b=f["ft_pct_b"],
                   ft_matched_words=f["ft_matched_words"], ft_longest=f["ft_longest"], ft_runs=f["ft_runs"],
                   rw=int(f["pair_lo"] in rw or f["pair_hi"] in rw))
        if f["ft_verdict"] == "real overlap" and f["library"]:
            if f["library"] not in libs:
                libs[f["library"]] = sqlite3.connect(f"file:{f['library']}?mode=ro", uri=True)
            stats, _ = fm.check(libs[f["library"]], f["paper_a"], f["paper_b"])
            for k in ("words_a", "words_b", "runs_ge30", "runs_ge50", "runs_ge100", "block_a", "block_b",
                      "block_a_start", "block_b_start", "longest_text"):
                row[k] = stats.get(k)
        cols = list(row)
        exp.execute(f"insert into compare ({','.join(cols)}) values ({','.join('?' * len(cols))})", [row[c] for c in cols])
    exp.commit()
    for r in exp.execute("""select run, band, count(*), sum(retrieved='AB'), sum(ft_verdict='real overlap'),
                            sum(ft_verdict='real overlap' and byline='no byline overlap'), sum(ft_verdict='wrong pdf'),
                            sum(ft_verdict='no real overlap'), sum(rw) from compare group by 1, 2 order by 1, 2"""):
        print(r)


if __name__ == "__main__":
    main()
