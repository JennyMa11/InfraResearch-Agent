#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from infraresearch import __version__
from infraresearch.agent import ResearchAgent, latency_percentile
from infraresearch.chunking import chunk_markdown
from infraresearch.config import Settings
from infraresearch.models import (
    Base,
    Chunk,
    ResearchRun,
    Source,
    Status,
    ToolCall,
    TraceEvent,
)
from infraresearch.provider import LLMProvider
from infraresearch.retrieval import SearchHit, VectorIndex, tokenize
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    return {
        "commit": run("rev-parse", "HEAD"),
        "short_commit": run("rev-parse", "--short", "HEAD"),
        "dirty": bool(run("status", "--porcelain")),
    }


def environment_metadata() -> dict[str, str]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python": platform.python_version(),
    }


def cached_fastembed_revision(cache_dir: Path, model_name: str) -> str:
    model_dir = cache_dir.expanduser() / f"models--{model_name.replace('/', '--')}"
    reference = model_dir / "refs" / "main"
    if reference.exists():
        return reference.read_text().strip() or "unrecorded"
    snapshots = model_dir / "snapshots"
    if snapshots.exists():
        revisions = sorted(path.name for path in snapshots.iterdir() if path.is_dir())
        if len(revisions) == 1:
            return revisions[0]
    return "unrecorded"


def seed_corpus(session: Session, path: Path) -> list[Chunk]:
    source = Source(kind="file", name=path.name, uri=str(path), status=Status.COMPLETED)
    session.add(source)
    session.flush()
    chunks = []
    for parsed in chunk_markdown(path.read_text(), path.name, size=1000, overlap=100):
        chunk = Chunk(
            source_id=source.id,
            content=parsed.content,
            locator=parsed.locator,
            path=parsed.path,
            start_line=parsed.start_line,
            end_line=parsed.end_line,
            heading=parsed.heading,
            metadata_json=json.dumps({"category": "docs"}),
            content_hash=hashlib.sha256(parsed.content.encode()).hexdigest(),
        )
        session.add(chunk)
        chunks.append(chunk)
    session.commit()
    return chunks


def overlap_score(reference: str, answer: str) -> float:
    required = set(tokenize(reference))
    actual = set(tokenize(answer))
    return len(required & actual) / max(1, len(required))


