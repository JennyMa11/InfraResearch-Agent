# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
Semantic Versioning.

## [0.1.1] - 2026-07-29

### Added

- FastEmbed `multilingual-e5-small` semantic embeddings with an explicit hash fallback.
- Reproducible vLLM 0.23.0 setup and RTX 3060 Laptop / WSL launch scripts.
- Isolated API, Playwright, real GitHub and GPU/prefix-cache verification stages.
- Live Qwen + Qdrant + E5 mode for the 20-question comparison evaluator.
- Direct report output field and compact Agent trace presentation.
- Source reindex/delete lifecycle, research history and failed-run retry APIs.
- Frontend source polling, history restoration and SSE disconnect polling fallback.
- SQLite-backed independent worker with atomic claims, leases and stale-task recovery.
- Cooperative cancellation for ingestion and research tasks.
- Paginated source/research lists with status, kind/mode and text search.

### Fixed

- Remove private `<think>` output from Qwen responses and disable Qwen thinking mode.
- Repair missing/invalid citations with a grounded extractive report and persist real claims.
- Deduplicate evidence, remove stale Qdrant points and rebuild vectors from SQLite when needed.
- Return validation errors for unsupported GitHub URLs instead of background 500 errors.
- Recover pending jobs after restart and mark interrupted running jobs as explicitly retryable.
- Adopt legacy in-process tasks and safely requeue work after an expired worker lease.

## [0.1.0] - 2026-07-28

### Added

- FastAPI and React monorepo with SQLite persistence and Qdrant Local adapter.
- Local files, source code, public GitHub repository and Issue ingestion.
- Naive RAG and bounded single-Agent research workflows.
- Stable evidence locators, inline citations and citation verification.
- SSE run trace, tool latency, token, TTFT and cache metric surfaces.
- Deterministic offline provider and OpenAI-compatible Qwen provider.
- Reproducible 20-question comparison evaluator and report formats.
- Unit, integration, frontend and smoke-test scaffolding.
