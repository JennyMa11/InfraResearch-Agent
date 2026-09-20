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
from math import log2
from pathlib import Path
from statistics import mean
from typing import Any

from infraresearch import __version__
from infraresearch.agent import CITATION_RE, ResearchAgent, latency_percentile
from infraresearch.chunking import chunk_markdown
from infraresearch.config import Settings
from infraresearch.evaluation import (
    bootstrap_mean_ci,
    fact_coverage_score,
    paired_bootstrap_delta_ci,
)
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
    """Legacy lexical reference recall retained as a diagnostic metric."""

    required = set(tokenize(reference))
    actual = set(tokenize(answer))
    return len(required & actual) / max(1, len(required))


def evidence_relevance(required: list[str], content: str) -> float:
    if not required:
        return 0.0
    normalized = content.lower()
    return sum(term.lower() in normalized for term in required) / len(required)


def ranked_retrieval_metrics(
    hits: list[SearchHit], required: list[str], *, cutoffs: tuple[int, ...] = (1, 3, 5, 10)
) -> dict[str, float]:
    relevance = [evidence_relevance(required, hit.chunk.content) for hit in hits]
    result = {
        f"recall_at_{cutoff}": (
            evidence_relevance(required, " ".join(hit.chunk.content for hit in hits[:cutoff]))
            if required
            else float(not hits[:cutoff])
        )
        for cutoff in cutoffs
    }
    first_relevant = next((rank for rank, score in enumerate(relevance, start=1) if score), None)
    result["mrr"] = 1 / first_relevant if first_relevant else 0.0
    gains = relevance[: max(cutoffs)]
    dcg = sum((2**gain - 1) / log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal = sorted(gains, reverse=True)
    ideal_dcg = sum(
        (2**gain - 1) / log2(rank + 1) for rank, gain in enumerate(ideal, start=1)
    )
    result["ndcg_at_10"] = dcg / ideal_dcg if ideal_dcg else float(not required)
    return result


def evaluate_retrieval(
    session: Session,
    index: VectorIndex,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for item in questions:
        hits = index.search(session, item["question"], top_k=10)
        metrics = ranked_retrieval_metrics(hits, item.get("required_evidence", []))
        rows.append(
            {
                "id": item["id"],
                "category": item.get("category", "standard"),
                **metrics,
                "hits": [
                    {
                        "chunk_id": hit.chunk.id,
                        "locator": hit.chunk.locator,
                        "retrieval_score": hit.retrieval_score,
                        "dense_score": hit.dense_score,
                        "lexical_score": hit.lexical_score,
                        "fusion_method": hit.fusion_method,
                    }
                    for hit in hits
                ],
            }
        )
    metric_names = ["recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10"]
    return {
        "summary": {
            name: mean(row[name] for row in rows) if rows else 0 for name in metric_names
        },
        "by_category": {
            category: {
                name: mean(row[name] for row in rows if row["category"] == category)
                for name in metric_names
            }
            for category in sorted({row["category"] for row in rows})
        },
        "rows": rows,
    }


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
        event_data = [(event, json.loads(event.data_json)) for event in events]
        tool_names = {call.name for call in tool_calls}
        answer = run.answer or ""
        raw_answer = next(
            (
                data.get("answer", answer)
                for event, data in event_data
                if event.event_type == "answer_generated"
            ),
            answer,
        )
        citation_verification = next(
            (
                data
                for event, data in reversed(event_data)
                if event.event_type == "node_completed" and event.node == "citation_verifier"
            ),
            {},
        )
        citation_repair_triggered = bool(citation_verification.get("repaired", False))
        required = [term.lower() for term in item.get("required_evidence", [])]
        answerable = item.get("answerable", True)
        evidence_text = " ".join(record.content for record in evidence).lower()
        cited = {citation.evidence_id for citation in run.citations}
        faithful_cited = {
            citation.evidence_id for citation in run.citations if citation.valid
        }
        valid = {record.id.rsplit(":", 1)[-1] for record in evidence}
        precision = (
            len(faithful_cited & valid) / len(cited)
            if cited
            else float(not answerable)
        )
        cited_evidence_text = " ".join(
            record.content
            for record in evidence
            if record.id.rsplit(":", 1)[-1] in faithful_cited & valid
        ).lower()
        citation_recall = (
            sum(term in cited_evidence_text for term in required) / len(required)
            if required
            else float(not cited)
        )
        evidence_by_id = {
            record.id.rsplit(":", 1)[-1]: record for record in evidence
        }
        raw_cited = set(CITATION_RE.findall(raw_answer))
        raw_support_scores = {
            marker: ResearchAgent._claim_support_score(
                ResearchAgent._citation_claim(raw_answer, marker),
                evidence_by_id[marker].content,
            )
            for marker in raw_cited & valid
        }
        raw_faithful_cited = {
            marker
            for marker, score in raw_support_scores.items()
            if score >= agent.settings.citation_support_threshold
        }
        raw_citation_precision = (
            len(raw_faithful_cited) / len(raw_cited)
            if raw_cited
            else float(not answerable)
        )
        raw_cited_evidence_text = " ".join(
            evidence_by_id[marker].content for marker in raw_faithful_cited
        ).lower()
        raw_citation_recall = (
            sum(term in raw_cited_evidence_text for term in required) / len(required)
            if required
            else float(not raw_cited)
        )
        evidence_citation_coverage = len(faithful_cited & valid) / max(1, len(valid))
        metrics = json.loads(run.metrics_json)
        grades = [
            json.loads(event.data_json)
            for event in events
            if event.event_type == "evidence_graded"
        ]
        rewrite_quality_gain = (
            (
                grades[-1].get("coverage", 0)
                + grades[-1].get("relevance", 0)
                - grades[0].get("coverage", 0)
                - grades[0].get("relevance", 0)
            )
            / 2
            if len(grades) > 1
            else 0
        )
        abstained = not evidence or bool(
            re.search(
                r"^(?:#{1,3}\s*)?(?:结论\s*)?.{0,80}"
                r"(?:证据不足|insufficient evidence|cannot answer)",
                answer.strip(),
                re.IGNORECASE | re.DOTALL,
            )
        )
        raw_abstained = not evidence or bool(
            re.search(
                r"^(?:#{1,3}\s*)?(?:结论\s*)?.{0,80}"
                r"(?:证据不足|insufficient evidence|cannot answer)",
                raw_answer.strip(),
                re.IGNORECASE | re.DOTALL,
            )
        )
        row = {
            "id": item["id"],
            "mode": mode,
            "status": run.status,
            "correctness": (
                fact_coverage_score(item, raw_answer)
                if answerable
                else float(raw_abstained)
            ),
            "post_repair_correctness": (
                fact_coverage_score(item, answer) if answerable else float(abstained)
            ),
            "lexical_reference_recall": (
                overlap_score(item["reference_answer"], raw_answer)
                if answerable
                else float(raw_abstained)
            ),
            "post_repair_lexical_reference_recall": (
                overlap_score(item["reference_answer"], answer)
                if answerable
                else float(abstained)
            ),
            "raw_citation_precision": raw_citation_precision,
            "raw_citation_faithfulness": raw_citation_precision,
            "raw_citation_recall": raw_citation_recall,
            "citation_precision": precision,
            "citation_faithfulness": precision,
            "citation_recall": citation_recall,
            "evidence_citation_coverage": evidence_citation_coverage,
            "recall_at_k": (
                sum(term in evidence_text for term in required) / len(required)
                if required
                else float(not evidence)
            ),
            "answerable": answerable,
            "raw_abstained": raw_abstained,
            "abstained": abstained,
            "abstention_correct": float(raw_abstained != answerable),
            "post_repair_abstention_correct": float(abstained != answerable),
            "citation_repair_triggered": citation_repair_triggered,
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
            "rewrite_attempts": metrics.get("rewrite_attempts", 0),
            "rewrite_successes": metrics.get("rewrite_successes", 0),
            "rewrite_quality_gain": rewrite_quality_gain,
            "retrieval_context_tokens": metrics.get("retrieval_context_tokens", 0),
            "estimated_cost_usd": metrics.get("estimated_cost_usd", 0),
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
                "answer_rubric": item.get("answer_rubric"),
                "raw_answer": raw_answer,
                "answer": answer,
                "citation_repair_triggered": citation_repair_triggered,
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
                        "support_score": citation.support_score,
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
                        "error_type": call.error_type,
                        "retryable": bool(call.retryable),
                        "attempts": call.attempts,
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


def summarize(
    rows: list[dict[str, Any]], *, seed: int, bootstrap_samples: int
) -> dict[str, Any]:
    correctness_ci = bootstrap_mean_ci(
        [row["correctness"] for row in rows],
        seed=seed,
        samples=bootstrap_samples,
    )
    return {
        "questions": len(rows),
        "completed": sum(row["status"] == Status.COMPLETED for row in rows),
        "correctness": mean(row["correctness"] for row in rows),
        "correctness_ci95": {"low": correctness_ci[0], "high": correctness_ci[1]},
        "post_repair_correctness": mean(
            row["post_repair_correctness"] for row in rows
        ),
        "lexical_reference_recall": mean(
            row["lexical_reference_recall"] for row in rows
        ),
        "post_repair_lexical_reference_recall": mean(
            row["post_repair_lexical_reference_recall"] for row in rows
        ),
        "raw_citation_precision": mean(row["raw_citation_precision"] for row in rows),
        "raw_citation_faithfulness": mean(
            row["raw_citation_faithfulness"] for row in rows
        ),
        "raw_citation_recall": mean(row["raw_citation_recall"] for row in rows),
        "citation_precision": mean(row["citation_precision"] for row in rows),
        "citation_faithfulness": mean(row["citation_faithfulness"] for row in rows),
        "citation_recall": mean(row["citation_recall"] for row in rows),
        "evidence_citation_coverage": mean(
            row["evidence_citation_coverage"] for row in rows
        ),
        "recall_at_k": mean(row["recall_at_k"] for row in rows),
        "tool_accuracy": mean(row["tool_accuracy"] for row in rows),
        "average_steps": mean(row["agent_steps"] for row in rows),
        "average_tool_calls": mean(row["tool_calls"] for row in rows),
        "average_retrieval_rounds": mean(row["retrieval_rounds"] for row in rows),
        "rewrite_trigger_rate": mean(row["rewrite_attempts"] > 0 for row in rows),
        "rewrite_success_rate": (
            sum(row["rewrite_successes"] for row in rows)
            / max(1, sum(row["rewrite_attempts"] for row in rows))
        ),
        "average_rewrite_quality_gain": mean(
            row["rewrite_quality_gain"] for row in rows if row["rewrite_attempts"]
        )
        if any(row["rewrite_attempts"] for row in rows)
        else 0,
        "abstention_accuracy": mean(row["abstention_correct"] for row in rows),
        "post_repair_abstention_accuracy": mean(
            row["post_repair_abstention_correct"] for row in rows
        ),
        "citation_repair_rate": mean(
            row["citation_repair_triggered"] for row in rows
        ),
        "average_retrieval_context_tokens": mean(
            row["retrieval_context_tokens"] for row in rows
        ),
        "total_estimated_cost_usd": sum(row["estimated_cost_usd"] for row in rows),
        "average_tokens": mean(row["tokens"] for row in rows),
        "average_reranker_latency_ms": mean(row["reranker_latency_ms"] for row in rows),
        "p50_latency_ms": latency_percentile([row["latency_ms"] for row in rows], 0.5),
        "p95_latency_ms": latency_percentile([row["latency_ms"] for row in rows], 0.95),
    }


def compare_modes(
    rows: list[dict[str, Any]],
    modes: list[str],
    *,
    seed: int,
    bootstrap_samples: int,
) -> list[dict[str, Any]]:
    """Build paired deltas against the first mode using question IDs as pairs."""

    if len(modes) < 2:
        return []
    baseline_mode = modes[0]
    baseline = {
        row["id"]: row["correctness"] for row in rows if row["mode"] == baseline_mode
    }
    comparisons = []
    for offset, mode in enumerate(modes[1:], start=1):
        candidate = {
            row["id"]: row["correctness"] for row in rows if row["mode"] == mode
        }
        question_ids = sorted(baseline.keys() & candidate.keys())
        delta, low, high = paired_bootstrap_delta_ci(
            [baseline[item_id] for item_id in question_ids],
            [candidate[item_id] for item_id in question_ids],
            seed=seed + offset,
            samples=bootstrap_samples,
        )
        comparisons.append(
            {
                "baseline": baseline_mode,
                "candidate": mode,
                "questions": len(question_ids),
                "correctness_delta": delta,
                "correctness_delta_ci95": {"low": low, "high": high},
            }
        )
    return comparisons


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
        f"- 生成温度：`{config['temperature']}`",
        "- 主正确性：引用修复前答案的事实 rubric 覆盖率（95% bootstrap CI）",
        "- 词项参考答案召回：仅保留在 JSON/CSV 中作为诊断指标",
        "",
        "| 模式 | 原始正确性 [95% CI] | 修复后正确性 | 原始引用忠实度 | 原始引用召回 | 修复后引用召回 | 修复率 | Recall@K | 拒答准确率 | P50 ms | 平均 Tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in summary["configuration"]["modes"]:
        item = summary[mode]
        ci = item["correctness_ci95"]
        lines.append(
            f"| {mode.title()} | {item['correctness']:.3f} "
            f"[{ci['low']:.3f}, {ci['high']:.3f}] | "
            f"{item['post_repair_correctness']:.3f} | "
            f"{item['raw_citation_faithfulness']:.3f} | "
            f"{item['raw_citation_recall']:.3f} | {item['citation_recall']:.3f} | "
            f"{item['citation_repair_rate']:.3f} | {item['recall_at_k']:.3f} | "
            f"{item['abstention_accuracy']:.3f} | {item['p50_latency_ms']:.1f} | "
            f"{item['average_tokens']:.1f} |"
        )
    if summary.get("comparisons"):
        lines.extend(
            [
                "",
                "| 配对比较 | 原始正确性差值 [95% CI] | 题数 |",
                "|---|---:|---:|",
            ]
        )
        for comparison in summary["comparisons"]:
            ci = comparison["correctness_delta_ci95"]
            lines.append(
                f"| {comparison['candidate']} − {comparison['baseline']} | "
                f"{comparison['correctness_delta']:+.3f} "
                f"[{ci['low']:+.3f}, {ci['high']:+.3f}] | "
                f"{comparison['questions']} |"
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
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("naive", "fixed_retrieval", "agentic"),
        default=["naive", "fixed_retrieval", "agentic"],
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
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
    if not 0 <= args.temperature <= 2:
        parser.error("--temperature must be between 0 and 2")
    if args.bootstrap_samples < 100:
        parser.error("--bootstrap-samples must be at least 100")
    if args.run_name and not re.fullmatch(r"[A-Za-z0-9._-]+", args.run_name):
        parser.error("--run-name may contain only letters, digits, dot, underscore, and dash")
    return args


def main() -> None:
    args = parse_args()
    questions = json.loads(args.dataset.read_text())
    if not questions:
        raise SystemExit("the evaluator requires at least one question")
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
        "temperature": args.temperature,
        "bootstrap_samples": args.bootstrap_samples,
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
        "modes": args.modes,
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
            llm_temperature=args.temperature,
            llm_timeout_seconds=300 if args.provider == "live" else 0.05,
        )
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            chunks = seed_corpus(session, args.corpus)
            index = VectorIndex(settings)
            index.upsert(chunks)
            agent = ResearchAgent(settings, index, LLMProvider(settings))
            retrieval_benchmark = evaluate_retrieval(session, index, questions)
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
            rows: list[dict[str, Any]] = []
            details: list[dict[str, Any]] = []
            mode_summaries: dict[str, Any] = {}
            for mode in args.modes:
                mode_rows, mode_details = evaluate_mode(
                    session,
                    agent,
                    questions,
                    mode,
                    candidate_k=args.candidate_k,
                    evidence_k=args.evidence_k,
                )
                rows.extend(mode_rows)
                details.extend(mode_details)
                mode_summaries[mode] = summarize(
                    mode_rows,
                    seed=args.seed,
                    bootstrap_samples=args.bootstrap_samples,
                )
            comparisons = compare_modes(
                rows,
                args.modes,
                seed=args.seed,
                bootstrap_samples=args.bootstrap_samples,
            )
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
                "retrieval": retrieval_benchmark,
                "comparisons": comparisons,
                **mode_summaries,
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
