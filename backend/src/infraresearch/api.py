from __future__ import annotations

import asyncio
import json
import shutil
from math import ceil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__
from .agent import latency_percentile
from .chunking import is_indexable
from .config import get_settings
from .database import SessionLocal, get_db
from .ingestion import parse_github_url
from .models import (
    CitationRecord,
    EvidenceRecord,
    Ingestion,
    Job,
    ResearchRun,
    Source,
    Status,
    ToolCall,
    TraceEvent,
    utcnow,
)
from .schemas import (
    Citation,
    Evidence,
    GitHubSourceIn,
    IngestionOut,
    MetricsSummary,
    ResearchPlan,
    ResearchRequest,
    ResearchRunOut,
    ResearchRunPage,
    ResearchRunSummary,
    RunMetrics,
    SourceOut,
    SourcePage,
    ToolCallOut,
    TraceEventOut,
)
from .task_queue import enqueue_job, request_cancellation

router = APIRouter(prefix="/api/v1")


def _source_metadata(source: Source) -> dict[str, Any]:
    try:
        return json.loads(source.metadata_json)
    except (TypeError, ValueError):
        return {}


def _source_out(source: Source, active_ingestion_id: str | None = None) -> SourceOut:
    return SourceOut(
        id=source.id,
        kind=source.kind,
        name=source.name,
        uri=source.uri,
        status=source.status,
        revision=source.revision,
        error=source.error,
        metadata=_source_metadata(source),
        active_ingestion_id=active_ingestion_id,
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
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "vector_backend": (
            "qdrant_local" if settings.vector_backend == "qdrant" else "sqlite_lexical"
        ),
        "embedding_backend": (
            settings.embedding_backend if settings.vector_backend == "qdrant" else "none"
        ),
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
    enqueue_job(session, "ingestion", ingestion.id)
    session.commit()
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
    try:
        owner, repo = parse_github_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = Source(
        kind="github",
        name=f"{owner}/{repo}",
        uri=url,
        revision=payload.revision,
        metadata_json=json.dumps(
            {
                "include_issues": payload.include_issues,
                "requested_revision": payload.revision,
            }
        ),
    )
    session.add(source)
    session.flush()
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.flush()
    enqueue_job(session, "ingestion", ingestion.id)
    session.commit()
    return _ingestion_out(ingestion)


@router.get("/sources", response_model=SourcePage)
async def list_sources(
    session: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(alias="q", max_length=200)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    kind: Annotated[str | None, Query()] = None,
) -> SourcePage:
    filters = [Source.status != Status.DELETED]
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(Source.name.ilike(pattern) | Source.uri.ilike(pattern))
    if status_filter:
        filters.append(Source.status == status_filter)
    if kind:
        filters.append(Source.kind == kind)
    total = session.scalar(select(func.count(Source.id)).where(*filters)) or 0
    sources = session.scalars(
        select(Source)
        .where(*filters)
        .order_by(Source.created_at.desc(), Source.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    source_ids = [source.id for source in sources]
    active = {}
    if source_ids:
        active = {
            source_id: ingestion_id
            for source_id, ingestion_id in session.execute(
                select(Ingestion.source_id, Ingestion.id).where(
                    Ingestion.source_id.in_(source_ids),
                    Ingestion.status.in_(
                        [Status.PENDING, Status.RUNNING, Status.CANCEL_REQUESTED]
                    ),
                )
            )
        }
    return SourcePage(
        items=[_source_out(source, active.get(source.id)) for source in sources],
        page=page,
        page_size=page_size,
        total=total,
        pages=ceil(total / page_size) if total else 0,
    )


def _active_ingestion(session: Session, source_id: str) -> Ingestion | None:
    return session.scalar(
        select(Ingestion).where(
            Ingestion.source_id == source_id,
            Ingestion.status.in_(
                [Status.PENDING, Status.RUNNING, Status.CANCEL_REQUESTED]
            ),
        )
    )


@router.post(
    "/sources/{source_id}/reindex",
    response_model=IngestionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_source(
    source_id: str, session: Annotated[Session, Depends(get_db)]
) -> IngestionOut:
    source = session.get(Source, source_id)
    if not source or source.status == Status.DELETED:
        raise HTTPException(status_code=404, detail="source not found")
    if source.kind not in {"file", "github"}:
        raise HTTPException(status_code=409, detail="this source is managed by its repository")
    if _active_ingestion(session, source.id):
        raise HTTPException(status_code=409, detail="source ingestion is already active")
    if source.kind == "file" and not Path(source.uri).is_file():
        raise HTTPException(status_code=409, detail="uploaded source file is no longer available")

    source.status = Status.PENDING
    source.error = None
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.flush()
    enqueue_job(session, "ingestion", ingestion.id)
    session.commit()
    return _ingestion_out(ingestion)


def _remove_managed_directory(path: Path, root: Path) -> None:
    resolved = path.resolve()
    managed_root = root.resolve()
    if resolved != managed_root and managed_root in resolved.parents and resolved.exists():
        shutil.rmtree(resolved)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: str, session: Annotated[Session, Depends(get_db)]
) -> Response:
    settings = get_settings()
    source = session.get(Source, source_id)
    if not source or source.status == Status.DELETED:
        raise HTTPException(status_code=404, detail="source not found")
    if _active_ingestion(session, source.id):
        raise HTTPException(
            status_code=409,
            detail="cannot delete a source while ingestion is active",
        )

    targets = [source]
    if source.kind == "github":
        targets.extend(
            item
            for item in session.scalars(select(Source).where(Source.kind == "issue")).all()
            if _source_metadata(item).get("parent_repository") == source.name
        )
    for item in targets:
        item.status = Status.DELETED
        item.error = None
    enqueue_job(
        session,
        "source_cleanup",
        source.id,
        {"source_ids": [item.id for item in targets]},
    )

    if source.kind == "file":
        _remove_managed_directory(Path(source.uri).parent, settings.data_dir / "uploads")
    elif source.kind == "github":
        _remove_managed_directory(settings.repos_path / source.id, settings.repos_path)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/ingestions/{ingestion_id}", response_model=IngestionOut)
async def get_ingestion(
    ingestion_id: str, session: Annotated[Session, Depends(get_db)]
) -> IngestionOut:
    ingestion = session.get(Ingestion, ingestion_id)
    if not ingestion:
        raise HTTPException(status_code=404, detail="ingestion not found")
    return _ingestion_out(ingestion)


def _job_for_target(session: Session, target_id: str) -> Job | None:
    return session.scalar(select(Job).where(Job.target_id == target_id))


@router.post("/ingestions/{ingestion_id}/cancel", response_model=IngestionOut)
async def cancel_ingestion(
    ingestion_id: str,
    session: Annotated[Session, Depends(get_db)],
) -> IngestionOut:
    ingestion = session.get(Ingestion, ingestion_id)
    if not ingestion:
        raise HTTPException(status_code=404, detail="ingestion not found")
    if ingestion.status == Status.CANCELLED:
        return _ingestion_out(ingestion)
    if ingestion.status not in {Status.PENDING, Status.RUNNING, Status.CANCEL_REQUESTED}:
        raise HTTPException(status_code=409, detail="ingestion is no longer active")
    job = _job_for_target(session, ingestion.id)
    if not job:
        raise HTTPException(status_code=409, detail="ingestion job not found")
    job_status = request_cancellation(session, job)
    ingestion.status = job_status
    source = session.get(Source, ingestion.source_id)
    if source and source.status != Status.DELETED:
        source.status = job_status
    if job_status == Status.CANCELLED:
        ingestion.completed_at = utcnow()
    session.commit()
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
    session.flush()
    enqueue_job(session, "research", run.id, {"top_k": payload.top_k})
    session.commit()
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


@router.get("/research", response_model=ResearchRunPage)
async def list_research(
    session: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(alias="q", max_length=200)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    mode: Annotated[str | None, Query()] = None,
) -> ResearchRunPage:
    filters = []
    if query:
        filters.append(ResearchRun.question.ilike(f"%{query.strip()}%"))
    if status_filter:
        filters.append(ResearchRun.status == status_filter)
    if mode:
        filters.append(ResearchRun.mode == mode)
    total = session.scalar(select(func.count(ResearchRun.id)).where(*filters)) or 0
    runs = session.scalars(
        select(ResearchRun)
        .where(*filters)
        .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ResearchRunPage(
        items=[
            ResearchRunSummary(
                id=run.id,
                question=run.question,
                mode=run.mode,
                status=run.status,
                provider=RunMetrics.model_validate_json(run.metrics_json).provider,
                created_at=run.created_at,
                completed_at=run.completed_at,
            )
            for run in runs
        ],
        page=page,
        page_size=page_size,
        total=total,
        pages=ceil(total / page_size) if total else 0,
    )


@router.post(
    "/research/{run_id}/retry",
    response_model=ResearchRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_research(
    run_id: str, session: Annotated[Session, Depends(get_db)]
) -> ResearchRunOut:
    previous = session.get(ResearchRun, run_id)
    if not previous:
        raise HTTPException(status_code=404, detail="research run not found")
    if previous.status in {Status.PENDING, Status.RUNNING, Status.CANCEL_REQUESTED}:
        raise HTTPException(status_code=409, detail="research run is still active")
    run = ResearchRun(question=previous.question, mode=previous.mode)
    session.add(run)
    session.flush()
    enqueue_job(session, "research", run.id, {"top_k": 6})
    session.commit()
    return _run_out(session, run)


@router.post("/research/{run_id}/cancel", response_model=ResearchRunOut)
async def cancel_research(
    run_id: str,
    session: Annotated[Session, Depends(get_db)],
) -> ResearchRunOut:
    run = session.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="research run not found")
    if run.status == Status.CANCELLED:
        return _run_out(session, run)
    if run.status not in {Status.PENDING, Status.RUNNING, Status.CANCEL_REQUESTED}:
        raise HTTPException(status_code=409, detail="research run is no longer active")
    job = _job_for_target(session, run.id)
    if not job:
        raise HTTPException(status_code=409, detail="research job not found")
    job_status = request_cancellation(session, job)
    run.status = job_status
    if job_status == Status.CANCELLED:
        run.completed_at = utcnow()
        sequence = (
            session.scalar(
                select(func.max(TraceEvent.sequence)).where(TraceEvent.run_id == run.id)
            )
            or 0
        )
        session.add(
            TraceEvent(
                run_id=run.id,
                sequence=sequence + 1,
                event_type="run_cancelled",
                data_json="{}",
            )
        )
    session.commit()
    return _run_out(session, run)


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
                if run and run.status in {
                    Status.COMPLETED,
                    Status.FAILED,
                    Status.CANCELLED,
                } and not events:
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
