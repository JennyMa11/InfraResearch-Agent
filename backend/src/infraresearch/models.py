from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceType(StrEnum):
    FILE = "file"
    GITHUB = "github"
    ISSUE = "issue"
    WEB = "web"
    WEB_SEARCH = "web_search"


class Status(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("src"))
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(512))
    uri: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=Status.PENDING)
    revision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    chunks: Mapped[list[Chunk]] = relationship(cascade="all, delete-orphan")


class Ingestion(Base):
    __tablename__ = "ingestions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("ing"))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(String(20), default=Status.PENDING)
    chunks_indexed: Mapped[int] = mapped_column(Integer, default=0)
    files_seen: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String(20), index=True)
    target_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default=Status.PENDING, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("chk"))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("run"))
    question: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=Status.PENDING)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    events: Mapped[list[TraceEvent]] = relationship(cascade="all, delete-orphan")
    evidence: Mapped[list[EvidenceRecord]] = relationship(cascade="all, delete-orphan")
    citations: Mapped[list[CitationRecord]] = relationship(cascade="all, delete-orphan")


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40))
    node: Mapped[str | None] = mapped_column(String(80), nullable=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("tool"))
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    arguments_json: Mapped[str] = mapped_column(Text)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default=Status.COMPLETED)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    retryable: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)


class EvidenceRecord(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    content: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    retrieval_score: Mapped[float] = mapped_column(Float, default=0)
    rerank_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class CitationRecord(Base):
    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("cit"))
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(80))
    marker: Mapped[str] = mapped_column(String(20))
    claim: Mapped[str] = mapped_column(Text)
    valid: Mapped[int] = mapped_column(Integer, default=1)
    support_score: Mapped[float] = mapped_column(Float, default=0)
