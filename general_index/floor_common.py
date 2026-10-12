"""Shared helpers for the 15%-floor experiment (FLOOR_EXPERIMENT.md). Read-only on gi.sqlite3."""
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
GI = HERE / "data" / "gi.sqlite3"
OUT = HERE / "data" / "floor_experiment"
EXP_DB = OUT / "floor.sqlite3"
# finer bands of max(pct_a, pct_b) = 100 * containment
BANDS = [(0, 5), (5, 10), (10, 12.5), (12.5, 15), (15, 30), (30, 50), (50, 70), (70, 90), (90, 101)]


def ro():
    db = sqlite3.connect(f"file:{GI}?mode=ro", uri=True, timeout=300)
    db.row_factory = sqlite3.Row
    return db


def first_id_below(db, cont):
    """Smallest pair_id whose containment < cont (pairs are ordered by containment desc)."""
    lo, hi = db.execute("select min(pair_id), max(pair_id) from pairs").fetchone()
    hi += 1
    while lo < hi:
        mid = (lo + hi) // 2
        c = db.execute("select containment from pairs where pair_id = ?", (mid,)).fetchone()[0]
        if c < cont:
            hi = mid
        else:
            lo = mid + 1
    return lo


def band_ranges(db):
    """band -> [first pair_id, last pair_id + 1)"""
    edges = {b: first_id_below(db, b / 100 - 1e-9) for b in sorted({x for bd in BANDS for x in bd})}
    return {bd: (edges[bd[1]], edges[bd[0]]) for bd in BANDS}
