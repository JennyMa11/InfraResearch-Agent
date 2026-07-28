from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .metrics import fetch_vllm_metrics
from .models import (
    CitationRecord,
    EvidenceRecord,
    ResearchRun,
    Status,
    ToolCall,
    TraceEvent,
)
from .provider import LLMProvider
from .retrieval import SearchHit, VectorIndex, tokenize
from .schemas import Evidence, RunMetrics

CITATION_RE = re.compile(r"\[(S\d+)]")


class EventWriter:
    def __init__(self, session: Session, run_id: str):
        self.session = session
        self.run_id = run_id
        self.sequence = (
            session.scalar(select(func.max(TraceEvent.sequence)).where(TraceEvent.run_id == run_id))
            or 0
        )

    def emit(self, event_type: str, node: str | None = None, **data: Any) -> None:
        self.sequence += 1
        self.session.add(
            TraceEvent(
                run_id=self.run_id,
                sequence=self.sequence,
                event_type=event_type,
                node=node,
                data_json=json.dumps(data, ensure_ascii=False),
            )
        )
        self.session.commit()


class ResearchAgent:
    def __init__(self, settings: Settings, index: VectorIndex, provider: LLMProvider):
        self.settings = settings
        self.index = index
        self.provider = provider

    def execute(self, session: Session, run_id: str, *, top_k: int = 6) -> None:
        run = session.get(ResearchRun, run_id)
        if not run:
            return
        writer = EventWriter(session, run_id)
        started = time.perf_counter()
        metrics = RunMetrics(vector_backend=self.index.backend)
        run.status = Status.RUNNING
        session.commit()
        writer.emit("run_started", question=run.question, mode=run.mode)
        try:
            self._node(writer, "router", "started")
            needs_retrieval = not self._is_chitchat(run.question)
            self._node(writer, "router", "completed", needs_retrieval=needs_retrieval)
            metrics.agent_steps += 1

            if not needs_retrieval:
                plan = self.provider.plan(run.question)
                run.plan_json = plan.model_dump_json()
                run.answer = "你好，我可以基于已导入的技术文档、代码和 Issue 进行研究。"
                metrics.provider = "rule_based"
                writer.emit("token", "generator", text=run.answer)
            else:
                self._node(writer, "planner", "started")
                plan = self.provider.plan(run.question)
                run.plan_json = plan.model_dump_json()
                writer.emit("plan_created", "planner", plan=plan.model_dump())
                self._node(writer, "planner", "completed")
                metrics.agent_steps += 1

                all_hits: dict[str, SearchHit] = {}
                queries = (
                    [run.question]
                    if run.mode == "naive"
                    else [item.question for item in plan.subquestions]
                )
                max_rounds = 1 if run.mode == "naive" else self.settings.max_agent_rewrites + 1
                for round_number in range(max_rounds):
                    metrics.retrieval_rounds += 1
                    writer.emit(
                        "node_started",
                        "retriever",
                        round=round_number + 1,
                        queries=queries,
                    )
                    round_hits = self._retrieve(
                        session, run, queries, plan, top_k, writer, agentic=run.mode == "agentic"
                    )
                    metrics.tool_calls += len(queries if run.mode == "naive" else queries)
                    for hit in round_hits:
                        existing = all_hits.get(hit.chunk.id)
                        if not existing or existing.score < hit.score:
                            all_hits[hit.chunk.id] = hit
                    writer.emit(
                        "node_completed",
                        "retriever",
                        round=round_number + 1,
                        candidates=len(all_hits),
                    )
                    metrics.agent_steps += 1
                    if run.mode == "naive":
                        break
                    sufficient, detail = self._grade(run.question, list(all_hits.values()))
                    writer.emit("evidence_graded", "evidence_grader", **detail)
                    metrics.agent_steps += 1
                    if sufficient:
                        break
                    if round_number < max_rounds - 1:
                        queries = [self._rewrite_query(item, round_number + 1) for item in queries]
                        writer.emit(
                            "query_rewritten",
                            "evidence_grader",
                            round=round_number + 1,
                            queries=queries,
                        )

                evidence = self._register_evidence(
                    session,
                    run_id,
                    sorted(all_hits.values(), key=lambda item: item.score, reverse=True)[:top_k],
                    writer,
                )
                evidence = self._fit_token_budget(evidence)
                writer.emit("node_started", "generator", evidence_count=len(evidence))
                generation = self.provider.generate(
                    run.question,
                    evidence,
                    on_token=lambda token: writer.emit("token", "generator", text=token),
                )
                run.answer = generation.text
                metrics.prompt_tokens = generation.prompt_tokens
                metrics.completion_tokens = generation.completion_tokens
                metrics.ttft_ms = generation.ttft_ms
                metrics.provider = generation.provider
                metrics.agent_steps += 1
                writer.emit(
                    "node_completed",
                    "generator",
                    provider=generation.provider,
                    completion_tokens=generation.completion_tokens,
                )
                self._verify_citations(session, run, evidence, writer)
                metrics.agent_steps += 1

            metrics.total_latency_ms = (time.perf_counter() - started) * 1000
            if metrics.provider == "openai_compatible":
                cache = fetch_vllm_metrics(self.settings)
                metrics.prefix_cache_hits = cache["prefix_cache_hits"]
                metrics.prefix_cache_queries = cache["prefix_cache_queries"]
                metrics.kv_cache_usage = cache["kv_cache_usage"]
            metrics.tool_calls = (
                session.scalar(
                    select(func.count(ToolCall.id)).where(ToolCall.run_id == run.id)
                )
                or 0
            )
            run.metrics_json = metrics.model_dump_json()
            run.status = Status.COMPLETED
            run.completed_at = datetime.now(UTC)
            session.commit()
            writer.emit("run_completed", metrics=metrics.model_dump())
        except Exception as exc:
            session.rollback()
            run = session.get(ResearchRun, run_id)
            if run:
                metrics.total_latency_ms = (time.perf_counter() - started) * 1000
                run.status = Status.FAILED
                run.error = str(exc)[:2000]
                run.metrics_json = metrics.model_dump_json()
                run.completed_at = datetime.now(UTC)
                session.commit()
                writer = EventWriter(session, run_id)
                writer.emit("run_failed", error=run.error)

    @staticmethod
    def _node(writer: EventWriter, node: str, state: str, **data: Any) -> None:
        writer.emit(f"node_{state}", node, **data)

    @staticmethod
    def _is_chitchat(question: str) -> bool:
        normalized = question.strip().lower()
        return normalized in {"hi", "hello", "你好", "嗨", "谢谢", "thanks"}

    def _retrieve(
        self,
        session: Session,
        run: ResearchRun,
        queries: list[str],
        plan: Any,
        top_k: int,
        writer: EventWriter,
        *,
        agentic: bool,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for index, query in enumerate(queries):
            expected = (
                plan.subquestions[min(index, len(plan.subquestions) - 1)].expected_evidence
                if plan.subquestions
                else "mixed"
            )
            tool_names = ["semantic_document_search"]
            if agentic and expected in {"code", "mixed"}:
                tool_names.append("code_keyword_search")
            if agentic and expected in {"issues", "mixed"}:
                tool_names.append("github_issue_search")
            for tool_name in tool_names:
                started = time.perf_counter()
                status, error = Status.COMPLETED, None
                try:
                    if tool_name == "semantic_document_search":
                        tool_hits = self.index.search(session, query, top_k)
                    elif tool_name == "code_keyword_search":
                        tool_hits = self.index.keyword_search(session, query, top_k)
                    else:
                        tool_hits = self.index.issue_search(session, query, top_k)
                    hits.extend(tool_hits)
                except Exception as exc:
                    tool_hits = []
                    status, error = Status.FAILED, str(exc)[:500]
                duration = (time.perf_counter() - started) * 1000
                call = ToolCall(
                    run_id=run.id,
                    name=tool_name,
                    arguments_json=json.dumps({"query": query, "top_k": top_k}, ensure_ascii=False),
                    result_summary=f"{len(tool_hits)} results",
                    duration_ms=duration,
                    status=status,
                    error=error,
                )
                session.add(call)
                session.commit()
                writer.emit(
                    "tool_completed",
                    "retriever",
                    tool=tool_name,
                    call_id=call.id,
                    results=len(tool_hits),
                    duration_ms=round(duration, 2),
                    status=status,
                    error=error,
                )
        return hits

    @staticmethod
    def _grade(question: str, hits: list[SearchHit]) -> tuple[bool, dict[str, Any]]:
        if not hits:
            return False, {"sufficient": False, "relevance": 0, "coverage": 0, "diversity": 0}
        query_tokens = set(tokenize(question))
        evidence_tokens = set()
        sources = set()
        for hit in hits[:8]:
            evidence_tokens.update(tokenize(hit.chunk.content))
            sources.add(hit.chunk.source_id)
        coverage = len(query_tokens & evidence_tokens) / max(1, len(query_tokens))
        relevance = sum(hit.score for hit in hits[:5]) / min(5, len(hits))
        diversity = min(1.0, len(sources) / 2)
        sufficient = (coverage >= 0.35 and relevance >= 0.12) or relevance >= 0.55
        return sufficient, {
            "sufficient": sufficient,
            "relevance": round(relevance, 3),
            "coverage": round(coverage, 3),
            "diversity": round(diversity, 3),
        }

    @staticmethod
    def _rewrite_query(query: str, round_number: int) -> str:
        suffixes = ["documentation implementation configuration", "error behavior example source"]
        return f"{query} {suffixes[min(round_number - 1, len(suffixes) - 1)]}"

    @staticmethod
    def _register_evidence(
        session: Session, run_id: str, hits: list[SearchHit], writer: EventWriter
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        for index, hit in enumerate(hits, start=1):
            evidence_id = f"S{index}"
            item = Evidence(
                id=evidence_id,
                chunk_id=hit.chunk.id,
                source_id=hit.chunk.source_id,
                content=hit.chunk.content,
                locator=hit.chunk.locator,
                score=hit.score,
                metadata=json.loads(hit.chunk.metadata_json),
            )
            session.add(
                EvidenceRecord(
                    id=f"{run_id}:{evidence_id}",
                    run_id=run_id,
                    chunk_id=item.chunk_id,
                    source_id=item.source_id,
                    content=item.content,
                    locator=item.locator,
                    score=item.score,
                    metadata_json=json.dumps(item.metadata),
                )
            )
            evidence.append(item)
            writer.emit(
                "evidence_added",
                "retriever",
                evidence_id=evidence_id,
                locator=item.locator,
                score=round(item.score, 3),
            )
        session.commit()
        return evidence

    def _fit_token_budget(self, evidence: list[Evidence]) -> list[Evidence]:
        """Bound evidence context without changing registered locators or IDs."""
        remaining = max(256, self.settings.token_budget - 800)
        fitted: list[Evidence] = []
        for item in evidence:
            estimated = max(1, len(item.content) // 4)
            if remaining <= 0:
                break
            if estimated > remaining:
                item = item.model_copy(update={"content": item.content[: remaining * 4]})
                estimated = remaining
            fitted.append(item)
            remaining -= estimated
        return fitted

    @staticmethod
    def _verify_citations(
        session: Session,
        run: ResearchRun,
        evidence: list[Evidence],
        writer: EventWriter,
    ) -> None:
        writer.emit("node_started", "citation_verifier")
        valid_ids = {item.id for item in evidence if item.locator}
        cited = CITATION_RE.findall(run.answer or "")
        invalid = sorted(set(cited) - valid_ids)
        repaired = False
        if invalid:
            answer = run.answer or ""
            for marker in invalid:
                answer = answer.replace(f"[{marker}]", "")
            answer += "\n\n> 证据不足：已移除无法对应已登记证据的引用。"
            run.answer = answer
            repaired = True
            cited = CITATION_RE.findall(answer)
        if evidence and not cited:
            run.answer = (run.answer or "") + "\n\n> 证据不足：生成内容没有提供可验证的行内引用。"
        for marker in dict.fromkeys(cited):
            session.add(
                CitationRecord(
                    run_id=run.id,
                    evidence_id=marker,
                    marker=f"[{marker}]",
                    claim="Referenced claim",
                    valid=int(marker in valid_ids),
                )
            )
        session.commit()
        writer.emit(
            "node_completed",
            "citation_verifier",
            citations=len(cited),
            invalid=invalid,
            repaired=repaired,
            repair_attempts=1 if repaired else 0,
        )


def latency_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if percentile == 0.5:
        return median(ordered)
    index = min(len(ordered) - 1, max(0, math_ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def math_ceil(value: float) -> int:
    integer = int(value)
    return integer if value == integer else integer + 1
