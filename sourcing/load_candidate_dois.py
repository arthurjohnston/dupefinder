#!/usr/bin/env python3
"""
Load candidate DOIs (estimate_coverage.py collect's output) into a MariaDB
table, so they can be joined against a local scimag-style DOI table in SQL
instead of loading a multi-GB DOI file into a Python set every time (see
coverage_join.py, and README's "Select only the required SciMag ZIPs",
which already assumes a `candidate_dois` table exists).

Fully regenerates the table every run (DROP + CREATE + bulk INSERT):
candidate_dois is a derived mirror of candidates.jsonl, not hand-curated
state, so there is nothing to preserve across runs -- just rerun this after
any collect/merge that changes candidates.jsonl.

Shells out to the `mysql` CLI rather than a Python DB driver: this machine
has no pip/venv available (see CLAUDE.md), so no new dependency was added.
Values are still safely escaped (not string-joined) before being embedded
in the generated SQL.

Auth: by default this runs `mysql <database>` with no -u/-p, i.e. whatever
ambient auth is in effect (e.g. `sudo python3 load_candidate_dois.py`, which
gets root via unix_socket the same way `sudo mariadb scimag -e ...` does).
Pass --mysql-user/--mysql-host for a password-auth user; the password
itself is read from the MYSQL_PWD env var (or ~/.my.cnf), never a CLI flag,
so it never shows up in `ps aux`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_TABLE = "candidate_dois"


def sql_quote(s: str) -> str:
    """Escape a string for a single-quoted MySQL/MariaDB literal.

    Correct for the default sql_mode (backslash escaping enabled, which is
    MariaDB's default unless NO_BACKSLASH_ESCAPES is set). Only ever used
    on our own DOI/title strings -- not a general-purpose SQL builder.
    """
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def load_candidates(path: Path) -> list[dict]:
    rows: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {e}")
            doi = (rec.get("doi") or "").strip().lower()
            if not doi:
                continue
            rows[doi] = {
                "doi": doi,
                "year": rec.get("year"),
                "relevance_score": rec.get("relevance_score"),
            }
    return list(rows.values())


def run_mysql(mysql_args: list[str], database: str, sql: str) -> None:
    cmd = ["mysql", *mysql_args, database]
    proc = subprocess.run(cmd, input=sql, text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"mysql exited {proc.returncode}")
    if proc.stderr.strip():
        sys.stderr.write(proc.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidates", default="output/candidates.jsonl")
    p.add_argument("--database", default="scimag")
    p.add_argument("--table", default=DEFAULT_TABLE)
    p.add_argument("--mysql-user", default=None, help="Passed as -u to mysql. Omit to use ambient auth.")
    p.add_argument("--mysql-host", default=None, help="Passed as -h to mysql. Omit for the local socket.")
    p.add_argument("--batch-size", type=int, default=2000, help="Rows per multi-row INSERT statement.")
    args = p.parse_args()

    mysql_args = []
    if args.mysql_user:
        mysql_args += ["-u", args.mysql_user]
    if args.mysql_host:
        mysql_args += ["-h", args.mysql_host]

    rows = load_candidates(Path(args.candidates))
    print(f"Loaded {len(rows):,} unique candidate DOIs from {args.candidates}", file=sys.stderr)
    if not rows:
        raise SystemExit("No DOIs found -- nothing to load.")

    ddl = f"""
DROP TABLE IF EXISTS `{args.table}`;
CREATE TABLE `{args.table}` (
  doi VARCHAR(200) COLLATE utf8mb4_unicode_ci NOT NULL PRIMARY KEY,
  year SMALLINT NULL,
  relevance_score SMALLINT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""
    run_mysql(mysql_args, args.database, ddl)
    print(f"(Re)created `{args.database}`.`{args.table}`", file=sys.stderr)

    total = 0
    for i in range(0, len(rows), args.batch_size):
        batch = rows[i : i + args.batch_size]
        values = ",\n".join(
            "({}, {}, {})".format(
                sql_quote(r["doi"]),
                r["year"] if isinstance(r["year"], int) else "NULL",
                r["relevance_score"] if isinstance(r["relevance_score"], int) else "NULL",
            )
            for r in batch
        )
        sql = f"INSERT INTO `{args.table}` (doi, year, relevance_score) VALUES\n{values};\n"
        run_mysql(mysql_args, args.database, sql)
        total += len(batch)
        print(f"  inserted {total:,}/{len(rows):,}", file=sys.stderr)

    verify_sql = f"SELECT COUNT(*) FROM `{args.table}`;"
    cmd = ["mysql", *mysql_args, "--batch", "--raw", "--skip-column-names", args.database]
    proc = subprocess.run(cmd, input=verify_sql, text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"mysql exited {proc.returncode}")
    print(f"Verified row count in `{args.table}`: {proc.stdout.strip()}")


if __name__ == "__main__":
    main()
