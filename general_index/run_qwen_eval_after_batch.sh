#!/bin/bash
# 2026-10-10: queued after run_local_eval_then_batch.sh (the user approved: the 10k-paper batch first, then
# the same 56-pair evaluation on Qwen3-30B). Stops the gpt-oss server, starts Qwen3-30B (CPU), reviews
# data/local_eval_pairs.tsv, compares with Opus, then stops the server. No API spend.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3
cd $G
while pgrep -f run_local_eval_then_batch.sh > /dev/null; do sleep 60; done
echo "$(date +%T) batch chain finished; switching to Qwen3-30B"
pkill -f llama-server; sleep 5
./run_local_model.sh qwen3-30b > data/local_model_server_qwen.log 2>&1 &
SRV=$!
until curl -sf http://127.0.0.1:8080/health > /dev/null; do sleep 10; kill -0 $SRV 2>/dev/null || { echo "server died"; tail data/local_model_server_qwen.log; exit 1; }; done
PV="v4+local:Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
echo "$(date +%T) == Qwen3-30B on the evaluation set"
$P gi_agent_review.py --backend local --effort medium --workers 1 --pair-list data/local_eval_pairs.tsv 2>&1 | tail -60
echo "$(date +%T) == compare with Opus"
$P compare_local_vs_opus.py "$PV" --out data/compare_qwen_vs_opus.tsv
kill $SRV
echo "$(date +%T) all done"
