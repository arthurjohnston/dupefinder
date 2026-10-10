#!/bin/bash
# 2026-10-10, while the user is away: (1) the local model (llama-server, run_local_model.sh, must be running)
# reviews the 56-pair evaluation set; (2) compared with Opus; (3) the next full-text batch of ~10k papers:
# ~5,650 tier 1-2 pairs by rank (gi_verify.sh), then the rescue pass and measuring. No API spend except
# OpenAlex title search/location lookups in the rescue (approved 2026-10-09). Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/..
cd $G
PV="v4+local:gpt-oss-20b-MXFP4.gguf"
echo "$(date +%T) == local model on the evaluation set"
$P gi_agent_review.py --backend local --effort medium --workers 1 --pair-list data/local_eval_pairs.tsv 2>&1 | tail -60
echo "$(date +%T) == compare with Opus"
$P compare_local_vs_opus.py "$PV"
echo "$(date +%T) == next batch: leads12_s16b (~10k papers)"
./gi_verify.sh leads12_s16b arthur.j.johnston@gmail.com --tiers 1 2 --limit 5650 > data/verify_leads12_s16b.log 2>&1
tail -2 data/verify_leads12_s16b.log
export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
W=$G/data/verify_leads12_s16b
echo "$(date +%T) == rescue"
$P gi_rescue_pdfs.py leads12_s16b > /dev/null 2>&1
grep -E "title search:|papers to retry|done:" $W/rescue.log | tail -3
cd $W
$P $G/gi_verify.py paths --run leads12_s16b --state $W/state.sqlite3
$P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 --paragraphs-file paragraphs.jsonl 2>&1 | grep -E "done\." | tail -1
$P $G/gi_verify.py load-text --run leads12_s16b --library $W/library.sqlite3 --paragraphs-file $W/paragraphs.jsonl
$P $G/gi_verify.py measure --run leads12_s16b --library $W/library.sqlite3 --fix-years
sqlite3 $W/state.sqlite3 "select status, count(*) from papers group by 1 order by 2 desc"
echo "$(date +%T) all done"
