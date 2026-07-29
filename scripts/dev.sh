#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  kill "$backend_pid" "$worker_pid" "$frontend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$project_dir"
  uv run --project backend uvicorn infraresearch.main:app --reload
) &
backend_pid=$!

(
  cd "$project_dir"
  uv run --project backend python -m infraresearch.worker
) &
worker_pid=$!

npm --prefix "$project_dir/frontend" run dev &
frontend_pid=$!

wait -n "$backend_pid" "$worker_pid" "$frontend_pid"
