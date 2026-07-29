from __future__ import annotations

import json
import os
import signal
import socket
import threading
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from uuid import uuid4

from sqlalchemy import select

from .agent import ResearchAgent
from .config import Settings, get_settings
from .database import SessionLocal, init_db
from .errors import TaskCancelled
from .ingestion import IngestionService
from .models import Ingestion, Job, ResearchRun, Source, Status
from .provider import LLMProvider
from .retrieval import VectorIndex
from .task_queue import claim_next_job, reconcile_orphaned_tasks, recover_stale_jobs


class JobControl(AbstractContextManager["JobControl"]):
    def __init__(self, job_id: str, settings: Settings):
        self.job_id = job_id
        self.settings = settings
        self._stop = threading.Event()
        self._cancelled = threading.Event()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"heartbeat-{job_id}",
            daemon=True,
        )

    def __enter__(self) -> JobControl:
        self._thread.start()
        self.checkpoint()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.settings.worker_heartbeat_seconds * 2))

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.settings.worker_heartbeat_seconds):
            try:
                with SessionLocal() as session:
                    job = session.get(Job, self.job_id)
                    if not job or job.status not in {
                        Status.RUNNING,
                        Status.CANCEL_REQUESTED,
                    }:
                        return
                    if job.status == Status.CANCEL_REQUESTED:
                        self._cancelled.set()
                    job.heartbeat_at = datetime.now(UTC)
                    session.commit()
            except Exception:
                # A task checkpoint performs the authoritative status check.
                continue

    def checkpoint(self) -> None:
        if self._cancelled.is_set():
            raise TaskCancelled("task cancellation requested")
        with SessionLocal() as session:
            status = session.scalar(select(Job.status).where(Job.id == self.job_id))
            if status in {Status.CANCEL_REQUESTED, Status.CANCELLED}:
                self._cancelled.set()
                raise TaskCancelled("task cancellation requested")
            if status != Status.RUNNING:
                raise TaskCancelled("task lease is no longer active")


class Worker:
    def __init__(self, settings: Settings | None = None, worker_id: str | None = None):
        self.settings = settings or get_settings()
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        )
        self.index = VectorIndex(self.settings)
        self.ingestion = IngestionService(self.settings, self.index)
        self.agent = ResearchAgent(
            self.settings,
            self.index,
            LLMProvider(self.settings),
        )

    def recover(self) -> int:
        with SessionLocal() as session:
            reconciled = reconcile_orphaned_tasks(session)
            return reconciled + recover_stale_jobs(
                session,
                self.settings.worker_stale_seconds,
            )

    def run_once(self) -> bool:
        with SessionLocal() as session:
            job = claim_next_job(session, self.worker_id)
            if not job:
                return False
            job_id = job.id
        self._execute(job_id)
        return True

    def _execute(self, job_id: str) -> None:
        try:
            with JobControl(job_id, self.settings) as control:
                with SessionLocal() as session:
                    job = session.get(Job, job_id)
                    if not job:
                        return
                    payload = json.loads(job.payload_json)
                    if job.kind == "ingestion":
                        self._execute_ingestion(session, job, payload, control)
                    elif job.kind == "research":
                        self.agent.execute(
                            session,
                            job.target_id,
                            top_k=int(payload.get("top_k", 6)),
                            cancel_check=control.checkpoint,
                        )
                    elif job.kind == "source_cleanup":
                        for source_id in payload.get("source_ids", [job.target_id]):
                            control.checkpoint()
                            self.index.delete_source(str(source_id))
                    else:
                        raise ValueError(f"unsupported job kind: {job.kind}")
            self._finish_from_target(job_id)
        except TaskCancelled:
            self._finish_cancelled(job_id)
        except Exception as exc:
            self._finish_failed(job_id, str(exc))

    def _execute_ingestion(
        self,
        session,
        job: Job,
        payload: dict,
        control: JobControl,
    ) -> None:
        ingestion = session.get(Ingestion, job.target_id)
        if not ingestion:
            raise ValueError("ingestion target no longer exists")
        source = session.get(Source, ingestion.source_id)
        if not source or source.status == Status.DELETED:
            raise TaskCancelled("source no longer exists")
        if source.kind == "file":
            self.ingestion.ingest_file(
                session,
                source.id,
                ingestion.id,
                Path(source.uri),
                cancel_check=control.checkpoint,
            )
        elif source.kind == "github":
            metadata = json.loads(source.metadata_json)
            self.ingestion.ingest_github(
                session,
                source.id,
                ingestion.id,
                revision=metadata.get("requested_revision"),
                include_issues=bool(metadata.get("include_issues", False)),
                cancel_check=control.checkpoint,
            )
        else:
            raise ValueError(f"unsupported source kind: {source.kind}")

    def _finish_from_target(self, job_id: str) -> None:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if not job:
                return
            if job.kind == "ingestion":
                target = session.get(Ingestion, job.target_id)
                target_status = target.status if target else Status.FAILED
                target_error = target.error if target else "ingestion target missing"
            elif job.kind == "research":
                target = session.get(ResearchRun, job.target_id)
                target_status = target.status if target else Status.FAILED
                target_error = target.error if target else "research target missing"
            else:
                target_status = Status.COMPLETED
                target_error = None
            if target_status == Status.CANCELLED:
                job.status = Status.CANCELLED
            elif target_status == Status.COMPLETED:
                job.status = Status.COMPLETED
            else:
                job.status = Status.FAILED
            job.error = target_error
            job.completed_at = datetime.now(UTC)
            session.commit()

    def _finish_cancelled(self, job_id: str) -> None:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if not job:
                return
            now = datetime.now(UTC)
            job.status = Status.CANCELLED
            job.error = None
            job.completed_at = now
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
                if run and run.status != Status.CANCELLED:
                    self.agent.mark_cancelled(session, run)
            session.commit()

    def _finish_failed(self, job_id: str, error: str) -> None:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if not job:
                return
            job.status = Status.FAILED
            job.error = error[:2000]
            job.completed_at = datetime.now(UTC)
            session.commit()


def main() -> None:
    init_db()
    worker = Worker()
    stop = threading.Event()

    def request_stop(_: int, __: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    worker.recover()
    while not stop.is_set():
        if not worker.run_once():
            stop.wait(worker.settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
