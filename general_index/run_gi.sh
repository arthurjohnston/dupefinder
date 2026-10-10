#!/bin/bash
# Steps 2-3 and the recall check, all into data/gi.sqlite3:
#   gi_blocks.py run      step 2, one shard-pair block at a time (resumable, incremental; a shard that
#                         isn't finished streaming is skipped and its blocks run on the next call)
#   gi_blocks.py common   shared_common per pair (phrases in more than --cap documents, kept, not dropped)
#   gi_blocks.py clusters template families from those common phrases -> phrase_clusters
#   gi_blocks.py finalize pairs table <- every block of the run
#   -> gi_report.py (leads) -> gi_abstract_check.py (abstracts, abstract_checks)
#   -> gi_rank_leads.py (leads: tiers) -> gi_recall.py (Retraction Watch recall)
# Usage: run_gi.sh [SHARDS] [RATE] [CAP] [DB]     defaults: 0-15 8 100 gi.sqlite3
#   e.g. run_gi.sh 0-3 4 20     reproduces the 2026-10-06 slice 0-3 run (6,734,753 pairs, exactly)
# documents must already be loaded (gi_db.py import-documents) for those shards, and retraction_watch
# (gi_db.py import-rw). Full text is a separate, chosen step: gi_verify.sh. The reviews table is only read.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
P="$here/../.venv/bin/python3"
shards=${1:-0-15}; rate=${2:-8}; cap=${3:-100}; db=${4:-gi.sqlite3}
cd "$here/data"
[ -r ~/.config/dupefinder/openalex_api_key ] && export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
B="$P ../gi_blocks.py --rate $rate --cap $cap --db $db"
echo "$(date +%T) blocks: shards $shards, 1-in-$rate, cap $cap"
$B run --shards "$shards" --workers 3
echo "$(date +%T) shared_common"; $B common
echo "$(date +%T) clusters";      $B clusters
echo "$(date +%T) finalize";      $B finalize
# --min-samples 50 was set at 1-in-4; the same document has half the samples at 1-in-8
echo "$(date +%T) report";        $P ../gi_report.py --db "$db" --min-samples $((200 / rate))
# SKIP_ABSTRACTS=1 leaves the OpenAlex abstract check out: at 16 shards it is ~40k filter calls (~$4),
# past the free $1/day, so it runs only once the user has OK'd the spend.
if [ "${SKIP_ABSTRACTS:-0}" = 1 ]; then echo "$(date +%T) abstracts skipped (SKIP_ABSTRACTS=1)"; else
echo "$(date +%T) abstracts";     $P ../gi_abstract_check.py --db "$db" --email arthur.j.johnston@gmail.com \
  --relations "no shared author" --bands ">=90%" "50-90%" "15-50%"; fi
echo "$(date +%T) ranking";       $P ../gi_rank_leads.py --db "$db"
echo "$(date +%T) recall";        $P ../gi_recall.py --db "$db"
echo "$(date +%T) done"
