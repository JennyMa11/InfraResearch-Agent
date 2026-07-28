from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from threading import Lock, Thread
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .agent import ResearchAgent, latency_percentile
from .chunking import is_indexable
from .config import Settings, get_settings
from .database import SessionLocal, get_db
from .ingestion import IngestionService, parse_github_url
from .models import (
    CitationRecord,
    EvidenceRecord,
    Ingestion,
    ResearchRun,
    Source,
    Status,
    ToolCall,
    TraceEvent,
)
from .provider import LLMProvider
from .retrieval import VectorIndex
from .schemas import (
    Citation,
    Evidence,
    GitHubSourceIn,
    IngestionOut,
    MetricsSummary,
    ResearchPlan,
    ResearchRequest,
    ResearchRunOut,
    RunMetrics,
    SourceOut,
    ToolCallOut,
    TraceEventOut,
)

router = APIRouter(prefix="/api/v1")
running_threads: set[Thread] = set()
_thread_lock = Lock()


def _start_worker(target: Callable[..., None], *args: Any) -> None:
    def runner() -> None:
        try:
            target(*args)
        finally:
            with _thread_lock:
                running_threads.discard(thread)

    thread = Thread(target=runner, daemon=True)
    with _thread_lock:
        running_threads.add(thread)
    thread.start()


@lru_cache
def _services() -> tuple[Settings, VectorIndex, IngestionService, ResearchAgent]:
    settings = get_settings()
    index = VectorIndex(settings)
    return (
        settings,
        index,
        IngestionService(settings, index),
        ResearchAgent(settings, index, LLMProvider(settings)),
    )


def _run_file_ingestion(source_id: str, ingestion_id: str, path: str) -> None:
    _, _, service, _ = _services()
    with SessionLocal() as session:
        service.ingest_file(session, source_id, ingestion_id, Path(path))


def _run_github_ingestion(
    source_id: str, ingestion_id: str, revision: str | None, include_issues: bool
) -> None:
    _, _, service, _ = _services()
    with SessionLocal() as session:
        service.ingest_github(
            session,
            source_id,
            ingestion_id,
            revision=revision,
            include_issues=include_issues,
        )


def _run_research(run_id: str, top_k: int) -> None:
    _, _, _, agent = _services()
    with SessionLocal() as session:
        agent.execute(session, run_id, top_k=top_k)


def _source_out(source: Source) -> SourceOut:
    return SourceOut(
        id=source.id,
        kind=source.kind,
        name=source.name,
        uri=source.uri,
        status=source.status,
        revision=source.revision,
        error=source.error,
        metadata=json.loads(source.metadata_json),
        created_at=source.created_at,
    )


def _ingestion_out(item: Ingestion) -> IngestionOut:
    return IngestionOut(
        id=item.id,
        source_id=item.source_id,
        status=item.status,
        chunks_indexed=item.chunks_indexed,
        files_seen=item.files_seen,
        error=item.error,
        started_at=item.started_at,
        completed_at=item.completed_at,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    settings, index, _, _ = _services()
    return {
        "status": "ok",
        "version": "0.1.0",
        "vector_backend": index.backend,
        "embedding_model": settings.embedding_model,
    }


@router.post(
    "/sources/files",
    response_model=IngestionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_file(
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_db)],
) -> IngestionOut:
    settings = get_settings()
    safe_name = Path(file.filename or "upload").name
    if not is_indexable(Path(safe_name)):
        raise HTTPException(
            status_code=415,
            detail="unsupported file type; PDF, Markdown, text and common code files are accepted",
        )
    source = Source(kind="file", name=safe_name, uri=f"upload://{safe_name}")
    session.add(source)
    session.flush()
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.commit()
    upload_dir = settings.data_dir / "uploads" / source.id
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / safe_name
    size = 0
    with destination.open("wb") as handle:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_mb * 1024 * 1024:
                handle.close()
                destination.unlink(missing_ok=True)
                source.status = ingestion.status = Status.FAILED
                source.error = ingestion.error = f"file exceeds {settings.max_upload_mb} MB"
                session.commit()
                raise HTTPException(status_code=413, detail=source.error)
            handle.write(chunk)
    source.uri = str(destination)
    session.commit()
    _start_worker(_run_file_ingestion, source.id, ingestion.id, str(destination))
    return _ingestion_out(ingestion)


