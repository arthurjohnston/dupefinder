"""Retired 2026-10-06: superseded by gi.sqlite3 (gi_db.py).

gi_results_db.py used to rebuild data/gi_results.sqlite3 (one pair_results table) from the TSVs. The
pipeline now writes data/gi.sqlite3 directly, a table per step, with `fulltext` holding the full-text
measurements and `reviews` holding verdicts. The equivalent of pair_results is the lead_details view
joined to fulltext, e.g.:

  select l.*, f.ft_verdict, f.ft_pct_a, f.ft_pct_b, f.ft_matched_words, f.byline
  from lead_details l left join fulltext f using (pair_lo, pair_hi) where l.primary_pair = 1;

The old data/gi_results.sqlite3 is left in place, unmaintained.
"""
import sys

if __name__ == "__main__":
    print(__doc__.strip())
    sys.exit(0)
