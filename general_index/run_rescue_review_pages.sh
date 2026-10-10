#!/bin/bash
# 2026-10-09: second try at the missing PDFs of the two 16-shard full-text runs (gi_rescue_pdfs.py: OpenAlex
# title search for no-DOI papers, then every other OA copy), the rest of each run (paths -> extract ->
# load-text -> measure -> move), the agent review (Claude API, user-requested, capped by MAX_COST), and the
# HTML pages + landing page in data/fulltext_pages_s16/. Don't edit this or gi_verify.sh while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; D=$G/..; P=$D/.venv/bin/python3
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
MAX_COST=${MAX_COST:-30}
for run in leads12_s16a strat_s16a; do
  W=$G/data/verify_$run
  echo "$(date +%T) == $run: rescue"
  $P $G/gi_rescue_pdfs.py $run || { echo "rescue failed for $run"; continue; }
  grep -E "title search:|papers to retry|done:" $W/rescue.log | tail -3
  cd $W
  $P $G/gi_verify.py paths --run $run --state $W/state.sqlite3
  $P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 --paragraphs-file paragraphs.jsonl 2>&1 | grep -E "done\." | tail -1
  $P $G/gi_verify.py load-text --run $run --library $W/library.sqlite3 --paragraphs-file $W/paragraphs.jsonl
  $P $G/gi_verify.py measure --run $run --library $W/library.sqlite3 --fix-years
  if mountpoint -q /mnt/papers; then
    $P $G/gi_verify.py move --run $run --state $W/state.sqlite3 --library $W/library.sqlite3 --dest /mnt/papers/general_index/$run
  else
    echo "external drive not mounted: rescued PDFs stay in $W/papers (rerun gi_verify.py move later)"
  fi
  cd $G
done
echo "$(date +%T) == agent review (dry run for the count and token estimate)"
$P $G/gi_agent_review.py --dry-run 2>&1 | tail -4
echo "$(date +%T) == agent review (max \$$MAX_COST)"
$P $G/gi_agent_review.py --max-cost $MAX_COST 2>&1 | tail -15
echo "$(date +%T) == pages"
$P $G/gi_write_pages.py $G/data/fulltext_pages_s16 --sample-rate 8 2>&1 | tail -5
echo "$(date +%T) all done"
