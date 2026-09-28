#!/usr/bin/env python3
"""Apply many agent verdicts in one process, reusing review_dupes.py's own
load_candidate_by_id()/apply_agent_verdict() (same agent_decided_* status vocabulary, same
b/c bucket-cascade and p same-paper-correction logic -- see REVIEWING.md's "Agent review
mode"). Exists so a reviewing agent working through a large batch of candidates doesn't
spawn one `review_dupes.py --agent-verdict` subprocess per row -- read/reason about a whole
batch, then apply the resulting verdicts in one call here.

Input: a JSON file, a single object mapping potential_dupes id (as a string) to a verdict
key (one of d/f/b/c/p/u), e.g.:
    {"12345": "d", "12346": "f", "12399": "u"}

Prints one line per id applied (or skipped, with why) and a final summary. Safe to re-run --
re-applying the same verdict to an already-agent_decided_* row just overwrites it with
itself; nothing here is destructive beyond what a normal agent verdict already is.
"""
import argparse
import json
from pathlib import Path

import review_dupes as rd
import db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-db", type=Path, required=True)
    parser.add_argument("verdicts_json", type=Path, help="path to the {id: key} JSON file")
    args = parser.parse_args()

    verdicts = json.loads(args.verdicts_json.read_text())
    conn = db.connect(args.library_db)
    conn.row_factory = __import__("sqlite3").Row

    applied, skipped_missing, skipped_bad_key, errors = 0, 0, 0, 0
    for id_str, key in verdicts.items():
        try:
            candidate_id = int(id_str)
        except ValueError:
            print(f"SKIP {id_str!r}: not a valid id")
            skipped_bad_key += 1
            continue
        if key not in ("d", "f", "b", "c", "p", "u"):
            print(f"SKIP id={candidate_id}: {key!r} is not a valid verdict key (d/f/b/c/p/u)")
            skipped_bad_key += 1
            continue
        row = rd.load_candidate_by_id(conn, candidate_id)
        if row is None:
            print(f"SKIP id={candidate_id}: no such potential_dupes row (already deleted?)")
            skipped_missing += 1
            continue
        try:
            marked = rd.apply_agent_verdict(conn, row, key)
            fallback = ("same_author correction (no status set)" if key == "a"
                         else "same_paper correction (no status set)")
            label = rd.AGENT_ACTIONS.get(key, fallback)
            print(f"OK id={candidate_id} -> {key!r} ({label}): {len(marked)} row(s) updated")
            applied += 1
        except Exception as e:
            print(f"ERROR id={candidate_id}: {e}")
            errors += 1

    print(f"\n{applied} applied, {skipped_missing} missing, {skipped_bad_key} bad key/verdict, "
          f"{errors} error(s), {len(verdicts)} total in input")
    conn.close()


if __name__ == "__main__":
    main()
