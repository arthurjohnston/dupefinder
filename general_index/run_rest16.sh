#!/bin/bash
# Resume the 16-shard run after the report was stopped (2026-10-08 ~21:00, WAL checkpoint starvation;
# see PILOT_FINDINGS.md): fold the WAL into the database, rerun the fixed report, then what run_full16.sh
# and run_after16.sh would have done: the OpenAlex abstract check (user approved 2026-10-07), ranking, recall.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd); cd "$here/data"
P="$here/../.venv/bin/python3"
echo "$(date +%T) checkpoint: WAL $(du -h gi.sqlite3-wal | cut -f1)"
sqlite3 gi.sqlite3 ".timeout 600000" "pragma wal_checkpoint(truncate);" || { echo "checkpoint failed"; exit 1; }
ls -la gi.sqlite3* ; df -h / | tail -1
echo "$(date +%T) report";  $P ../gi_report.py --db gi.sqlite3 --min-samples 25 || { echo "report failed"; exit 1; }
sqlite3 gi.sqlite3 "select count(*) from reviews; select count(*) from to_review;" | tr '\n' ' '; echo "(reviews, to_review after the report)"
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
echo "$(date +%T) abstracts (OpenAlex, key set)"
$P ../gi_abstract_check.py --db gi.sqlite3 --email arthur.j.johnston@gmail.com \
  --relations "no shared author" --bands ">=90%" "50-90%" "15-50%" || echo "abstract check failed; ranking anyway"
echo "$(date +%T) ranking";  $P ../gi_rank_leads.py --db gi.sqlite3 || { echo "ranking failed"; exit 1; }
echo "$(date +%T) recall";   $P ../gi_recall.py --db gi.sqlite3
df -h / | tail -1
echo "$(date +%T) rest16 done"
