#!/bin/bash
# 2026-10-17: the user asked for 3,000 more pairs ranked by who the later paper's authors are now
# (gi_affiliations.py: an author still publishing at a US/European university first, then a university
# elsewhere, then no data, then no author active in academia). The same leads as run_band4_batch.sh (tier 4,
# two-sided, 15-50%, no shared author: where strat4_s16 found real overlap; no retraction tiers), but select
# reads the top 15,000 by lead_rank (--pool default 5 x --limit), fetches their authors from OpenAlex/ORCID
# (free filter calls, --max-calls 3000 cap) and takes the best 3,000 by that rank.
# Download -> rescue (no OpenAlex title search) -> extract -> measure -> pages. No paid spend.
# Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/..
RUN=${1:-aff4_s16}   # the run name: a new one selects the next 3,000 (pairs already in fulltext are skipped)
# POOL=N: how many of the best-ranked leads select reads before taking 3,000 by authors (default 5 x 3,000;
# 2026-10-19: once earlier runs had taken the US/European ones from that, 100000)
cd $G
echo "$(date +%T) == $RUN: select + retrieve"
./gi_verify.sh $RUN arthur.j.johnston@gmail.com --tiers 4 --band 15 50 --shape two-sided --no-shared-author \
  --limit 3000 --order affiliation ${POOL:+--pool $POOL} > data/verify_$RUN.log 2>&1 \
  || { echo "select/retrieve failed, stopping:"; tail -3 data/verify_$RUN.log; exit 1; }
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
