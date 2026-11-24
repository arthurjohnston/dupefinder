"""Score a copy-screen run against data/copy_screen/key.json.

    python3 experiments/copy_screen_score.py v0 dev        # results/v0/dev_*.jsonl
"""
import json, re, sys
from pathlib import Path

GI = Path(__file__).resolve().parent.parent
D = GI / "data" / "copy_screen"
run, split = sys.argv[1], sys.argv[2]
key = {k.split("/")[1]: v for k, v in json.loads((D / "key.json").read_text()).items() if k.startswith(split + "/")}

res = {}
for f in sorted((D / "results" / run).glob(f"{split}_*.jsonl")):
    for line in f.read_text().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            res[re.sub(r"\.txt$", "", o["paper"])] = o
missing = sorted(set(key) - set(res))
print(f"{run}/{split}: {len(res)} of {len(key)} scored" + (f"; missing {missing}" if missing else ""))

rows = [(res[p]["p_copied"], key[p]["label"] == "copied", p) for p in key if p in res]
pos = [s for s, y, _ in rows if y]
neg = [s for s, y, _ in rows if not y]
auc = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))
print(f"AUC {auc:.2f}  (positives {len(pos)}, controls {len(neg)})")

tp = sum(res[p]["verdict"] == "likely copied" for p in key if p in res and key[p]["label"] == "copied")
fp = sum(res[p]["verdict"] == "likely copied" for p in key if p in res and key[p]["label"] != "copied")
print(f"verdict 'likely copied': caught {tp}/{len(pos)}, flagged {fp}/{len(neg)} controls"
      + (f", precision {tp / (tp + fp):.0%}" if tp + fp else ""))
print("threshold  caught  false-flags  precision")
for t in (20, 30, 40, 50, 60, 70, 80):
    a = sum(s >= t for s in pos); b = sum(s >= t for s in neg)
    print(f"  >={t:<3}    {a:>2}/{len(pos)}    {b:>2}/{len(neg)}       {a / (a + b):.0%}" if a + b else f"  >={t:<3}    0")

by_type = {}
for p, v in key.items():
    if v["label"] != "copied" and p in res:
        by_type.setdefault(v["control_type"], []).append(res[p]["p_copied"])
for t, s in by_type.items():
    print(f"controls, {t}: n={len(s)}, mean p {sum(s) / len(s):.0f}, >=50: {sum(x >= 50 for x in s)}")

print("\npositives (sorted by p):")
for s, y, p in sorted(rows, reverse=True):
    if y:
        v = key[p]
        print(f"  {p} {s:>3} {v['case']:<7} shared {v['pct_of_later']:.0f}%  {v['title'][:55]}")
print("\ncontrols with p >= 40:")
for s, y, p in sorted(rows, reverse=True):
    if not y and s >= 40:
        print(f"  {p} {s:>3} {key[p]['control_type'][:12]}  {key[p]['title'][:55]}  | {res[p].get('note', '')[:120]}")
