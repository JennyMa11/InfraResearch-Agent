#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stage="${1:-all}"
uv_cache_dir="${UV_CACHE_DIR:-/tmp/infraresearch-uv-cache}"
github_url="${INFRARESEARCH_ACCEPTANCE_GITHUB_URL:-https://github.com/octocat/Spoon-Knife}"
github_issues="${INFRARESEARCH_ACCEPTANCE_GITHUB_ISSUES:-1}"

usage() {
  echo "Usage: $0 [all|preflight|quality|unit|build|browser|api|evaluation|gpu|online]"
}

run_stage() {
  local name="$1"
  shift
  echo "[verify] ${name}"
  "$@"
}

cleanup_api() {
  if [[ -n "${worker_pid:-}" ]]; then
    kill "$worker_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
    worker_pid=""
  fi
  if [[ -n "${api_pid:-}" ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
    api_pid=""
  fi
  if [[ -n "${acceptance_dir:-}" && "$acceptance_dir" == /tmp/infraresearch-acceptance.* ]]; then
    rm -rf "$acceptance_dir"
    acceptance_dir=""
  fi
}

trap cleanup_api EXIT INT TERM

run_api_stage() {
  local with_github="$1"
  acceptance_dir="$(mktemp -d /tmp/infraresearch-acceptance.XXXXXX)"
  local port
  port="$(
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
  )"
  local log_path="$acceptance_dir/api.log"
  local worker_log_path="$acceptance_dir/worker.log"
  local database_path="$acceptance_dir/acceptance.db"
  local data_path="$acceptance_dir/data"

  (
    cd "$project_dir"
    env \
      UV_CACHE_DIR="$uv_cache_dir" \
      INFRARESEARCH_DATA_DIR="$data_path" \
      INFRARESEARCH_DATABASE_URL="sqlite:///$database_path" \
      INFRARESEARCH_VECTOR_BACKEND=qdrant \
      INFRARESEARCH_EMBEDDING_BACKEND=hash \
      INFRARESEARCH_LLM_BASE_URL=http://127.0.0.1:1/v1 \
      INFRARESEARCH_LLM_TIMEOUT_SECONDS=0.05 \
      uv run --project backend uvicorn infraresearch.main:app \
        --host 127.0.0.1 \
        --port "$port" \
        >"$log_path" 2>&1
  ) &
  api_pid=$!

  local ready=0
  for _ in $(seq 1 100); do
    if curl --silent --fail "http://127.0.0.1:${port}/api/v1/health" >/dev/null; then
      ready=1
      break
    fi
    if ! kill -0 "$api_pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  if [[ "$ready" -ne 1 ]]; then
    echo "[verify] isolated API failed to start"
    sed -n '1,240p' "$log_path"
    return 1
  fi

  (
    cd "$project_dir"
    env \
      UV_CACHE_DIR="$uv_cache_dir" \
      INFRARESEARCH_DATA_DIR="$data_path" \
      INFRARESEARCH_DATABASE_URL="sqlite:///$database_path" \
      INFRARESEARCH_VECTOR_BACKEND=qdrant \
      INFRARESEARCH_EMBEDDING_BACKEND=hash \
      INFRARESEARCH_LLM_BASE_URL=http://127.0.0.1:1/v1 \
      INFRARESEARCH_LLM_TIMEOUT_SECONDS=0.05 \
      uv run --project backend python -m infraresearch.worker \
        >"$worker_log_path" 2>&1
  ) &
  worker_pid=$!

  local command=(
    env UV_CACHE_DIR="$uv_cache_dir"
    uv run --project backend python scripts/smoke_api.py
    --base-url "http://127.0.0.1:${port}"
    --expected-provider extractive
    --expected-vector-backend qdrant_local
  )
  if [[ "$with_github" == "yes" ]]; then
    command+=(--github-url "$github_url")
    if [[ "$github_issues" == "1" ]]; then
      command+=(--github-issues)
    fi
  fi
  (
    cd "$project_dir"
    "${command[@]}"
  )
  cleanup_api
}

run_preflight() {
  run_stage "preflight" "$project_dir/scripts/preflight.sh"
}

run_quality() {
  (
    cd "$project_dir"
    run_stage "python lint" env UV_CACHE_DIR="$uv_cache_dir" \
      uv run --project backend ruff check backend scripts
    run_stage "typescript typecheck" npm --prefix frontend run typecheck
  )
}

run_unit() {
  (
    cd "$project_dir"
    run_stage "backend tests" env UV_CACHE_DIR="$uv_cache_dir" \
      uv run --project backend pytest backend/tests -m "not gpu"
    run_stage "frontend tests" npm --prefix frontend test -- --run
  )
}

run_build() {
  (
    cd "$project_dir"
    run_stage "frontend production build" npm --prefix frontend run build
  )
}

run_browser() {
  (
    cd "$project_dir"
    run_stage "playwright browser smoke" npm --prefix frontend run test:e2e
  )
}

run_evaluation() {
  local evaluation_dir
  evaluation_dir="$(mktemp -d /tmp/infraresearch-evaluation.XXXXXX)"
  (
    cd "$project_dir"
    run_stage "20-question dual baseline evaluation" env UV_CACHE_DIR="$uv_cache_dir" \
      uv run --project backend python scripts/evaluate.py --output-dir "$evaluation_dir"
  )
  rm -rf "$evaluation_dir"
}

run_gpu() {
  (
    cd "$project_dir"
    run_stage "GPU and vLLM verification" env UV_CACHE_DIR="$uv_cache_dir" \
      uv run --project backend python scripts/verify_gpu.py
  )
}

case "$stage" in
  all)
    run_preflight
    run_quality
    run_unit
    run_build
    run_browser
    run_stage "isolated API workflow" run_api_stage no
    run_evaluation
    ;;
  preflight) run_preflight ;;
  quality) run_quality ;;
  unit) run_unit ;;
  build) run_build ;;
  browser) run_browser ;;
  api) run_stage "isolated API workflow" run_api_stage no ;;
  evaluation) run_evaluation ;;
  gpu) run_gpu ;;
  online) run_stage "real GitHub workflow" run_api_stage yes ;;
  *)
    usage
    exit 2
    ;;
esac

echo "[verify] ${stage}: passed"
