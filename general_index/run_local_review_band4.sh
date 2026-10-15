#!/bin/bash
# 2026-10-15: the local agent on band4_s16's real-overlap pairs (30 of the 3,000 tier-4 two-sided 15-50% leads).
# Full packet, effort medium: on the 24-pair slim evaluation the slim packet at effort low agreed with Opus on
# problem-vs-not 17/24 against the full packet's 20/24 on the same pairs, and slim at medium hit max_tokens. The
# speed-up kept is the server's: flash attention, -ub 2048, two pairs at a time (NP=2 + --workers 2), machine
# otherwise idle. No API spend. Resumable. Don't edit while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3
cd $G
NP=2 ./run_local_model.sh gpt-oss-20b > data/local_model_server_review_band4.log 2>&1 &
SRV=$!
until curl -sf http://127.0.0.1:8080/health > /dev/null; do sleep 10; kill -0 $SRV 2>/dev/null || { echo "server died"; tail data/local_model_server_review_band4.log; exit 1; }; done
echo "$(date +%T) == gpt-oss-20b on band4_s16"
$P gi_agent_review.py --backend local --effort medium --workers 2 --runs band4_s16
kill $SRV
$P gi_write_pages.py data/fulltext_pages_s16 --sample-rate 8 --index-only
$P gi_priority_db.py
echo "$(date +%T) all done"
