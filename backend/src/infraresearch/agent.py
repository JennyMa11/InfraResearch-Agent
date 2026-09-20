from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .errors import TaskCancelled
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
from .reranking import IdentityReranker, Reranker, create_reranker
from .retrieval import SearchHit, VectorIndex, tokenize
from .schemas import Evidence, RunMetrics
from .tooling import ToolRegistry, create_tool_registry

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
    def __init__(
        self,
        settings: Settings,
        index: VectorIndex,
        provider: LLMProvider,
        reranker: Reranker | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.settings = settings
        self.index = index
        self.provider = provider
        self.reranker = reranker or create_reranker(settings)
        self.tool_registry = tool_registry or create_tool_registry(index)
        self._reranker_error: str | None = None

    def execute(
        self,
        session: Session,
        run_id: str,
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
        evidence_k: int | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        run = session.get(ResearchRun, run_id)
        if not run:
            return
        self._reranker_error = None
        writer = EventWriter(session, run_id)
        started = time.perf_counter()
        metrics = RunMetrics(vector_backend=self.index.backend)
        evidence_limit = evidence_k or top_k or self.settings.evidence_k
        candidate_limit = max(candidate_k or self.settings.candidate_k, evidence_limit)
        metrics.candidate_k = candidate_limit
        metrics.evidence_k = evidence_limit
        metrics.reranker = self.reranker.name
        metrics.reranker_status = (
            "disabled" if isinstance(self.reranker, IdentityReranker) else "ready"
        )
        run.status = Status.RUNNING
        session.commit()
        self._checkpoint(cancel_check)
        writer.emit("run_started", question=run.question, mode=run.mode)
        try:
            self._checkpoint(cancel_check)
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
                ranked_hits: list[SearchHit] = []
                queries = (
                    [run.question]
                    if run.mode == "naive"
                    else [item.question for item in plan.subquestions]
                )
                max_rounds = 1 if run.mode == "naive" else self.settings.max_agent_rewrites + 1
                for round_number in range(max_rounds):
                    self._checkpoint(cancel_check)
                    metrics.retrieval_rounds += 1
                    writer.emit(
                        "node_started",
                        "retriever",
                        round=round_number + 1,
                        queries=queries,
                    )
                    round_hits = self._retrieve(
                        session,
                        run,
                        queries,
                        plan,
                        candidate_limit,
                        writer,
                        agentic=run.mode == "agentic",
                        cancel_check=cancel_check,
                    )
                    metrics.tool_calls += len(queries if run.mode == "naive" else queries)
                    for hit in round_hits:
                        existing = all_hits.get(hit.chunk.id)
                        if not existing or existing.retrieval_score < hit.retrieval_score:
                            all_hits[hit.chunk.id] = hit
                    ranked_hits = self._rerank(
                        run.question,
                        list(all_hits.values()),
                        writer,
                        metrics,
                    )
                    writer.emit(
                        "node_completed",
                        "retriever",
                        round=round_number + 1,
                        candidates=len(all_hits),
                    )
                    metrics.agent_steps += 1
                    if run.mode == "naive":
                        break
                    sufficient, detail = self._grade(
                        run.question, ranked_hits[:evidence_limit]
                    )
                    writer.emit("evidence_graded", "evidence_grader", **detail)
                    metrics.agent_steps += 1
                    writer.emit(
                        "decision_made",
                        "evidence_grader",
                        action="generate" if sufficient else "retrieve_again",
                        reason=self._decision_reason(detail),
                        round=round_number + 1,
                    )
                    if sufficient:
                        break
                    if round_number < max_rounds - 1:
                        previous_queries = queries
                        queries = [
                            self._rewrite_query(item, round_number + 1)
                            for item in previous_queries
                        ]
                        writer.emit(
                            "query_rewritten",
                            "evidence_grader",
                            round=round_number + 1,
                            previous_queries=previous_queries,
                            queries=queries,
                            reason=self._decision_reason(detail),
                        )

                evidence = self._register_evidence(
                    session,
                    run_id,
                    ranked_hits[:evidence_limit],
                    writer,
                    cancel_check=cancel_check,
                )
                evidence = self._fit_token_budget(evidence)
                self._checkpoint(cancel_check)
                writer.emit("node_started", "generator", evidence_count=len(evidence))
                generation = self.provider.generate(
                    run.question,
                    evidence,
                    on_token=lambda token: writer.emit("token", "generator", text=token),
                    cancel_check=cancel_check,
                )
                self._checkpoint(cancel_check)
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
        except TaskCancelled:
            session.rollback()
            run = session.get(ResearchRun, run_id)
            if run:
                metrics.total_latency_ms = (time.perf_counter() - started) * 1000
                run.metrics_json = metrics.model_dump_json()
                self.mark_cancelled(session, run)
            raise
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
    def _checkpoint(cancel_check: Callable[[], None] | None) -> None:
        if cancel_check:
            cancel_check()

    @staticmethod
    def mark_cancelled(session: Session, run: ResearchRun) -> None:
        run.status = Status.CANCELLED
        run.error = None
        run.completed_at = datetime.now(UTC)
        session.commit()
        writer = EventWriter(session, run.id)
        writer.emit("run_cancelled")

    @staticmethod
    def _node(writer: EventWriter, node: str, state: str, **data: Any) -> None:
        writer.emit(f"node_{state}", node, **data)

    @staticmethod
    def _is_chitchat(question: str) -> bool:
        normalized = question.strip().lower()
        return normalized in {"hi", "hello", "你好", "嗨", "谢谢", "thanks"}

    def _rerank(
        self,
        query: str,
        hits: list[SearchHit],
        writer: EventWriter,
        metrics: RunMetrics,
    ) -> list[SearchHit]:
        started = time.perf_counter()
        status = "disabled" if isinstance(self.reranker, IdentityReranker) else "completed"
        error = self._reranker_error
        if error is not None:
            ranked = IdentityReranker().rerank(query, hits)
            status = "degraded"
        else:
            try:
                ranked = self.reranker.rerank(query, hits)
            except Exception as exc:
                ranked = IdentityReranker().rerank(query, hits)
                status = "degraded"
                error = str(exc)[:500]
                self._reranker_error = error
        duration = (time.perf_counter() - started) * 1000
        metrics.reranker_latency_ms += duration
        metrics.reranker_status = status
        retrieval_ranks = {
            hit.chunk.id: rank
            for rank, hit in enumerate(
                sorted(hits, key=lambda item: item.retrieval_score, reverse=True),
                start=1,
            )
        }
        writer.emit(
            "rerank_completed",
            "reranker",
            reranker=self.reranker.name,
            status=status,
            candidates=len(hits),
            duration_ms=round(duration, 2),
            error=error,
            ranking=[
                {
                    "chunk_id": hit.chunk.id,
                    "locator": hit.chunk.locator,
                    "retrieval_rank": retrieval_ranks[hit.chunk.id],
                    "rerank_rank": rerank_rank,
                    "movement": retrieval_ranks[hit.chunk.id] - rerank_rank,
                    "selected": rerank_rank <= metrics.evidence_k,
                    "retrieval_score": round(hit.retrieval_score, 4),
                    "rerank_score": (
                        round(hit.rerank_score, 4)
                        if hit.rerank_score is not None
                        else None
                    ),
                }
                for rerank_rank, hit in enumerate(ranked[:50], start=1)
            ],
        )
        return ranked

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
        cancel_check: Callable[[], None] | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for index, query in enumerate(queries):
            self._checkpoint(cancel_check)
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
                self._checkpoint(cancel_check)
                writer.emit(
                    "tool_started",
                    "retriever",
                    tool=tool_name,
                    query=query,
                    top_k=top_k,
                )
                result = self.tool_registry.execute(
                    session,
                    tool_name,
                    {"query": query, "top_k": top_k},
                    cancel_check=cancel_check,
                )
                tool_hits = result.hits
                for hit in tool_hits:
                    hit.query = query
                hits.extend(tool_hits)
                call = ToolCall(
                    run_id=run.id,
                    name=tool_name,
                    arguments_json=json.dumps({"query": query, "top_k": top_k}, ensure_ascii=False),
                    result_summary=result.summary,
                    duration_ms=result.duration_ms,
                    status=result.status,
                    error=result.error,
                )
                session.add(call)
                session.commit()
                sources = sorted({hit.chunk.source_id for hit in tool_hits})
                top_score = max(
                    (hit.retrieval_score for hit in tool_hits), default=None
                )
                writer.emit(
                    "tool_completed",
                    "retriever",
                    tool=tool_name,
                    call_id=call.id,
                    results=len(tool_hits),
                    duration_ms=round(result.duration_ms, 2),
                    status=result.status,
                    error=result.error,
                    attempts=result.attempts,
                )
                writer.emit(
                    "observation_created",
                    "retriever",
                    tool=tool_name,
                    call_id=call.id,
                    query=query,
                    results=len(tool_hits),
                    sources=sources,
                    top_score=round(top_score, 4) if top_score is not None else None,
                    summary=call.result_summary,
                    status=result.status,
                    error=result.error,
                )
        return hits

    @staticmethod
    def _grade(question: str, hits: list[SearchHit]) -> tuple[bool, dict[str, Any]]:
        if not hits:
            return False, {
                "sufficient": False,
                "relevance": 0,
                "coverage": 0,
                "diversity": 0,
                "thresholds": {
                    "coverage": 0.35,
                    "relevance": 0.12,
                    "strong_relevance": 0.55,
                },
            }
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
            "thresholds": {
                "coverage": 0.35,
                "relevance": 0.12,
                "strong_relevance": 0.55,
            },
        }

    @staticmethod
    def _decision_reason(detail: dict[str, Any]) -> str:
        if detail.get("sufficient"):
            return (
                "evidence met the configured coverage/relevance threshold; "
                "continue to generation"
            )
        return (
            f"evidence below threshold: coverage={detail.get('coverage', 0)}, "
            f"relevance={detail.get('relevance', 0)}; rewrite and retrieve again"
        )

    @staticmethod
    def _rewrite_query(query: str, round_number: int) -> str:
        suffixes = ["documentation implementation configuration", "error behavior example source"]
        return f"{query} {suffixes[min(round_number - 1, len(suffixes) - 1)]}"

    def _register_evidence(
        self,
        session: Session,
        run_id: str,
        hits: list[SearchHit],
        writer: EventWriter,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        seen_content: set[str] = set()
        for hit in hits:
            self._checkpoint(cancel_check)
            content_key = hit.chunk.content_hash
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            evidence_id = f"S{len(evidence) + 1}"
            item = Evidence(
                id=evidence_id,
                chunk_id=hit.chunk.id,
                source_id=hit.chunk.source_id,
                content=hit.chunk.content,
                locator=hit.chunk.locator,
                score=hit.score,
                retrieval_score=hit.retrieval_score,
                rerank_score=hit.rerank_score,
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
                    retrieval_score=item.retrieval_score,
                    rerank_score=item.rerank_score,
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
                retrieval_score=round(item.retrieval_score, 3),
                rerank_score=(
                    round(item.rerank_score, 3)
                    if item.rerank_score is not None
                    else None
                ),
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
    def _citation_claim(answer: str, marker: str) -> str:
        rendered = f"[{marker}]"
        for line in answer.splitlines():
            if rendered in line:
                claim = line.replace(rendered, "").strip().lstrip("#-* ").strip()
                if claim:
                    return claim[:500]
        return "Evidence reference"

    def _verify_citations(
        self,
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
        if invalid and self.settings.citation_repair_enabled:
            answer = run.answer or ""
            for marker in invalid:
                answer = answer.replace(f"[{marker}]", "")
            run.answer = answer
            cited = CITATION_RE.findall(answer)
            repaired = True
        if evidence and not cited and self.settings.citation_repair_enabled:
            fallback = self.provider._extractive_generate(run.question, evidence)
            run.answer = (
                fallback.text
                + "\n\n> 引用修复：原生成内容没有提供有效行内引用，"
                "已改用登记证据生成可验证结果。"
            )
            cited = CITATION_RE.findall(run.answer)
            repaired = True
        for marker in dict.fromkeys(cited):
            session.add(
                CitationRecord(
                    run_id=run.id,
                    evidence_id=marker,
                    marker=f"[{marker}]",
                    claim=self._citation_claim(run.answer or "", marker),
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
            repair_enabled=self.settings.citation_repair_enabled,
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
