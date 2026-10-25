"""Floor experiment, part 1: volume of pairs/leads per fine band of the larger side's share, and the tier
each pair WOULD get if gi_rank_leads.py's --min-pct floor were lower. Read-only on gi.sqlite3; one pass over
leads JOIN pairs in pair_id order (pairs are sorted by containment desc, so a band is a pair_id range).

The tier logic is gi_rank_leads.py's, copied (not imported: main() writes). For pairs >= 15% it is checked
against the tier gi_rank_leads.py stored (mismatches are counted and printed).

Writes data/floor_experiment/volume.json (all counters) and prints a summary.
Usage: nice -n 10 floor_volume.py [--from-id N] [--chunk 2000000]
"""
import argparse
import json
import re
import time
from collections import Counter

import floor_common as fc
from gi_filters import venue

FINE = [0, 2.5, 5, 7.5, 10, 12.5, 15, 30, 50, 70, 90, 101]


def fine_band(x):
    for lo, hi in zip(FINE, FINE[1:]):
        if lo <= x < hi:
            return f"{lo}-{hi}"
    return "?"


def bucket(n, edges):
    for e in reversed(edges):
        if n >= e:
            return f">={e}"
    return f"<{edges[0]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-id", type=int, default=1)
    ap.add_argument("--chunk", type=int, default=2_000_000)
    args = ap.parse_args()
    db = fc.ro()
    retracted = {}
    for r in db.execute("select original_doi_lower, reason from retraction_watch "
                        "where original_doi_lower is not null order by file_order"):
        retracted[r[0]] = r[1]
    print("loading update_dois", flush=True)
    alt_dois = {r[0] for r in db.execute("select doi from update_dois")}
    abstracts = {(r[0], r[1]): (r[2], r[3], r[4]) for r in db.execute(
        "select doi_a, doi_b, verdict, longest_run, containment5 from abstract_checks")}
    lo, hi = db.execute("select min(pair_id), max(pair_id) from pairs").fetchone()

    reason_c = Counter()      # (band, reason or 'kept')
    kept_rel = Counter()      # (band, authors) for reason ''
    tier_c = Counter()        # (band, tier) document pairs
    tier_doi = Counter()      # (band, tier) DOI pairs, each counted at its highest-containment doc pair
    shared_c = Counter()      # (band, shared bucket) for kept cross-author
    minsamp_c = Counter()     # (band, min sampled bucket) for kept cross-author
    shape_c = Counter()       # (band, shape) for tier 4 (would-be) doc pairs
    abs_c = Counter()         # (band, abstract state, run bucket) for kept cross-author DOI pairs
    short_shared = Counter()  # (band, shared bucket) for 'too short'
    mismatch = Counter()
    examples_mismatch = []
    seen = set()
    t0 = time.time()
    n = 0
    for start in range(max(lo, args.from_id), hi + 1, args.chunk):
        rows = db.execute("""select l.pair_id, l.doi_a, l.doi_b, l.pair_lo, l.pair_hi, l.reason, l.authors, l.tier,
                                    p.shared, p.sampled_a, p.sampled_b, p.pct_a, p.pct_b
                             from leads l join pairs p using (pair_id)
                             where l.pair_id >= ? and l.pair_id < ? order by l.pair_id""",
                          (start, start + args.chunk))
        for (pid, doi_a, doi_b, plo, phi, reason, authors, stored_tier, shared, sa, sb, pa, pb) in rows:
            n += 1
            hi_pct, lo_pct = max(pa, pb), min(pa, pb)
            band = fine_band(hi_pct)
            reason_c[(band, reason or "kept")] += 1
            if reason == "too short":
                short_shared[(band, bucket(shared, [5, 10, 20, 50]))] += 1
            if reason == "":
                kept_rel[(band, authors)] += 1
            tier = None
            if reason == "no metadata" or (reason == "" and authors == "no shared author"):
                unidentified = reason == "no metadata"
                if unidentified and doi_a.startswith("gi:") and doi_b.startswith("gi:"):
                    tier = "both unnamed"
                else:
                    da, db_ = doi_a.lower(), doi_b.lower()
                    rw = "; ".join(f"{s}: {retracted[d].strip(';')}" for s, d in (("A", da), ("B", db_))
                                   if d in retracted)
                    ab = abstracts.get((da, db_))
                    abs_state = "not checked" if ab is None else ab[0]
                    if ab and ab[0] == "shared text" and (ab[2] or 0) >= 0.99 \
                            and venue({"journal": None, "doi": da}) == venue({"journal": None, "doi": db_}):
                        abs_state = "identical (artifact)"
                    if unidentified:
                        tier = 1 if "plagiarism" in rw.lower() else 5
                        if tier == 5 and hi_pct < 50:
                            tier = "5-below-50"
                    elif (da.startswith("gi:") or db_.startswith("gi:") or da in alt_dois or db_ in alt_dois) and \
                            lo_pct >= 85 and "plagiarism" not in rw.lower():
                        tier = 5
                    elif "plagiarism" in rw.lower():
                        tier = 1
                    elif abs_state == "shared text":
                        tier = 2
                    elif rw:
                        tier = 3
                    else:
                        tier = 4
                    if not unidentified:
                        shared_c[(band, bucket(shared, [5, 10, 20, 50, 100]))] += 1
                        minsamp_c[(band, bucket(min(sa, sb), [25, 50, 100, 200, 500, 1000]))] += 1
                        if tier == 4:
                            shape = "one-sided" if lo_pct < 10 and hi_pct >= 3 * lo_pct else "two-sided"
                            shape_c[(band, shape)] += 1
                    key = hash((plo, phi))
                    first = key not in seen
                    if first:
                        seen.add(key)
                        tier_doi[(band, str(tier))] += 1
                        if not unidentified:
                            run = ab[1] if ab else None
                            rb = "none" if ab is None else bucket(run or 0, [1, 10, 15, 20, 30, 50])
                            abs_c[(band, abs_state, rb)] += 1
                tier_c[(band, str(tier))] += 1
            # validate against the stored tier where the production floor applies
            if hi_pct >= 15:
                want = tier if isinstance(tier, int) else None
                if want != stored_tier:
                    mismatch[(str(want), str(stored_tier))] += 1
                    if len(examples_mismatch) < 20:
                        examples_mismatch.append((pid, want, stored_tier, reason, authors, pa, pb))
            elif stored_tier is not None:
                mismatch[("sub15-has-tier", str(stored_tier))] += 1
        print(f"  {start + args.chunk - 1:>11,} / {hi:,}: {n:,} rows, {time.time() - t0:.0f}s, "
              f"{len(seen):,} DOI pairs seen", flush=True)

    out = {k: {"|".join(map(str, kk)): v for kk, v in c.items()} for k, c in dict(
        reason=reason_c, kept_rel=kept_rel, tier=tier_c, tier_doi=tier_doi, shared=shared_c, minsamp=minsamp_c,
        shape=shape_c, abstracts=abs_c, short_shared=short_shared, mismatch=mismatch).items()}
    out["mismatch_examples"] = examples_mismatch
    out["rows"] = n
    (fc.OUT / "volume.json").write_text(json.dumps(out, indent=1))
    print("tier mismatches vs stored (computed, stored):", dict(mismatch))


if __name__ == "__main__":
    main()
