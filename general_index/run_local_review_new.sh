#!/bin/bash
# 2026-10-11: the user asked for the local agent to review the 528 result pages no AI had reviewed (almost all
# from the leads12_s16c batch). gpt-oss-20b, not Qwen3-30B: closer to Opus on the 56-pair evaluation
# (decision 40/56 vs Qwen's 30/49) and ~3x faster. ~6 min a pair on the CPU, so ~2 days. No API spend.
# Resumable: rerunning skips pairs this prompt version already has. Refreshes the pages' index at the end.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3
cd $G
pkill -f llama-server; sleep 5
./run_local_model.sh gpt-oss-20b > data/local_model_server_review_new.log 2>&1 &
SRV=$!
until curl -sf http://127.0.0.1:8080/health > /dev/null; do sleep 10; kill -0 $SRV 2>/dev/null || { echo "server died"; tail data/local_model_server_review_new.log; exit 1; }; done
echo "$(date +%T) == gpt-oss-20b on data/local_review_new_pairs.tsv ($(wc -l < data/local_review_new_pairs.tsv) pairs)"
nice -n 5 $P gi_agent_review.py --backend local --effort medium --workers 1 --pair-list data/local_review_new_pairs.tsv
kill $SRV
$P gi_write_pages.py data/fulltext_pages_s16 --sample-rate 8 --index-only
echo "$(date +%T) all done"
