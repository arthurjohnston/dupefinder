#!/bin/bash
# 2026-10-14: can the local review run faster without losing agreement with Opus? 24 pairs that Opus (v4) and
# gpt-oss-20b (full packet, effort medium) both reviewed, rerun with the slim packet at effort medium and low,
# two pairs at a time (NP=2). Compare with compare_local_vs_opus.py. No API spend. Don't edit while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3
cd $G
NP=2 ./run_local_model.sh gpt-oss-20b > data/local_model_server_eval_slim.log 2>&1 &
SRV=$!
until curl -sf http://127.0.0.1:8080/health > /dev/null; do sleep 10; kill -0 $SRV 2>/dev/null || { echo "server died"; exit 1; }; done
for effort in medium low; do
  echo "$(date +%T) == slim, effort $effort"
  $P gi_agent_review.py --backend local --effort $effort --workers 2 --packet slim --prompt-version v4-slim-$effort \
    --include-reviewed --pair-list data/local_eval_slim_pairs.tsv
  echo "$(date +%T) == done $effort"
done
kill $SRV
echo "$(date +%T) all done"
