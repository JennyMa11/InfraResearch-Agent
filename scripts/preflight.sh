#!/usr/bin/env bash
set -u

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
status=0

echo "InfraResearch preflight"
python3 --version || status=1
node --version || status=1
git --version || status=1

if command -v nvidia-smi >/dev/null 2>&1; then
  if gpu_info="$(nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv,noheader 2>&1)"; then
    echo "$gpu_info"
  else
    echo "GPU: blocked or unavailable ($gpu_info)"
  fi
else
  echo "GPU: N/A (基础离线 provider 仍可运行)"
fi

if curl --silent --fail --max-time 2 http://127.0.0.1:8001/v1/models >/dev/null; then
  echo "LLM endpoint: ready"
else
  echo "LLM endpoint: unavailable; extractive fallback will be used"
fi

if [[ -w "$project_dir" ]]; then
  echo "Workspace: writable"
else
  echo "Workspace: not writable"
  status=1
fi

exit "$status"
