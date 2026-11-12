"""Score General Index leads by how likely they are to become a medium/high-severity copying case, from the
index's own numbers plus the abstract check -- for leads whose full text we don't have (the unobtainable list).

Training set (export): every tier 2/4 lead checked in full text with both PDFs ('real overlap' / 'no real
overlap'). A hit is a real overlap with no byline overlap whose decision is 'problem' at severity medium or
high -- your review where there is one, the case agent's otherwise (results view). Real overlaps decided
'unsure' are left out. Written to data/lead_score_training.tsv; the export reads ~4,000 pairs through the
leads table and takes several minutes, so re-run it only when there are many new checked/reviewed pairs.

Features (features()): the larger and smaller GI 5-gram share of the two documents, log shared sampled
5-grams, log of the smaller and larger document's 5-gram total, the share of shared 5-grams that are common
phrases, and the abstract check ('shared text' / 'no shared text' / not checked). Model: scikit-learn
gradient-boosted trees (depth 3, 300 rounds, learning rate 0.05).

Measured 2026-11-11 by 5-fold cross-validation on 4,143 pairs (739 hits, 18%): AUC 0.886, precision 75% in
the top 100, 71% top 300, 64% top 500, 54% top 1,000. For comparison, the abstract check x 20%-overlap-band
table: AUC 0.850; the index's numbers without the abstract check: AUC 0.771. Without the abstract check, high
index overlap mostly means a dud (60%+ with no shared abstract text: 1 real in 828); with it, high overlap
means the most severe cases (your graded 60%+ pairs: ~60% high severity, vs 9% under 20%).

The checked pairs were chosen by earlier runs (tiers, affiliation, stratified samples), not at random from
the unobtainable pool, so treat the score as a ranking, not a calibrated probability.

Usage: gi_lead_score.py export      (writes data/lead_score_training.tsv)
       gi_lead_score.py cv          (cross-validated metrics on the training file)
       in code: model = fit(); model.predict_proba(features(rows))[:, 1]
"""
import argparse
import csv
import math
import sqlite3
import sys

import numpy as np

import gi_db

TRAIN = gi_db.DATA / "lead_score_training.tsv"
COLS = ["pair_id", "tier", "abstracts", "pct_a", "pct_b", "shared", "shared_common", "tot_a", "tot_b", "hit"]
ABSTRACTS = ("shared text", "not checked", "no shared text")


def features(rows):
    """rows: dicts with pct_a, pct_b, shared, shared_common, tot_a, tot_b, abstracts."""
    out = []
    for r in rows:
        pa, pb = float(r["pct_a"] or 0), float(r["pct_b"] or 0)
        sh = float(r["shared"] or 0)
        ta, tb = float(r["tot_a"] or 1), float(r["tot_b"] or 1)
        out.append([max(pa, pb), min(pa, pb), math.log1p(sh), math.log1p(min(ta, tb)), math.log1p(max(ta, tb)),
                    float(r["shared_common"] or 0) / max(sh, 1),
                    r["abstracts"] == "shared text", r["abstracts"] == "no shared text"])
    return np.array(out, dtype=float)


def model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, random_state=1)


def load_training():
    with open(TRAIN, newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    return rows, np.array([int(r["hit"]) for r in rows])


def fit():
    rows, y = load_training()
    return model().fit(features(rows), y)


def export(db):
    db.execute("""create temp table ft as select pair_lo, pair_hi, ft_verdict, byline from fulltext
                  where retrieved = 'AB' and ft_verdict in ('real overlap', 'no real overlap')""")
    db.execute("create index temp.ft_key on ft(pair_lo, pair_hi)")
    q = """select l.pair_id, l.tier, l.abstracts, ft.ft_verdict, ft.byline, p.pct_a, p.pct_b, p.shared,
                  p.shared_common, da.total_5grams, db.total_5grams,
                  coalesce(r.human_decision, r.ai_decision), coalesce(r.human_severity, r.ai_severity)
           from ft join leads l on l.pair_lo = ft.pair_lo and l.pair_hi = ft.pair_hi and l.primary_pair = 1
           join pairs p on p.pair_id = l.pair_id
           left join documents da on da.dkey = p.dkey_a left join documents db on db.dkey = p.dkey_b
           left join results r on r.pair_lo = ft.pair_lo and r.pair_hi = ft.pair_hi
           where l.tier in (2, 4)"""
    out = []
    for pid, tier, abst, verdict, byline, pa, pb, sh, shc, ta, tb, dec, sev in db.execute(q):
        if abst not in ABSTRACTS:
            continue
        real = verdict == "real overlap" and byline == "no byline overlap"
        if real and dec == "unsure":
            continue
        out.append([pid, tier, abst, pa, pb, sh, shc, ta, tb, int(real and dec == "problem" and sev in ("medium", "high"))])
    with open(TRAIN, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(COLS)
        w.writerows(out)
    print(f"{len(out)} pairs, {sum(r[-1] for r in out)} hits -> {TRAIN}")


def cv():
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    rows, y = load_training()
    p = cross_val_predict(model(), features(rows), y, method="predict_proba",
                          cv=StratifiedKFold(5, shuffle=True, random_state=1))[:, 1]
    order = np.argsort(-p)
    print(f"{len(y)} pairs, {y.sum()} hits: AUC {roc_auc_score(y, p):.3f}, AP {average_precision_score(y, p):.3f}, "
          + ", ".join(f"top {k} {y[order[:k]].mean():.0%}" for k in (100, 300, 500, 1000)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["export", "cv"])
    args = ap.parse_args()
    if args.command == "export":
        export(sqlite3.connect(f"file:{gi_db.DB_PATH}?mode=ro", uri=True))
    else:
        cv()


if __name__ == "__main__":
    sys.exit(main())
