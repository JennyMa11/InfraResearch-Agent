.PHONY: install dev backend frontend test check build evaluate preflight

install:
	uv sync --project backend --extra dev
	npm --prefix frontend install

dev:
	./scripts/dev.sh

backend:
	uv run --project backend uvicorn infraresearch.main:app --reload

frontend:
	npm --prefix frontend run dev

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

preflight:
	./scripts/preflight.sh
