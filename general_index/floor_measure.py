"""Floor experiment, part 3c: measure the sample's pairs whose two PDFs were both retrieved.

Same check as gi_verify.py measure (rank_paper_pairs.text_check settings: 10-word exact shingles, lockstep
X-drop 3, min 300 words per document; wrong_pdf_side(); verdict 'real overlap' at >= 300 matched words; the
byline check on the first paragraphs + PDF page 1, gi_verify.opening/surnames), but:
  - writes to data/floor_experiment/floor.sqlite3 (table results), never to gi.sqlite3;
  - finds each side's PDF through the run's own state.sqlite3 + keys.json, not local_pdfs;
  - no --fix-years (that is OpenAlex);
  - also records where the shared text sits: run lengths, the largest contiguous covered block per side
    (runs merged when they are within 30 words), and the covered span's share of each document, so
    "a section lifted into a long paper" can be told from scattered stock phrasing.

Usage: floor_measure.py --run floor_s1
"""
import argparse
import argparse as _ap
import json
import re
import sqlite3
import sys
from pathlib import Path

import floor_common as fc
import gi_verify as gv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compare_two_papers as ctp  # noqa: E402
import rank_paper_pairs as rpp  # noqa: E402

SCHEMA = """
create table if not exists results (
  run text, pair_lo text, pair_hi text, band text, retrieved text, paper_a integer, paper_b integer,
  words_a integer, words_b integer, ft_verdict text, ft_pct_a real, ft_pct_b real, ft_matched_words integer,
  ft_longest integer, ft_runs integer, runs_ge30 integer, runs_ge50 integer, runs_ge100 integer,
  block_a integer, block_b integer, block_a_start real, block_b_start real, wrong_pdf text, byline text,
  longest_text text, primary key (run, pair_lo, pair_hi));
"""
CHECK = _ap.Namespace(min_doc_words=300, shingle_size=10, x_drop=3, gap_open=None,
                      gap_extend=ctp.DEFAULT_GAP_EXTEND, max_gap=ctp.DEFAULT_MAX_GAP)


def blocks(spans, gap=30):
    """Merge [start, end) word spans within `gap` words of each other; return the largest merged block's
    (covered words in it, its start)."""
    best, cur = (0, 0), None
    for s, e in sorted(spans):
        if cur and s <= cur[1] + gap:
            cur = [cur[0], max(cur[1], e), cur[2] + (max(cur[1], e) - max(cur[1], s))]
        else:
            if cur and cur[2] > best[0]:
                best = (cur[2], cur[0])
            cur = [s, e, e - s]
    if cur and cur[2] > best[0]:
        best = (cur[2], cur[0])
    return best


def check(lib, a, b):
    words_a, words_b = ctp.load_paper_words(lib, a), ctp.load_paper_words(lib, b)
    out = dict(words_a=len(words_a), words_b=len(words_b))
    if len(words_a) < CHECK.min_doc_words or len(words_b) < CHECK.min_doc_words:
        return out, None
    runs = ctp.find_shingle_matches(words_a, words_b, shingle_size=CHECK.shingle_size, x_drop=CHECK.x_drop)
    if not runs:
        return out, None
    ca, cb = set(), set()
    for r in runs:
        ca.update(range(r[5], r[6]))
        cb.update(range(r[7], r[8]))
    ba, sa = blocks([(r[5], r[6]) for r in runs])
    bb, sb = blocks([(r[7], r[8]) for r in runs])
    out.update(ft_pct_a=100 * len(ca) / len(words_a), ft_pct_b=100 * len(cb) / len(words_b),
               ft_matched_words=len(ca), ft_longest=runs[0][0], ft_runs=len(runs),
               runs_ge30=sum(r[0] >= 30 for r in runs), runs_ge50=sum(r[0] >= 50 for r in runs),
               runs_ge100=sum(r[0] >= 100 for r in runs), block_a=ba, block_b=bb,
               block_a_start=round(sa / len(words_a), 3), block_b_start=round(sb / len(words_b), 3),
               longest_text=" ".join(runs[0][3].split())[:400])
    return out, len(ca)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--min-words", type=int, default=300)
    args = ap.parse_args()
    work = fc.OUT / f"verify_{args.run}"
    exp = sqlite3.connect(fc.EXP_DB)
    exp.row_factory = sqlite3.Row
    exp.executescript(SCHEMA)
    keys = json.loads((fc.OUT / f"{args.run}.keys.json").read_text())
    dkey_to_state = {dk: k for k, dks in keys.items() for dk in dks}
    st = sqlite3.connect(work / "state.sqlite3")
    path_of = {k: f for k, f in st.execute("select key, file_path from papers where status = 'downloaded' "
                                           "and file_path is not null")}
    lib = sqlite3.connect(work / "library.sqlite3")
    by_path = {str((work / f).resolve()): i for i, f in lib.execute("select id, file_path from papers")}

    def pid(dkey):
        f = path_of.get(dkey_to_state.get(dkey))
        if not f:
            return None
        p = str((work / f).resolve())
        return by_path.get(p) if Path(p).exists() else None

    counts = {}
    rows = exp.execute("select * from sample where run = ?", (args.run,)).fetchall()
    for r in rows:
        a, b = pid(r["dkey_a"]), pid(r["dkey_b"])
        upd = dict(run=args.run, pair_lo=r["pair_lo"], pair_hi=r["pair_hi"], band=r["band"],
                   retrieved=f"{'A' if a else '-'}{'B' if b else '-'}", paper_a=a, paper_b=b)
        if a and b:
            stats, words = check(lib, a, b)
            upd.update(stats)
            words = words or 0
            if words == 0:
                upd.update(ft_pct_a=0, ft_pct_b=0, ft_matched_words=0, ft_longest=0, ft_runs=0)
            upd["wrong_pdf"] = rpp.wrong_pdf_side(lib, a, b) or None
            upd["ft_verdict"] = ("wrong pdf" if upd["wrong_pdf"] else
                                 "real overlap" if words >= args.min_words else "no real overlap")
            oa, ob, sa, sb = gv.opening(lib, a), gv.opening(lib, b), gv.surnames(lib, a), gv.surnames(lib, b)
            hit = sorted({s for s in sa if re.search(r"\b" + s + r"\b", ob)} |
                         {s for s in sb if re.search(r"\b" + s + r"\b", oa)})
            upd["byline"] = "byline overlap: " + ",".join(hit) if hit else "no byline overlap"
        v = upd.get("ft_verdict") or "not both retrieved"
        counts[v] = counts.get(v, 0) + 1
        cols = list(upd)
        exp.execute(f"insert or replace into results ({','.join(cols)}) values ({','.join('?' * len(cols))})",
                    [upd[c] for c in cols])
    exp.commit()
    print(f"run {args.run}: {len(rows)} pairs: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