def evaluate_mode(
    session: Session,
    agent: ResearchAgent,
    questions: list[dict[str, Any]],
    mode: str,
    *,
    candidate_k: int,
    evidence_k: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for number, item in enumerate(questions, start=1):
        print(f"[evaluate] {mode}: {number}/{len(questions)} {item['id']}", flush=True)
        run = ResearchRun(question=item["question"], mode=mode)
        session.add(run)
        session.commit()
        agent.execute(
            session,
            run.id,
            candidate_k=candidate_k,
            evidence_k=evidence_k,
        )
        session.refresh(run)
        evidence = list(run.evidence)
        tool_calls = session.scalars(
            select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.id)
        ).all()
        events = session.scalars(
            select(TraceEvent)
            .where(TraceEvent.run_id == run.id)
            .order_by(TraceEvent.sequence)
        ).all()
        tool_names = {call.name for call in tool_calls}
        answer = run.answer or ""
        required = [term.lower() for term in item["required_evidence"]]
        evidence_text = " ".join(record.content for record in evidence).lower()
        cited = {citation.evidence_id for citation in run.citations}
        valid = {record.id.rsplit(":", 1)[-1] for record in evidence}
        precision = len(cited & valid) / max(1, len(cited))
        cited_evidence_text = " ".join(
            record.content
            for record in evidence
            if record.id.rsplit(":", 1)[-1] in cited & valid
        ).lower()
        citation_recall = (
            sum(term in cited_evidence_text for term in required) / len(required)
        )
        evidence_citation_coverage = len(cited & valid) / max(1, len(valid))
        metrics = json.loads(run.metrics_json)
        row = {
            "id": item["id"],
            "mode": mode,
            "status": run.status,
            "correctness": overlap_score(item["reference_answer"], answer),
            "citation_precision": precision,
            "citation_recall": citation_recall,
            "evidence_citation_coverage": evidence_citation_coverage,
            "recall_at_k": sum(term in evidence_text for term in required) / len(required),
            "tool_accuracy": float(
                bool(tool_names & set(item["expected_tools"]))
                or (mode == "naive" and "semantic_document_search" in tool_names)
            ),
            "agent_steps": metrics["agent_steps"],
            "tool_calls": metrics["tool_calls"],
            "tokens": metrics["prompt_tokens"] + metrics["completion_tokens"],
            "latency_ms": metrics["total_latency_ms"],
            "reranker_latency_ms": metrics.get("reranker_latency_ms", 0),
            "retrieval_rounds": metrics["retrieval_rounds"],
        }
        rows.append(row)
        details.append(
            {
                "id": item["id"],
                "mode": mode,
                "run_id": run.id,
                "question": item["question"],
                "reference_answer": item["reference_answer"],
                "required_evidence": item["required_evidence"],
                "expected_sources": item["expected_sources"],
                "expected_tools": item["expected_tools"],
                "answer": answer,
                "metrics": metrics,
                "scores": row,
                "evidence": [
                    {
                        "id": record.id.rsplit(":", 1)[-1],
                        "chunk_id": record.chunk_id,
                        "source_id": record.source_id,
                        "locator": record.locator,
                        "retrieval_score": record.retrieval_score,
                        "rerank_score": record.rerank_score,
                        "final_score": record.score,
                        "content": record.content,
                    }
                    for record in evidence
                ],
                "citations": [
                    {
                        "evidence_id": citation.evidence_id,
                        "marker": citation.marker,
                        "claim": citation.claim,
                        "valid": bool(citation.valid),
                    }
                    for citation in run.citations
                ],
                "tool_calls": [
                    {
                        "name": call.name,
                        "arguments": json.loads(call.arguments_json),
                        "result_summary": call.result_summary,
                        "duration_ms": call.duration_ms,
                        "status": call.status,
                        "error": call.error,
                    }
                    for call in tool_calls
                ],
                "events": [
                    {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "node": event.node,
                        "data": json.loads(event.data_json),
                    }
                    for event in events
                    if event.event_type != "token"
                ],
            }
        )
    return rows, details


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "questions": len(rows),
        "completed": sum(row["status"] == Status.COMPLETED for row in rows),
        "correctness": mean(row["correctness"] for row in rows),
        "citation_precision": mean(row["citation_precision"] for row in rows),
        "citation_recall": mean(row["citation_recall"] for row in rows),
        "evidence_citation_coverage": mean(
            row["evidence_citation_coverage"] for row in rows
        ),
        "recall_at_k": mean(row["recall_at_k"] for row in rows),
        "tool_accuracy": mean(row["tool_accuracy"] for row in rows),
        "average_steps": mean(row["agent_steps"] for row in rows),
        "average_tool_calls": mean(row["tool_calls"] for row in rows),
        "average_retrieval_rounds": mean(row["retrieval_rounds"] for row in rows),
        "average_tokens": mean(row["tokens"] for row in rows),
        "average_reranker_latency_ms": mean(row["reranker_latency_ms"] for row in rows),
        "p50_latency_ms": latency_percentile([row["latency_ms"] for row in rows], 0.5),
        "p95_latency_ms": latency_percentile([row["latency_ms"] for row in rows], 0.95),
    }


