#!/bin/bash
# Wait for shards 4-15 to finish streaming, then the full 16-shard run into data/gi.sqlite3:
# back up the human/agent/full-text tables, import documents for all shards, then run_gi.sh
# (blocks -> common -> clusters -> finalize -> report -> ranking -> recall) with the abstract check
# skipped until the user OKs its OpenAlex spend.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd); cd "$here/data"
P="$here/../.venv/bin/python3"
for s in $(seq 4 15); do
  until grep -q '^done:' "slice$s.log" 2>/dev/null && [ -s "slice$s/docs.tsv" ]; do sleep 120; done
done
echo "$(date +%T) all 16 shards streamed; checking each read its whole file"
# "done:" alone isn't proof: a connection the server closed early used to read as end-of-file
# (slice 13, 2026-10-07: 82.2 of 282.3 GB). Compare bytes read with the zip's real size.
for s in $(seq 4 15); do
  h=$(printf '%x' $s)
  got=$(grep '^done:' "slice$s.log" | sed -E 's/done: ([0-9.]+) GB.*/\1/')
  size=$(curl -s "https://archive.org/metadata/GeneralIndex.ngrams.$h" | python3 -c \
    "import json,sys; print([int(f['size']) for f in json.load(sys.stdin)['files'] if f['name'].endswith('.zip')][0])")
  if ! python3 -c "import sys; sys.exit(0 if $got * 1e9 >= 0.999 * $size else 1)"; then
    echo "slice $s TRUNCATED: read $got GB of $((size / 1000000000)) GB; re-stream it (run_downloads.sh \"$s\" 1 8) and rerun run_full16.sh"
    exit 1
  fi
done
echo "$(date +%T) all 16 shards complete"
# Disk guard (2026-10-08): the 16-shard database is projected at ~55-88 GB more than today's, which this
# disk may not have. Stop here rather than fill the disk mid-write; the user decides how to free space.
need_gb=${MIN_FREE_GB:-90}
free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
if [ "$free_gb" -lt "$need_gb" ]; then
  echo "$(date +%T) STOPPED before the run: ${free_gb} GB free, need ${need_gb} GB. Free space (see the disk plan in PILOT_FINDINGS.md / ask the user), then rerun run_full16.sh (MIN_FREE_GB=... to override)."
  exit 1
fi
df -h / | tail -1
bk="gi_backup_$(date +%Y%m%d_%H%M).sqlite3"
sqlite3 gi.sqlite3 ".timeout 300000" "attach '$bk' as b;
  create table b.reviews as select * from reviews;
  create table b.agent_reviews as select * from agent_reviews;
  create table b.fulltext as select * from fulltext;" && echo "$(date +%T) backed up reviews/agent_reviews/fulltext to $bk"
echo "$(date +%T) import documents (all shards)"
$P ../gi_db.py import-documents || { echo "import-documents failed"; exit 1; }
SKIP_ABSTRACTS=1 bash ../run_gi.sh 0-15 8 100 gi.sqlite3
sqlite3 gi.sqlite3 "select count(*) from reviews; select count(*) from to_review;" | tr '\n' ' '; echo "(reviews, to_review after the run)"
echo "$(date +%T) full16 done"
