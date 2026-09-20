# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
Semantic Versioning.

## [0.3.1] - 2026-09-20

### Fixed

- Mark the three shebang-based evaluation/OCR command scripts executable so Ruff `EXE001` and
  clean-checkout CI pass.

## [0.3.0] - 2026-09-20

### Added

- Layout-aware PDF ingestion with real Tesseract OCR fallback and page/bbox evidence locators.
- Dense + BM25 weighted-RRF retrieval, structure-aware Markdown/code chunks, symbol locators and
  incremental GitHub file/Issue synchronization.
- Optional guarded Web Search and URL ingestion with unified Evidence persistence and citation
  verification.
- Fifty-question evaluation with fact rubrics, pre-repair model scoring, repair-rate diagnostics,
  bootstrap confidence intervals and paired mode comparisons.
- Reproducible Qwen3-0.6B and Qwen3-1.7B live runs across Naive, Fixed Retrieval and Agentic
  modes using Qdrant Local and FastEmbed.
- Controlled Prefix Cache on/off benchmark, 1/4/8/16 concurrency acceptance, real OCR probe,
  Docker images/Compose configuration and fixed offline demo.

### Changed

- Grade rewritten queries directly and retain the best evidence grade across Agent rounds.
- Add domain-aware query rewrites for hybrid retrieval, source filtering, symbols, tool errors,
  model comparison and incremental indexing.
- Make LLM temperature configurable and use temperature zero for published model comparisons.
- Preserve the raw generated answer before citation repair so evaluator results separate model
  capability from end-to-end repaired output.
- Make the vLLM setup work without a system `python-venv` package by creating the environment
  through `uv`, and expose an explicit Prefix Cache switch.

### Fixed

- Prevent citation repair from inflating the primary model-correctness score.
- Avoid benchmark false failures caused by aggressive polling and transient observation errors;
  retries and their error types are now recorded in the report.

## [0.2.0] - 2026-09-20

### Added

- Two-stage candidate retrieval with configurable Identity or FastEmbed cross-encoder reranking.
- Separate retrieval/rerank evidence scores, reranker latency/status metrics and SQLite migration.
- Typed Tool Registry with JSON Schema validation, bounded retries, timeouts and cancellation.
- MCP 2.x server exposing document, code and Issue search tools plus source/chunk/evidence resources.
- Structured Tool, Observation, Rerank, Grade and Decision trace events in the React timeline.
- Reproducible, non-overwriting ablation reports with Git, environment, revision and data hashes.

### Changed

- Split candidate retrieval size from final evidence size.
- Define Citation Recall as required-evidence coverage from cited evidence; retain evidence citation
  coverage as a separate machine-readable metric.

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
