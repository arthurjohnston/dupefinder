#!/bin/bash
# 2026-10-10: queued after run_qwen_eval_after_batch.sh (the user asked for more papers once Qwen finishes):
# every remaining tier 1-2 lead (gi_verify.py select skips pairs already in `fulltext`), then the rescue
# pass, measuring and the screenshot pages. No API spend except OpenAlex lookups in the rescue (approved
# 2026-10-09). Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/..
RUN=leads12_s16c
cd $G
[ -n "${NO_WAIT:-}" ] || while pgrep -f run_qwen_eval_after_batch.sh > /dev/null; do sleep 60; done
echo "$(date +%T) == $RUN: remaining tier 1-2 leads"
./gi_verify.sh $RUN arthur.j.johnston@gmail.com --tiers 1 2 > data/verify_$RUN.log 2>&1
tail -2 data/verify_$RUN.log
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
W=$G/data/verify_$RUN
echo "$(date +%T) == rescue"
$P gi_rescue_pdfs.py $RUN > /dev/null 2>&1
grep -E "title search:|papers to retry|done:" $W/rescue.log | tail -3
cd $W
$P $G/gi_verify.py paths --run $RUN --state $W/state.sqlite3
$P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 --paragraphs-file paragraphs.jsonl 2>&1 | grep -E "done\." | tail -1
$P $G/gi_verify.py load-text --run $RUN --library $W/library.sqlite3 --paragraphs-file $W/paragraphs.jsonl
$P $G/gi_verify.py measure --run $RUN --library $W/library.sqlite3 --fix-years
sqlite3 $W/state.sqlite3 "select status, count(*) from papers group by 1 order by 2 desc"
cd $G
echo "$(date +%T) == pages"
nice -n 10 $P gi_write_pages.py data/fulltext_pages_s16 --sample-rate 8 2>&1 | tail -6
echo "$(date +%T) all done"
