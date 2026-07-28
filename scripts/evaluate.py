#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from infraresearch.agent import ResearchAgent, latency_percentile
from infraresearch.chunking import chunk_markdown
from infraresearch.config import Settings
from infraresearch.models import Base, Chunk, ResearchRun, Source, Status, ToolCall
from infraresearch.provider import LLMProvider
from infraresearch.retrieval import VectorIndex, tokenize
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]


def seed_corpus(session: Session, path: Path) -> None:
    source = Source(kind="file", name=path.name, uri=str(path), status=Status.COMPLETED)
    session.add(source)
    session.flush()
    for parsed in chunk_markdown(path.read_text(), path.name, size=1000, overlap=100):
        session.add(
            Chunk(
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
        )
    session.commit()


def overlap_score(reference: str, answer: str) -> float:
    required = set(tokenize(reference))
    actual = set(tokenize(answer))
    return len(required & actual) / max(1, len(required))


def evaluate_mode(session: Session, agent: ResearchAgent, questions: list[dict], mode: str) -> list[dict]:
    rows = []
    for item in questions:
        run = ResearchRun(question=item["question"], mode=mode)
        session.add(run)
        session.commit()
        agent.execute(session, run.id, top_k=6)
        session.refresh(run)
        evidence = list(run.evidence)
        tool_names = set(
            session.scalars(
                select(ToolCall.name).where(ToolCall.run_id == run.id)
            ).all()
        )
        answer = run.answer or ""
        required = [term.lower() for term in item["required_evidence"]]
        evidence_text = " ".join(record.content for record in evidence).lower()
        cited = {citation.evidence_id for citation in run.citations}
        valid = {record.id.rsplit(":", 1)[-1] for record in evidence}
        precision = len(cited & valid) / max(1, len(cited))
        recall = len(cited & valid) / max(1, len(valid))
        metrics = json.loads(run.metrics_json)
        rows.append(
            {
                "id": item["id"],
                "mode": mode,
                "status": run.status,
                "correctness": overlap_score(item["reference_answer"], answer),
                "citation_precision": precision,
                "citation_recall": recall,
                "recall_at_k": sum(term in evidence_text for term in required) / len(required),
                "tool_accuracy": float(
                    bool(tool_names & set(item["expected_tools"]))
                    or (mode == "naive" and "semantic_document_search" in tool_names)
                ),
                "agent_steps": metrics["agent_steps"],
                "tokens": metrics["prompt_tokens"] + metrics["completion_tokens"],
                "latency_ms": metrics["total_latency_ms"],
            }
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    return {
        "questions": len(rows),
        "correctness": mean(row["correctness"] for row in rows),
        "citation_precision": mean(row["citation_precision"] for row in rows),
        "citation_recall": mean(row["citation_recall"] for row in rows),
        "recall_at_k": mean(row["recall_at_k"] for row in rows),
        "tool_accuracy": mean(row["tool_accuracy"] for row in rows),
        "average_steps": mean(row["agent_steps"] for row in rows),
        "average_tokens": mean(row["tokens"] for row in rows),
        "p50_latency_ms": latency_percentile([row["latency_ms"] for row in rows], 0.5),
        "p95_latency_ms": latency_percentile([row["latency_ms"] for row in rows], 0.95),
    }


def markdown(summary: dict, generated_at: str) -> str:
    lines = [
        "# InfraResearch 双基线评测",
        "",
        "- 版本：`v0.1.0`",
        "- 数据集：20 题",
        f"- 运行时间：{generated_at}",
        "- Provider：确定性离线 extractive provider",
        "",
        "| 模式 | 正确性 | 引用精确率 | 引用召回率 | Recall@K | 工具准确率 | 平均步数 | P50 ms | P95 ms | 平均 Tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in ("naive", "agentic"):
        item = summary[mode]
        lines.append(
            f"| {mode.title()} | {item['correctness']:.3f} | "
            f"{item['citation_precision']:.3f} | {item['citation_recall']:.3f} | "
            f"{item['recall_at_k']:.3f} | {item['tool_accuracy']:.3f} | "
            f"{item['average_steps']:.2f} | {item['p50_latency_ms']:.1f} | "
            f"{item['p95_latency_ms']:.1f} | {item['average_tokens']:.1f} |"
        )
    lines.extend(
        [
            "",
            "> 本报告如实记录内置语料上的离线结果，不预设 Agentic RAG 必然优于 Naive RAG。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/questions.json")
    parser.add_argument("--corpus", type=Path, default=ROOT / "evals/corpus.md")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evals/results")
    args = parser.parse_args()
    questions = json.loads(args.dataset.read_text())
    if len(questions) != 20:
        raise SystemExit("v0.1.0 evaluator requires exactly 20 questions")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    with tempfile.TemporaryDirectory(prefix="infraresearch-eval-") as directory:
        settings = Settings(
            data_dir=Path(directory),
            database_url="sqlite://",
            vector_backend="sqlite",
            llm_base_url="http://127.0.0.1:1/v1",
            llm_timeout_seconds=0.05,
        )
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            seed_corpus(session, args.corpus)
            agent = ResearchAgent(settings, VectorIndex(settings), LLMProvider(settings))
            rows = evaluate_mode(session, agent, questions, "naive")
            rows += evaluate_mode(session, agent, questions, "agentic")
            summary = {
                "generated_at": generated_at,
                "dataset": str(args.dataset),
                "naive": summarize([row for row in rows if row["mode"] == "naive"]),
                "agentic": summarize([row for row in rows if row["mode"] == "agentic"]),
            }
    (args.output_dir / "comparison.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2)
    )
    with (args.output_dir / "comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = markdown(summary, generated_at)
    (args.output_dir / "comparison.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
