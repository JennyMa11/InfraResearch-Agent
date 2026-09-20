from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class SourceOut(BaseModel):
    id: str
    kind: str
    name: str
    uri: str
    status: str
    revision: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    active_ingestion_id: str | None = None
    created_at: datetime


class IngestionOut(BaseModel):
    id: str
    source_id: str
    status: str
    chunks_indexed: int
    files_seen: int
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class GitHubSourceIn(BaseModel):
    url: HttpUrl
    revision: str | None = None
    include_issues: bool = False


class ResearchRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    mode: Literal["naive", "agentic"] = "agentic"
    top_k: int = Field(default=6, ge=1, le=20)
    candidate_k: int | None = Field(default=None, ge=1, le=100)
    evidence_k: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def validate_retrieval_limits(self) -> ResearchRequest:
        evidence_k = self.evidence_k or self.top_k
        if self.candidate_k is not None and self.candidate_k < evidence_k:
            raise ValueError("candidate_k must be greater than or equal to evidence_k")
        return self


class SubQuestion(BaseModel):
    question: str
    expected_evidence: Literal["docs", "code", "issues", "mixed"] = "mixed"


class ResearchPlan(BaseModel):
    question_type: str = "technical"
    subquestions: list[SubQuestion] = Field(default_factory=list, max_length=3)


class Evidence(BaseModel):
    id: str
    chunk_id: str
    source_id: str
    content: str
    locator: str
    score: float
    retrieval_score: float
    rerank_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    id: str
    evidence_id: str
    marker: str
    claim: str
    valid: bool


class TraceEventOut(BaseModel):
    sequence: int
    event_type: str
    node: str | None
    data: dict[str, Any]
    created_at: datetime


class ToolCallOut(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]
    result_summary: str
    duration_ms: float
    status: str
    error: str | None


class RunMetrics(BaseModel):
    total_latency_ms: float = 0
    ttft_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    agent_steps: int = 0
    retrieval_rounds: int = 0
    prefix_cache_hits: int | None = None
    prefix_cache_queries: int | None = None
    kv_cache_usage: float | None = None
    provider: str = "unknown"
    vector_backend: str = "unknown"
    reranker: str = "identity"
    reranker_status: str = "disabled"
    reranker_latency_ms: float = 0
    candidate_k: int = 0
    evidence_k: int = 0


class ResearchRunOut(BaseModel):
    id: str
    question: str
    mode: str
    status: str
    answer: str | None
    plan: ResearchPlan
    metrics: RunMetrics
    error: str | None
    evidence: list[Evidence]
    citations: list[Citation]
    events: list[TraceEventOut]
    tool_calls: list[ToolCallOut]
    created_at: datetime
    completed_at: datetime | None


class ResearchRunSummary(BaseModel):
    id: str
    question: str
    mode: str
    status: str
    provider: str
    created_at: datetime
    completed_at: datetime | None


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class SourcePage(PageMeta):
    items: list[SourceOut]


class ResearchRunPage(PageMeta):
    items: list[ResearchRunSummary]


class MetricsSummary(BaseModel):
    run_count: int
    completed_count: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    total_tokens: int
    total_tool_calls: int
    cache: dict[str, int | float | None]
