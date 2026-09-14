#!/usr/bin/env python3
"""
Join candidate_dois against a local scimag-style DOI table in SQL and print
coverage -- the DB-side counterpart to `estimate_coverage.py estimate`,
which instead loads the whole DOI file into a Python set. Requires
load_candidate_dois.py to have populated `candidate_dois` first.

Same auth convention as load_candidate_dois.py: no -u/-p by default (ambient
auth, e.g. run under sudo for root-via-unix_socket); --mysql-user/--mysql-host
to use a password-auth user, with the password itself coming from the
MYSQL_PWD env var or ~/.my.cnf, never a CLI flag.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys

MATCHES_QUERY = """
SELECT s.ID, s.DOI
FROM `{scimag_table}` s
JOIN `{candidate_table}` c
  ON s.DOI = c.doi;
"""

OVERALL_QUERY = """
SELECT
  (SELECT COUNT(*) FROM `{candidate_table}`) AS candidates,
  COUNT(DISTINCT c.doi) AS found,
  ROUND(
    100.0 * COUNT(DISTINCT c.doi) /
    (SELECT COUNT(*) FROM `{candidate_table}`),
    2
  ) AS coverage_percent
FROM `{candidate_table}` c
JOIN `{scimag_table}` s
  ON s.DOI = c.doi;
"""

BY_YEAR_QUERY = """
SELECT
  c.year,
  COUNT(*) AS candidates,
  COUNT(s.DOI) AS found,
  ROUND(100.0 * COUNT(s.DOI) / COUNT(*), 2) AS coverage_percent
FROM `{candidate_table}` c
LEFT JOIN `{scimag_table}` s
  ON s.DOI = c.doi
WHERE c.year IS NOT NULL
GROUP BY c.year
ORDER BY c.year;
"""


def run_mysql(mysql_args: list[str], database: str, sql: str, tabular: bool = True) -> str:
    cmd = ["mysql", *mysql_args]
    if tabular:
        cmd += ["--table"]
    cmd.append(database)
    proc = subprocess.run(cmd, input=sql, text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"mysql exited {proc.returncode}")
    return proc.stdout


def export_matches(mysql_args: list[str], database: str, sql: str, out_path: str) -> int:
    """
    Run the ID/DOI join and write a proper CSV (header `ID,DOI`, matching what
    torrent_manifest.py build --scimag-csv expects) -- via the csv module, not
    a naive tab-to-comma replace, since DOI text could in principle contain a
    comma even though it won't contain the tab mysql uses as its own delimiter.
    """
    cmd = ["mysql", *mysql_args, "--batch", "--raw", "--skip-column-names", database]
    proc = subprocess.run(cmd, input=sql, text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"mysql exited {proc.returncode}")

    n = 0
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "DOI"])
        for line in proc.stdout.splitlines():
            if not line:
                continue
            scimag_id, doi = line.split("\t", 1)
            writer.writerow([scimag_id, doi])
            n += 1
    return n


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database", default="scimag")
    p.add_argument("--candidate-table", default="candidate_dois")
    p.add_argument("--scimag-table", default="scimag")
    p.add_argument("--mysql-user", default=None, help="Passed as -u to mysql. Omit to use ambient auth.")
    p.add_argument("--mysql-host", default=None, help="Passed as -h to mysql. Omit for the local socket.")
    p.add_argument("--by-year", action="store_true", help="Also print a per-year coverage breakdown.")
    p.add_argument(
        "--export-matches",
        default=None,
        help="Also write matched (ID, DOI) rows to this CSV path -- the input "
             "torrent_manifest.py build --scimag-csv expects.",
    )
    args = p.parse_args()

    mysql_args = []
    if args.mysql_user:
        mysql_args += ["-u", args.mysql_user]
    if args.mysql_host:
        mysql_args += ["-h", args.mysql_host]

    overall = OVERALL_QUERY.format(candidate_table=args.candidate_table, scimag_table=args.scimag_table)
    print("Overall coverage")
    print("-----------------")
    print(run_mysql(mysql_args, args.database, overall))

    if args.by_year:
        by_year = BY_YEAR_QUERY.format(candidate_table=args.candidate_table, scimag_table=args.scimag_table)
        print("Coverage by year")
        print("-----------------")
        print(run_mysql(mysql_args, args.database, by_year))

    if args.export_matches:
        matches = MATCHES_QUERY.format(candidate_table=args.candidate_table, scimag_table=args.scimag_table)
        n = export_matches(mysql_args, args.database, matches, args.export_matches)
        print(f"Wrote {n:,} matched (ID, DOI) rows to {args.export_matches}")


if __name__ == "__main__":
    main()
