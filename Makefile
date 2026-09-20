.PHONY: install dev demo docker-demo backend worker frontend mcp test check build evaluate evaluate-reranker evaluate-gpu compare-models benchmark-runtime stress-ingestion fault-injection preflight setup-vllm start-vllm verify verify-gpu verify-ocr verify-online

export UV_CACHE_DIR ?= /tmp/infraresearch-uv-cache

install:
	uv sync --project backend --extra dev
	npm --prefix frontend install

dev:
	./scripts/dev.sh

demo:
	./scripts/demo.sh

docker-demo:
	docker compose up --build

backend:
	uv run --project backend uvicorn infraresearch.main:app --reload

worker:
	uv run --project backend python -m infraresearch.worker

frontend:
	npm --prefix frontend run dev

mcp:
	uv run --project backend python -m infraresearch.mcp_server

test:
	uv run --project backend pytest backend/tests
	npm --prefix frontend test -- --run

check:
	uv run --project backend ruff check backend
	npm --prefix frontend run typecheck

build:
	npm --prefix frontend run build

evaluate:
	uv run --project backend python scripts/evaluate.py

evaluate-reranker:
	uv run --project backend python scripts/evaluate.py \
		--reranker-backend fastembed \
		--reranker-model BAAI/bge-reranker-base

evaluate-gpu:
	uv run --project backend python scripts/evaluate.py \
		--provider live \
		--vector-backend qdrant \
		--embedding-backend fastembed \
		--output-dir evals/results/gpu

compare-models:
	uv run --project backend python scripts/compare_evaluations.py \
		--baseline evals/results/roadmap-50q-qwen3-0.6b-full-v2/comparison.json \
		--candidate evals/results/roadmap-50q-qwen3-1.7b-full-v2/comparison.json \
		--output evals/results/model-comparison-qwen3.json

benchmark-runtime:
	uv run --project backend python scripts/benchmark_runtime.py

stress-ingestion:
	uv run --project backend python scripts/stress_ingestion.py

fault-injection:
	uv run --project backend python scripts/fault_injection_worker.py

preflight:
	./scripts/preflight.sh

setup-vllm:
	./scripts/setup-vllm.sh

start-vllm:
	./scripts/start-vllm.sh

verify:
	./scripts/verify.sh all

verify-gpu:
	./scripts/verify.sh gpu

verify-ocr:
	uv run --project backend python scripts/verify_ocr.py

verify-online:
	./scripts/verify.sh online
