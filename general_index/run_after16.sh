#!/bin/bash
# After run_full16.sh: the OpenAlex abstract check on the 16-shard cross-author leads (user approved
# spending past the free tier on 2026-10-07), then re-rank (new tier-2 leads) and recall.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd); cd "$here/data"
P="$here/../.venv/bin/python3"
until grep -q 'full16 done' run_full16.log 2>/dev/null; do
  grep -qE 'failed|Traceback' run_full16.log 2>/dev/null && { echo "run_full16 failed; not starting"; exit 1; }
  sleep 300
done
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
echo "$(date +%T) abstracts (OpenAlex, key set)"
$P ../gi_abstract_check.py --db gi.sqlite3 --email arthur.j.johnston@gmail.com \
  --relations "no shared author" --bands ">=90%" "50-90%" "15-50%"
echo "$(date +%T) ranking";  $P ../gi_rank_leads.py --db gi.sqlite3
echo "$(date +%T) recall";   $P ../gi_recall.py --db gi.sqlite3
echo "$(date +%T) after16 done"
