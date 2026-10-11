#!/bin/bash
# 2026-10-10, planned with the user: random samples of tiers 4 and 5 (no retraction on either side; the
# retraction tiers 1 and 3 stay out except as test sets). Queued after run_next_batch_after_qwen.sh.
#   strat4_s16: tier 4 (index evidence only), 1,000 per band of the larger side's index overlap,
#               15-30 / 30-50 / 50-70 / 70-90 / 90+  -> 5,000 pairs
#   strat5_s16: tier 5, the "near-identical copy" kind only (the other kind has a side with no metadata,
#               nothing to retrieve it by), 300 per band, 70-90 / 90+ (all of them sit at 70%+) -> 600 pairs
# Each: select -> retrieve -> rescue -> extract -> measure; then the index-text (wrong-DOI) check over
# both and the screenshot pages. No API spend except OpenAlex lookups in the rescue (approved 2026-10-09).
# Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/..
cd $G
while pgrep -f run_next_batch_after_qwen.sh > /dev/null; do sleep 60; done
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"

one_run() {  # RUN, then gi_verify.py select options
  local run=$1; shift
  cd $G
  echo "$(date +%T) == $run: $*"
  ./gi_verify.sh $run arthur.j.johnston@gmail.com "$@" > data/verify_$run.log 2>&1
  grep -E "pair_ids drawn" data/verify_$run.log; tail -1 data/verify_$run.log
  local W=$G/data/verify_$run
  echo "$(date +%T) == $run rescue"
  $P gi_rescue_pdfs.py $run > /dev/null 2>&1
  grep -E "title search:|papers to retry|done:" $W/rescue.log | tail -3
  cd $W
  $P $G/gi_verify.py paths --run $run --state $W/state.sqlite3
  $P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 --paragraphs-file paragraphs.jsonl 2>&1 | grep -E "done\." | tail -1
  $P $G/gi_verify.py load-text --run $run --library $W/library.sqlite3 --paragraphs-file $W/paragraphs.jsonl
  $P $G/gi_verify.py measure --run $run --library $W/library.sqlite3 --fix-years
  sqlite3 $W/state.sqlite3 "select status, count(*) from papers group by 1 order by 2 desc"
}

one_run strat4_s16 --per-band 1000 --strat-tiers 4 --min-band 15 --seed 4 --max-draws 20000000
one_run strat5_s16 --per-band 300 --strat-tiers 5 --min-band 70 --seed 5 --max-draws 60000000
cd $G
echo "$(date +%T) == index-text check"
$P gi_check_index_text.py strat4_s16 strat5_s16 --out data/index_text_check_tier45.tsv 2>&1 | tail -1
echo "$(date +%T) == pages"
nice -n 10 $P gi_write_pages.py data/fulltext_pages_s16 --sample-rate 8 2>&1 | tail -4
echo "$(date +%T) all done"
