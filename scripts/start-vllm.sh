#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$project_dir/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$project_dir/.env"
  set +a
fi

venv_dir="${INFRARESEARCH_VLLM_VENV:-$project_dir/.venv-vllm}"
if [[ "$venv_dir" != /* ]]; then
  venv_dir="$project_dir/${venv_dir#./}"
fi
vllm_bin="${INFRARESEARCH_VLLM_BIN:-}"
if [[ -z "$vllm_bin" && -x "$venv_dir/bin/vllm" ]]; then
  vllm_bin="$venv_dir/bin/vllm"
fi
if [[ -z "$vllm_bin" ]]; then
  vllm_bin="$(command -v vllm || true)"
fi
if [[ -z "$vllm_bin" ]]; then
  echo "vLLM is not installed. Run ./scripts/setup-vllm.sh first." >&2
  exit 1
fi

served_model="${INFRARESEARCH_LLM_MODEL:-Qwen/Qwen3-0.6B}"
model="${INFRARESEARCH_VLLM_MODEL_PATH:-$served_model}"
if [[ -n "${INFRARESEARCH_VLLM_MODEL_PATH:-}" && "$model" != /* ]]; then
  model="$project_dir/${model#./}"
fi

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export VLLM_USE_V2_MODEL_RUNNER="${VLLM_USE_V2_MODEL_RUNNER:-0}"
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"
export CC="${CC:-gcc-12}"
export CXX="${CXX:-g++-12}"

args=(
  serve "$model"
  --served-model-name "$served_model"
  --host "${INFRARESEARCH_VLLM_HOST:-127.0.0.1}"
  --port "${INFRARESEARCH_VLLM_PORT:-8001}"
  --dtype "${INFRARESEARCH_VLLM_DTYPE:-float16}"
  --max-model-len "${INFRARESEARCH_VLLM_MAX_MODEL_LEN:-8192}"
  --max-num-seqs "${INFRARESEARCH_VLLM_MAX_NUM_SEQS:-1}"
  --gpu-memory-utilization "${INFRARESEARCH_VLLM_GPU_MEMORY_UTILIZATION:-0.75}"
  --enable-prefix-caching
)

if [[ "${INFRARESEARCH_VLLM_ENFORCE_EAGER:-1}" == "1" ]]; then
  args+=(--enforce-eager)
fi
if [[ "${INFRARESEARCH_VLLM_ASYNC_SCHEDULING:-0}" == "0" ]]; then
  args+=(--no-async-scheduling)
fi

exec "$vllm_bin" "${args[@]}" "$@"
