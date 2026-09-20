.PHONY: install dev backend worker frontend mcp test check build evaluate evaluate-reranker evaluate-gpu preflight setup-vllm start-vllm verify verify-gpu verify-online

export UV_CACHE_DIR ?= /tmp/infraresearch-uv-cache

install:
	uv sync --project backend --extra dev
	npm --prefix frontend install

dev:
	./scripts/dev.sh

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

verify-online:
	./scripts/verify.sh online
