#!/usr/bin/env bash
set -euo pipefail

model="${INFRARESEARCH_LLM_MODEL:-Qwen/Qwen3-1.7B}"

exec vllm serve "$model" \
  --host 127.0.0.1 \
  --port 8001 \
  --max-model-len 8192 \
  --max-num-seqs 1 \
  --gpu-memory-utilization 0.85 \
  --enable-prefix-caching
