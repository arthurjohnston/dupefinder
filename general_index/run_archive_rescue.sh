#!/bin/bash
# 2026-10-25: the user asked to get the papers the US/European rounds couldn't download from the Wayback
# Machine and PubMed Central (gi_rescue_pdfs.py --archives-only), then measure what that completes. Per run:
# archives -> paths -> extract -> load-text -> measure; pages once at the end (it rewrites them all).
# Usage: ./run_archive_rescue.sh RUN [RUN ...]   No paid spend. Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/..
cd $G
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
for RUN in "$@"; do
  W=$G/data/verify_$RUN
  [ -d "$W" ] || { echo "no $W"; continue; }
  echo "$(date +%T) == $RUN: Wayback Machine / PMC"
  $P gi_rescue_pdfs.py $RUN --archives-only > /dev/null 2>&1
  grep -E "archives: [0-9]+ papers|have a PMC copy|archives done" $W/rescue.log | tail -3
  cd $W
  $P $G/gi_verify.py paths --run $RUN --state $W/state.sqlite3
  $P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 --paragraphs-file paragraphs.jsonl 2>&1 | grep -E "done\." | tail -1
  $P $G/gi_verify.py load-text --run $RUN --library $W/library.sqlite3 --paragraphs-file $W/paragraphs.jsonl
  $P $G/gi_verify.py measure --run $RUN --library $W/library.sqlite3 --fix-years
  sqlite3 -readonly $G/data/gi.sqlite3 "select coalesce(ft_verdict, 'no PDFs'), count(*) from fulltext where run = '$RUN' group by 1"
  cd $G
done
echo "$(date +%T) == pages"
nice -n 10 $P gi_write_pages.py data/fulltext_pages_s16 --sample-rate 8 2>&1 | tail -3
echo "$(date +%T) all done"
