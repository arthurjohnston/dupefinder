"""Floor experiment: print each real-overlap pair's metadata and its longest shared runs, for reading by hand.
Usage: floor_inspect.py --run floor_s1 [--verdicts 'real overlap'] [--top 6]"""
import argparse
import sqlite3
import sys
from pathlib import Path

import floor_common as fc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compare_two_papers as ctp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
ap.add_argument("--verdicts", nargs="+", default=["real overlap"])
ap.add_argument("--top", type=int, default=6)
args = ap.parse_args()
exp = sqlite3.connect(fc.EXP_DB)
exp.row_factory = sqlite3.Row
lib = sqlite3.connect(fc.OUT / f"verify_{args.run}" / "library.sqlite3")
q = f"""select r.*, s.title_a, s.title_b, s.year_a, s.year_b, s.doi_a, s.doi_b, s.pct_a idx_a, s.pct_b idx_b, s.shared
        from results r join sample s using (run, pair_lo, pair_hi) where r.run = ? and r.ft_verdict in
        ({','.join('?' * len(args.verdicts))}) order by r.band, r.ft_matched_words desc"""
for i, r in enumerate(exp.execute(q, (args.run, *args.verdicts)), 1):
    print(f"\n#{i} [{r['band']}] {r['ft_verdict']}; {r['byline']}; index {r['idx_a']}%/{r['idx_b']}% ({r['shared']} samples)")
    print(f"  A {r['doi_a']} ({r['year_a']}) {r['title_a'][:110]}  [{r['words_a']} words]")
    print(f"  B {r['doi_b']} ({r['year_b']}) {r['title_b'][:110]}  [{r['words_b']} words]")
    print(f"  full text {r['ft_pct_a']:.1f}%/{r['ft_pct_b']:.1f}%, {r['ft_matched_words']} words in {r['ft_runs']} runs, "
          f"longest {r['ft_longest']}, largest block {r['block_a']}/{r['block_b']} at {r['block_a_start']}/{r['block_b_start']}")
    wa, wb = ctp.load_paper_words(lib, r["paper_a"]), ctp.load_paper_words(lib, r["paper_b"])
    runs = ctp.find_shingle_matches(wa, wb, shingle_size=10, x_drop=3)
    for x in runs[:args.top]:
        print(f"   - {x[0]}w @A{x[5] / len(wa):.2f} B{x[7] / len(wb):.2f}: {' '.join(x[3].split())[:230]}")
