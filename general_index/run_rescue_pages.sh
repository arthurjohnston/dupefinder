#!/bin/bash
# 2026-10-18: the second half of run_aff4_batch.sh, for a run whose select + retrieve was done by hand with
# gi_verify.sh (which stops after a plain download): rescue (no OpenAlex title search) -> extract -> measure ->
# pages. Usage: ./run_rescue_pages.sh RUN. No paid spend. Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/..
RUN=${1:?usage: run_rescue_pages.sh RUN}
W=$G/data/verify_$RUN
[ -d "$W" ] || { echo "no $W"; exit 1; }
cd $G
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
echo "$(date +%T) == $RUN: rescue (no title search)"
$P gi_rescue_pdfs.py $RUN --no-titles > /dev/null 2>&1
grep -E "papers to retry|done:" $W/rescue.log | tail -2
cd $W
$P $G/gi_verify.py paths --run $RUN --state $W/state.sqlite3
$P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 --paragraphs-file paragraphs.jsonl 2>&1 | grep -E "done\." | tail -1
$P $G/gi_verify.py load-text --run $RUN --library $W/library.sqlite3 --paragraphs-file $W/paragraphs.jsonl
$P $G/gi_verify.py measure --run $RUN --library $W/library.sqlite3 --fix-years
sqlite3 $W/state.sqlite3 "select status, count(*) from papers group by 1 order by 2 desc"
sqlite3 -readonly $G/data/gi.sqlite3 "select coalesce(ft_verdict, 'no PDFs'), count(*) from fulltext where run = '$RUN' group by 1"
cd $G
echo "$(date +%T) == pages"
nice -n 10 $P gi_write_pages.py data/fulltext_pages_s16 --sample-rate 8 2>&1 | tail -6
echo "$(date +%T) all done"
