from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

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
)


def enqueue_job(
    session: Session,
    kind: str,
    target_id: str,
    payload: dict[str, Any] | None = None,
) -> Job:
    job = Job(
        kind=kind,
        target_id=target_id,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    session.add(job)
    return job


def claim_next_job(session: Session, worker_id: str) -> Job | None:
    """Atomically claim the oldest pending job.

    The compare-and-swap update keeps multiple worker processes from executing
    the same persisted job.
    """

    while True:
        job_id = session.scalar(
            select(Job.id)
            .where(Job.status == Status.PENDING)
            .order_by(Job.created_at, Job.id)
            .limit(1)
        )
        if not job_id:
            return None
        now = datetime.now(UTC)
        claimed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == Status.PENDING)
            .values(
                status=Status.RUNNING,
                worker_id=worker_id,
                attempts=Job.attempts + 1,
                claimed_at=now,
                heartbeat_at=now,
                error=None,
            )
        )
        session.commit()
        if claimed.rowcount == 1:
            return session.get(Job, job_id)
        session.expire_all()


def request_cancellation(session: Session, job: Job) -> str:
    now = datetime.now(UTC)
    if job.status == Status.PENDING:
        job.status = Status.CANCELLED
        job.completed_at = now
    elif job.status == Status.RUNNING:
        job.status = Status.CANCEL_REQUESTED
    return job.status


def recover_stale_jobs(session: Session, stale_seconds: float) -> int:
    """Requeue jobs whose worker lease expired and reset partial target state."""

    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    jobs = session.scalars(
        select(Job).where(
            Job.status.in_([Status.RUNNING, Status.CANCEL_REQUESTED]),
            Job.heartbeat_at < cutoff,
        )
    ).all()
    for job in jobs:
        if job.status == Status.CANCEL_REQUESTED:
            _cancel_target(session, job)
            job.status = Status.CANCELLED
            job.completed_at = datetime.now(UTC)
            continue
        _reset_target(session, job)
        job.status = Status.PENDING
        job.worker_id = None
        job.claimed_at = None
        job.heartbeat_at = None
        job.error = "worker lease expired; job safely requeued"
    session.commit()
    return len(jobs)


def reconcile_orphaned_tasks(session: Session) -> int:
    """Create queue records for active tasks made before the jobs table existed."""

    known_targets = set(session.scalars(select(Job.target_id)).all())
    reconciled = 0
    ingestions = session.scalars(
        select(Ingestion).where(
            Ingestion.status.in_(
                [Status.PENDING, Status.RUNNING, Status.CANCEL_REQUESTED]
            )
        )
    ).all()
    for ingestion in ingestions:
        if ingestion.id in known_targets:
            continue
        job = enqueue_job(session, "ingestion", ingestion.id)
        if ingestion.status == Status.CANCEL_REQUESTED:
            _cancel_target(session, job)
            job.status = Status.CANCELLED
            job.completed_at = datetime.now(UTC)
        else:
            _reset_target(session, job)
        reconciled += 1

    runs = session.scalars(
        select(ResearchRun).where(
            ResearchRun.status.in_(
                [Status.PENDING, Status.RUNNING, Status.CANCEL_REQUESTED]
            )
        )
    ).all()
    for run in runs:
        if run.id in known_targets:
            continue
        job = enqueue_job(
            session,
            "research",
            run.id,
            {"candidate_k": 20, "evidence_k": 6, "top_k": 6},
        )
        if run.status == Status.CANCEL_REQUESTED:
            _cancel_target(session, job)
            job.status = Status.CANCELLED
            job.completed_at = datetime.now(UTC)
        else:
            _reset_target(session, job)
        reconciled += 1
    session.commit()
    return reconciled


def _reset_target(session: Session, job: Job) -> None:
    if job.kind == "ingestion":
        ingestion = session.get(Ingestion, job.target_id)
        if not ingestion:
            return
        ingestion.status = Status.PENDING
        ingestion.error = None
        ingestion.started_at = None
        ingestion.completed_at = None
        ingestion.files_seen = 0
        ingestion.chunks_indexed = 0
        source = session.get(Source, ingestion.source_id)
        if source and source.status != Status.DELETED:
            source.status = Status.PENDING
            source.error = None
    elif job.kind == "research":
        run = session.get(ResearchRun, job.target_id)
        if not run:
            return
        session.execute(delete(CitationRecord).where(CitationRecord.run_id == run.id))
        session.execute(delete(EvidenceRecord).where(EvidenceRecord.run_id == run.id))
        session.execute(delete(ToolCall).where(ToolCall.run_id == run.id))
        session.execute(delete(TraceEvent).where(TraceEvent.run_id == run.id))
        run.status = Status.PENDING
        run.answer = None
        run.plan_json = "{}"
        run.metrics_json = "{}"
        run.error = None
        run.completed_at = None


def _cancel_target(session: Session, job: Job) -> None:
    now = datetime.now(UTC)
    if job.kind == "ingestion":
        ingestion = session.get(Ingestion, job.target_id)
        if ingestion:
            ingestion.status = Status.CANCELLED
            ingestion.error = None
            ingestion.completed_at = now
            source = session.get(Source, ingestion.source_id)
            if source and source.status != Status.DELETED:
                source.status = Status.CANCELLED
                source.error = None
    elif job.kind == "research":
        run = session.get(ResearchRun, job.target_id)
        if run:
            run.status = Status.CANCELLED
            run.error = None
            run.completed_at = now
