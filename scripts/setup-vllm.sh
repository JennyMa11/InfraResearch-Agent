#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${INFRARESEARCH_VLLM_VENV:-$project_dir/.venv-vllm}"
if [[ "$venv_dir" != /* ]]; then
  venv_dir="$project_dir/${venv_dir#./}"
fi
python_bin="${INFRARESEARCH_VLLM_PYTHON:-python3}"
vllm_version="${INFRARESEARCH_VLLM_VERSION:-0.23.0}"

if [[ ! -x "$venv_dir/bin/python" ]]; then
  "$python_bin" -m venv "$venv_dir"
fi

"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install "vllm==$vllm_version"

echo "vLLM $vllm_version installed in $venv_dir"
echo "Set INFRARESEARCH_VLLM_MODEL_PATH when using a pre-downloaded model."
echo "Start the server with ./scripts/start-vllm.sh"