def markdown(summary: dict[str, Any]) -> str:
    config = summary["configuration"]
    git = summary["git"]
    lines = [
        "# InfraResearch 可复现评测",
        "",
        f"- 版本：`v{__version__}`",
        f"- Git：`{git['short_commit']}`（dirty={str(git['dirty']).lower()}）",
        f"- 数据集：{summary['dataset']['questions']} 题，SHA256 `{summary['dataset']['sha256']}`",
        f"- 运行时间：{summary['generated_at']}",
        f"- Provider：`{config['provider']}` / `{config['llm_model']}`",
        f"- 向量后端：`{summary['actual']['vector_backend']}`",
        f"- Embedding：`{summary['actual']['embedding_backend']}`",
        (
            f"- Reranker：`{config['reranker_backend']}` / `{config['reranker_model']}` "
            f"@ `{config['reranker_revision']}`"
        ),
        f"- Reranker Warmup：`{summary['actual']['reranker_warmup_ms']:.1f} ms`",
        f"- Candidate K / Evidence K：`{config['candidate_k']}` / `{config['evidence_k']}`",
        f"- Citation Repair：`{str(config['citation_repair']).lower()}`",
        "",
        "| 模式 | 正确性 | 引用精确率 | 引用召回率 | Recall@K | 工具准确率 | 平均步数 | 平均工具数 | P50 ms | P95 ms | Rerank ms | 平均 Tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in ("naive", "agentic"):
        item = summary[mode]
        lines.append(
            f"| {mode.title()} | {item['correctness']:.3f} | "
            f"{item['citation_precision']:.3f} | {item['citation_recall']:.3f} | "
            f"{item['recall_at_k']:.3f} | {item['tool_accuracy']:.3f} | "
            f"{item['average_steps']:.2f} | {item['average_tool_calls']:.2f} | "
            f"{item['p50_latency_ms']:.1f} | {item['p95_latency_ms']:.1f} | "
            f"{item['average_reranker_latency_ms']:.1f} | {item['average_tokens']:.1f} |"
        )
    run_kind = "真实模型" if config["provider"] == "live" else "离线"
    lines.extend(
        [
            "",
            (
                f"> 本报告如实记录固定语料上的{run_kind}结果，不预设 Agentic RAG "
                "或 Reranker 必然优于基线。逐题证据、工具调用和非 token 轨迹保存在 "
                "`comparison.json`。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/questions.json")
    parser.add_argument("--corpus", type=Path, default=ROOT / "evals/corpus.md")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evals/results")
    parser.add_argument("--run-name")
    parser.add_argument("--provider", choices=("extractive", "live"), default="extractive")
    parser.add_argument("--vector-backend", choices=("sqlite", "qdrant"), default="sqlite")
    parser.add_argument("--embedding-backend", choices=("hash", "fastembed"), default="hash")
    parser.add_argument("--reranker-backend", choices=("identity", "fastembed"), default="identity")
    parser.add_argument("--reranker-model", default="BAAI/bge-reranker-base")
    parser.add_argument("--reranker-local-files-only", action="store_true")
    parser.add_argument(
        "--warmup-reranker",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--evidence-k", type=int, default=6)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-agent-rewrites", type=int, default=2)
    parser.add_argument("--token-budget", type=int, default=6000)
    parser.add_argument(
        "--citation-repair",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("INFRARESEARCH_LLM_BASE_URL", "http://127.0.0.1:8001/v1"),
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("INFRARESEARCH_LLM_MODEL", "Qwen/Qwen3-0.6B"),
    )
    parser.add_argument("--llm-revision", default="unrecorded")
    parser.add_argument("--embedding-revision", default="unrecorded")
    parser.add_argument("--reranker-revision", default="unrecorded")
    args = parser.parse_args()
    if args.candidate_k < args.evidence_k:
        parser.error("--candidate-k must be greater than or equal to --evidence-k")
    if args.candidate_k < 1 or args.evidence_k < 1:
        parser.error("retrieval limits must be positive")
    if args.run_name and not re.fullmatch(r"[A-Za-z0-9._-]+", args.run_name):
        parser.error("--run-name may contain only letters, digits, dot, underscore, and dash")
    return args


def main() -> None:
    args = parse_args()
    questions = json.loads(args.dataset.read_text())
    if len(questions) != 20:
        raise SystemExit("the evaluator requires exactly 20 questions")
    random.seed(args.seed)
    generated_at = datetime.now(UTC).isoformat()
    git = git_metadata()
    run_name = args.run_name or (
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{git['short_commit']}-{args.reranker_backend}"
    )
    output_dir = args.output_dir / run_name
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing evaluation run: {output_dir}")
    output_dir.mkdir(parents=True)

    configuration = {
        "seed": args.seed,
        "provider": args.provider,
        "llm_model": args.llm_model,
        "llm_revision": args.llm_revision,
        "vector_backend": args.vector_backend,
        "embedding_backend": args.embedding_backend,
        "embedding_revision": args.embedding_revision,
        "reranker_backend": args.reranker_backend,
        "reranker_model": args.reranker_model,
        "reranker_revision": args.reranker_revision,
        "reranker_local_files_only": args.reranker_local_files_only,
        "warmup_reranker": args.warmup_reranker,
        "candidate_k": args.candidate_k,
        "evidence_k": args.evidence_k,
        "max_agent_rewrites": args.max_agent_rewrites,
        "token_budget": args.token_budget,
        "citation_repair": args.citation_repair,
        "python_hash_seed": os.getenv("PYTHONHASHSEED", "unset"),
    }

    with tempfile.TemporaryDirectory(prefix="infraresearch-eval-") as directory:
        settings = Settings(
            data_dir=Path(directory),
            database_url="sqlite://",
            vector_backend=args.vector_backend,
            embedding_backend=args.embedding_backend,
            reranker_backend=args.reranker_backend,
            reranker_model=args.reranker_model,
            reranker_local_files_only=args.reranker_local_files_only,
            candidate_k=args.candidate_k,
            evidence_k=args.evidence_k,
            max_agent_rewrites=args.max_agent_rewrites,
            token_budget=args.token_budget,
            citation_repair_enabled=args.citation_repair,
            llm_base_url=(
                args.llm_base_url if args.provider == "live" else "http://127.0.0.1:1/v1"
            ),
            llm_model=args.llm_model,
            llm_timeout_seconds=300 if args.provider == "live" else 0.05,
        )
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            chunks = seed_corpus(session, args.corpus)
            index = VectorIndex(settings)
            index.upsert(chunks)
            agent = ResearchAgent(settings, index, LLMProvider(settings))
            reranker_warmup_ms = 0.0
            if args.reranker_backend != "identity" and args.warmup_reranker and chunks:
                warmup_started = time.perf_counter()
                agent.reranker.rerank("reranker warmup", [SearchHit(chunks[0], 0.0)])
                reranker_warmup_ms = (time.perf_counter() - warmup_started) * 1000
            if (
                args.embedding_backend == "fastembed"
                and configuration["embedding_revision"] == "unrecorded"
            ):
                configuration["embedding_revision"] = cached_fastembed_revision(
                    settings.resolved_embedding_cache_dir,
                    settings.embedding_model,
                )
            if (
                args.reranker_backend == "fastembed"
                and configuration["reranker_revision"] == "unrecorded"
            ):
                configuration["reranker_revision"] = cached_fastembed_revision(
                    settings.resolved_reranker_cache_dir,
                    settings.reranker_model,
                )
            naive_rows, naive_details = evaluate_mode(
                session,
                agent,
                questions,
                "naive",
                candidate_k=args.candidate_k,
                evidence_k=args.evidence_k,
            )
            agentic_rows, agentic_details = evaluate_mode(
                session,
                agent,
                questions,
                "agentic",
                candidate_k=args.candidate_k,
                evidence_k=args.evidence_k,
            )
            rows = naive_rows + agentic_rows
            details = naive_details + agentic_details
            summary = {
                "generated_at": generated_at,
                "version": __version__,
                "git": git,
                "environment": environment_metadata(),
                "dataset": {
                    "path": str(args.dataset),
                    "sha256": sha256_file(args.dataset),
                    "questions": len(questions),
                },
                "corpus": {
                    "path": str(args.corpus),
                    "sha256": sha256_file(args.corpus),
                    "chunks": len(chunks),
                },
                "configuration": configuration,
                "actual": {
                    "vector_backend": index.backend,
                    "embedding_backend": index.embedding_backend,
                    "reranker": agent.reranker.name,
                    "reranker_warmup_ms": reranker_warmup_ms,
                },
                "naive": summarize(naive_rows),
                "agentic": summarize(agentic_rows),
            }
            if getattr(index, "_client", None):
                index._client.close()

    (output_dir / "comparison.json").write_text(
        json.dumps(
            {"summary": summary, "rows": rows, "details": details},
            ensure_ascii=False,
            indent=2,
        )
    )
    with (output_dir / "comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    report = markdown(summary)
    (output_dir / "comparison.md").write_text(report)
    (output_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(report)
    print(f"[evaluate] results: {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
