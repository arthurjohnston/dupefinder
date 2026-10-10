#!/bin/bash
# Start a local model for gi_agent_review.py --backend local: llama.cpp's llama-server on 127.0.0.1:8080
# (OpenAI-compatible API). Usage: run_local_model.sh [MODEL]   MODEL = gpt-oss-20b (default) | qwen3-30b
# Installed 2026-10-10 in ~/.local/llama.cpp (llama.cpp b11541, CPU and Vulkan builds, + models/); nothing
# system-wide.
#
# CPU build by default. Measured on this laptop (Core Ultra 5 225H, 30 GB, Intel Arc iGPU), gpt-oss-20b:
#   CPU, 6 threads:   prompt 45 tok/s, generation 13 tok/s   (10 threads: 53 / 9 -- more threads hurt generation)
#   Vulkan (GPU=1):   prompt 196 tok/s, generation 5.4 tok/s, and it pins the whole model (12 GB) in shared
#                     memory, which pushed the machine into swap; expert weights on the CPU (-ncmoe) was worse.
# A review packet is ~10k tokens in, ~1-3k out, so the CPU build is faster overall (~6 min a pair).
set -euo pipefail
L=~/.local/llama.cpp
case "${1:-gpt-oss-20b}" in
  gpt-oss-20b) M=$L/models/gpt-oss-20b-MXFP4.gguf ;;
  qwen3-30b)   M=$L/models/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf ;;
  *) echo "unknown model $1"; exit 1 ;;
esac
if [ "${GPU:-0}" = 1 ]; then BIN=$L/llama-b11541/llama-server; DEV="-ngl 99"; else BIN=$L/cpu/llama-b11541/llama-server; DEV="-t 6"; fi
# -c: room for the system prompt + the largest packet (~15k tokens) + the reply
exec $BIN -m "$M" --jinja -c 32768 -np 1 $DEV --host 127.0.0.1 --port 8080 ${EXTRA:-}
