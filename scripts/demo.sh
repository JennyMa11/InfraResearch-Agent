#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
demo_data_dir="${INFRARESEARCH_DEMO_DATA_DIR:-$project_dir/data/demo}"
api_url="http://127.0.0.1:8000"

cleanup() {
  kill "${backend_pid:-}" "${worker_pid:-}" "${frontend_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export INFRARESEARCH_DATA_DIR="$demo_data_dir"
export INFRARESEARCH_DATABASE_URL="sqlite:///$demo_data_dir/infraresearch.db"
export INFRARESEARCH_VECTOR_BACKEND="sqlite"
export INFRARESEARCH_EMBEDDING_BACKEND="hash"
export INFRARESEARCH_LLM_BASE_URL="http://127.0.0.1:1/v1"
export INFRARESEARCH_LLM_TIMEOUT_SECONDS="0.05"
mkdir -p "$demo_data_dir"

(
  cd "$project_dir"
  uv run --project backend uvicorn infraresearch.main:app --host 127.0.0.1 --port 8000
) &
backend_pid=$!

(
  cd "$project_dir"
  uv run --project backend python -m infraresearch.worker
) &
worker_pid=$!

for _ in $(seq 1 100); do
  if curl --silent --fail "$api_url/api/v1/health" >/dev/null; then
    break
  fi
  sleep 0.1
done

source_count="$(curl --silent "$api_url/api/v1/sources?page_size=1" | \
  uv run --project backend python -c 'import json,sys; print(json.load(sys.stdin)["total"])')"
if [[ "$source_count" == "0" ]]; then
  curl --silent --fail \
    -F "file=@$project_dir/evals/corpus.md;type=text/markdown" \
    "$api_url/api/v1/sources/files" >/dev/null
fi

npm --prefix "$project_dir/frontend" run dev -- --host 127.0.0.1 &
frontend_pid=$!

echo "InfraResearch demo: http://127.0.0.1:5173"
echo "Offline dataset: evals/corpus.md (50-question benchmark corpus)"
wait -n "$backend_pid" "$worker_pid" "$frontend_pid"
