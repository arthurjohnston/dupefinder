#!/bin/bash
# 2026-10-14: the user asked for a few hundred more pairs. Tier 1-2 are exhausted; tier 4's random sample
# (strat4_s16) found real overlap in the two-sided 15-50% band and index artifacts at 70%+, so this takes
# the top 3,000 two-sided, no-shared-author tier-4 leads in 15-50% (major publishers first, by lead_rank).
# Download -> rescue (no OpenAlex title search: the only paid call, and it found 6% in strat4) -> extract ->
# measure -> pages. The local AI review runs separately (run_local_review_band4.sh). No paid spend.
# Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/..
RUN=band4_s16
cd $G
echo "$(date +%T) == $RUN: select + retrieve"
./gi_verify.sh $RUN arthur.j.johnston@gmail.com --tiers 4 --band 15 50 --shape two-sided --no-shared-author \
  --limit 3000 > data/verify_$RUN.log 2>&1
tail -2 data/verify_$RUN.log
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
W=$G/data/verify_$RUN
echo "$(date +%T) == rescue (no title search)"
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