@router.post(
    "/sources/github",
    response_model=IngestionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_github_source(
    payload: GitHubSourceIn,
    session: Annotated[Session, Depends(get_db)],
) -> IngestionOut:
    url = str(payload.url).rstrip("/")
    owner, repo = parse_github_url(url)
    source = Source(kind="github", name=f"{owner}/{repo}", uri=url, revision=payload.revision)
    session.add(source)
    session.flush()
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.commit()
    _start_worker(
        _run_github_ingestion,
        source.id,
        ingestion.id,
        payload.revision,
        payload.include_issues,
    )
    return _ingestion_out(ingestion)


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(session: Annotated[Session, Depends(get_db)]) -> list[SourceOut]:
    sources = session.scalars(select(Source).order_by(Source.created_at.desc())).all()
    return [_source_out(source) for source in sources]


@router.get("/ingestions/{ingestion_id}", response_model=IngestionOut)
async def get_ingestion(
    ingestion_id: str, session: Annotated[Session, Depends(get_db)]
) -> IngestionOut:
    ingestion = session.get(Ingestion, ingestion_id)
    if not ingestion:
        raise HTTPException(status_code=404, detail="ingestion not found")
    return _ingestion_out(ingestion)


@router.post(
    "/research",
    response_model=ResearchRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_research(
    payload: ResearchRequest,
    session: Annotated[Session, Depends(get_db)],
) -> ResearchRunOut:
    run = ResearchRun(question=payload.question, mode=payload.mode)
    session.add(run)
    session.commit()
    _start_worker(_run_research, run.id, payload.top_k)
    return _run_out(session, run)


def _run_out(session: Session, run: ResearchRun) -> ResearchRunOut:
    evidence_records = session.scalars(
        select(EvidenceRecord).where(EvidenceRecord.run_id == run.id).order_by(EvidenceRecord.id)
    ).all()
    citations = session.scalars(select(CitationRecord).where(CitationRecord.run_id == run.id)).all()
    events = session.scalars(
        select(TraceEvent).where(TraceEvent.run_id == run.id).order_by(TraceEvent.sequence)
    ).all()
    tools = session.scalars(
        select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.id)
    ).all()
    return ResearchRunOut(
        id=run.id,
        question=run.question,
        mode=run.mode,
        status=run.status,
        answer=run.answer,
        plan=ResearchPlan.model_validate_json(run.plan_json),
        metrics=RunMetrics.model_validate_json(run.metrics_json),
        error=run.error,
        evidence=[
            Evidence(
                id=item.id.rsplit(":", 1)[-1],
                chunk_id=item.chunk_id,
                source_id=item.source_id,
                content=item.content,
                locator=item.locator,
                score=item.score,
                metadata=json.loads(item.metadata_json),
            )
            for item in evidence_records
        ],
        citations=[
            Citation(
                id=item.id,
                evidence_id=item.evidence_id,
                marker=item.marker,
                claim=item.claim,
                valid=bool(item.valid),
            )
            for item in citations
        ],
        events=[
            TraceEventOut(
                sequence=item.sequence,
                event_type=item.event_type,
                node=item.node,
                data=json.loads(item.data_json),
                created_at=item.created_at,
            )
            for item in events
        ],
        tool_calls=[
            ToolCallOut(
                id=item.id,
                name=item.name,
                arguments=json.loads(item.arguments_json),
                result_summary=item.result_summary,
                duration_ms=item.duration_ms,
                status=item.status,
                error=item.error,
            )
            for item in tools
        ],
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get("/research/{run_id}", response_model=ResearchRunOut)
async def get_research(
    run_id: str, session: Annotated[Session, Depends(get_db)]
) -> ResearchRunOut:
    run = session.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="research run not found")
    return _run_out(session, run)


@router.get("/research/{run_id}/events")
async def research_events(run_id: str) -> StreamingResponse:
    with SessionLocal() as session:
        if not session.get(ResearchRun, run_id):
            raise HTTPException(status_code=404, detail="research run not found")

    async def stream():
        last_sequence = 0
        idle_ticks = 0
        while idle_ticks < 1200:
            with SessionLocal() as session:
                events = session.scalars(
                    select(TraceEvent)
                    .where(TraceEvent.run_id == run_id, TraceEvent.sequence > last_sequence)
                    .order_by(TraceEvent.sequence)
                ).all()
                run = session.get(ResearchRun, run_id)
                for item in events:
                    last_sequence = item.sequence
                    body = {
                        "sequence": item.sequence,
                        "event_type": item.event_type,
                        "node": item.node,
                        "data": json.loads(item.data_json),
                        "created_at": item.created_at.isoformat(),
                    }
                    serialized = json.dumps(body, ensure_ascii=False)
                    yield (
                        f"id: {item.sequence}\nevent: {item.event_type}\n"
                        f"data: {serialized}\n\n"
                    )
                if run and run.status in {Status.COMPLETED, Status.FAILED} and not events:
                    break
            idle_ticks = 0 if events else idle_ticks + 1
            if not events:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/metrics/summary", response_model=MetricsSummary)
async def metrics_summary(session: Annotated[Session, Depends(get_db)]) -> MetricsSummary:
    runs = session.scalars(select(ResearchRun)).all()
    completed = [run for run in runs if run.status == Status.COMPLETED]
    metrics = [RunMetrics.model_validate_json(run.metrics_json) for run in completed]
    latencies = [item.total_latency_ms for item in metrics]
    return MetricsSummary(
        run_count=len(runs),
        completed_count=len(completed),
        p50_latency_ms=latency_percentile(latencies, 0.5),
        p95_latency_ms=latency_percentile(latencies, 0.95),
        total_tokens=sum(item.prompt_tokens + item.completion_tokens for item in metrics),
        total_tool_calls=session.scalar(select(func.count(ToolCall.id))) or 0,
        cache={
            "prefix_cache_hits": sum(item.prefix_cache_hits or 0 for item in metrics) or None,
            "prefix_cache_queries": sum(item.prefix_cache_queries or 0 for item in metrics) or None,
            "kv_cache_usage": None,
        },
    )
