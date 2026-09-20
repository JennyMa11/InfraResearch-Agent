#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from infraresearch.models import Base, Chunk, Ingestion, Job, Source, Status
from infraresearch.task_queue import enqueue_job
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]


def wait_for_status(
    database_url: str,
    job_id: str,
    statuses: set[str],
    *,
    timeout: float,
) -> Job:
    engine = create_engine(database_url, connect_args={"timeout": 0.05})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with Session(engine) as session:
                job = session.get(Job, job_id)
                if job and job.status in statuses:
                    session.expunge(job)
                    return job
        except OperationalError:
            pass
        time.sleep(0.01)
    raise TimeoutError(f"job {job_id} did not reach {sorted(statuses)}")


def start_worker(environment: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["uv", "run", "--project", "backend", "python", "-m", "infraresearch.worker"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kill a worker and verify lease recovery")
    parser.add_argument("--lines", type=int, default=300_000)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="infraresearch-fault-") as directory:
        root = Path(directory)
        database_url = f"sqlite:///{root / 'fault.db'}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        fixture = root / "large.md"
        fixture.write_text("# Worker recovery\n" + "lease heartbeat recovery evidence\n" * args.lines)
        with Session(engine) as session:
            source = Source(kind="file", name=fixture.name, uri=str(fixture))
            session.add(source)
            session.flush()
            ingestion = Ingestion(source_id=source.id)
            session.add(ingestion)
            session.flush()
            job = enqueue_job(session, "ingestion", ingestion.id)
            session.commit()
            job_id = job.id
            source_id = source.id

        environment = os.environ.copy()
        environment.update(
            {
                "INFRARESEARCH_DATA_DIR": str(root / "data"),
                "INFRARESEARCH_DATABASE_URL": database_url,
                "INFRARESEARCH_VECTOR_BACKEND": "sqlite",
                "INFRARESEARCH_EMBEDDING_BACKEND": "hash",
                "INFRARESEARCH_WORKER_HEARTBEAT_SECONDS": "0.05",
                "INFRARESEARCH_WORKER_STALE_SECONDS": "0.2",
                "INFRARESEARCH_WORKER_FAULT_PAUSE_AFTER_CLAIM_SECONDS": "2",
            }
        )
        first = start_worker(environment)
        wait_for_status(database_url, job_id, {Status.RUNNING}, timeout=args.timeout)
        os.killpg(first.pid, signal.SIGKILL)
        first.wait(timeout=5)
        time.sleep(0.3)

        replacement_environment = environment.copy()
        replacement_environment[
            "INFRARESEARCH_WORKER_FAULT_PAUSE_AFTER_CLAIM_SECONDS"
        ] = "0"
        replacement = start_worker(replacement_environment)
        try:
            completed = wait_for_status(
                database_url,
                job_id,
                {Status.COMPLETED, Status.FAILED},
                timeout=args.timeout,
            )
        finally:
            os.killpg(replacement.pid, signal.SIGTERM)
            replacement.wait(timeout=5)

        with Session(engine) as session:
            chunks = int(
                session.scalar(
                    select(func.count(Chunk.id)).where(Chunk.source_id == source_id)
                )
                or 0
            )
            ingestion = session.scalar(select(Ingestion).where(Ingestion.source_id == source_id))
            report = {
                "first_worker_exit": first.returncode,
                "job_status": completed.status,
                "job_attempts": completed.attempts,
                "job_error": completed.error,
                "ingestion_status": ingestion.status if ingestion else "missing",
                "ingestion_error": ingestion.error if ingestion else "missing",
                "chunks": chunks,
                "lease_recovered": completed.status == Status.COMPLETED
                and completed.attempts == 2
                and chunks > 0,
            }
        print(json.dumps(report, indent=2))
        if not report["lease_recovered"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
