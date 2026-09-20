from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from infraresearch.config import Settings
from infraresearch.errors import TaskCancelled
from infraresearch.models import Base, Job, ResearchRun, Status
from infraresearch.task_queue import (
    claim_next_job,
    enqueue_job,
    reconcile_orphaned_tasks,
    recover_stale_jobs,
)
from infraresearch.worker import JobControl, Worker


def make_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_persisted_job_is_claimed_once_across_workers(tmp_path) -> None:
    factory = make_factory(tmp_path)
    with factory() as session:
        run = ResearchRun(question="persistent queue", mode="naive")
        session.add(run)
        session.flush()
        enqueue_job(session, "research", run.id, {"top_k": 4})
        session.commit()
        run_id = run.id

    with factory() as first:
        claimed = claim_next_job(first, "worker-a")
        assert claimed is not None
        assert claimed.target_id == run_id
        assert claimed.attempts == 1
    with factory() as second:
        assert claim_next_job(second, "worker-b") is None
        jobs = second.scalars(select(Job)).all()
        assert len(jobs) == 1


def test_stale_worker_lease_requeues_job_for_safe_retry(tmp_path) -> None:
    factory = make_factory(tmp_path)
    with factory() as session:
        run = ResearchRun(
            question="recover after forced restart",
            mode="agentic",
            status=Status.RUNNING,
            answer="partial",
        )
        session.add(run)
        session.flush()
        job = enqueue_job(session, "research", run.id)
        job.status = Status.RUNNING
        job.worker_id = "dead-worker"
        job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
        session.commit()
        run_id = run.id
        job_id = job.id

    with factory() as session:
        assert recover_stale_jobs(session, stale_seconds=30) == 1
        recovered = session.get(Job, job_id)
        recovered_run = session.get(ResearchRun, run_id)
        assert recovered.status == Status.PENDING
        assert recovered.worker_id is None
        assert recovered_run.status == Status.PENDING
        assert recovered_run.answer is None


def test_legacy_active_task_is_adopted_by_persistent_queue(tmp_path) -> None:
    factory = make_factory(tmp_path)
    with factory() as session:
        run = ResearchRun(
            question="legacy in-process task",
            mode="agentic",
            status=Status.RUNNING,
            answer="unsafe partial answer",
        )
        session.add(run)
        session.commit()
        run_id = run.id

    with factory() as session:
        assert reconcile_orphaned_tasks(session) == 1
        adopted = session.scalar(select(Job).where(Job.target_id == run_id))
        run = session.get(ResearchRun, run_id)
        assert adopted.status == Status.PENDING
        assert run.status == Status.PENDING
        assert run.answer is None


def test_running_job_observes_cooperative_cancellation(monkeypatch, tmp_path) -> None:
    factory = make_factory(tmp_path)
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        vector_backend="sqlite",
        worker_heartbeat_seconds=60,
    )
    monkeypatch.setattr("infraresearch.worker.SessionLocal", factory)
    with factory() as session:
        run = ResearchRun(question="cancel running job", mode="naive")
        session.add(run)
        session.flush()
        job = enqueue_job(session, "research", run.id)
        session.commit()
        job_id = job.id
    with factory() as session:
        assert claim_next_job(session, "worker-a") is not None

    with JobControl(job_id, settings) as control:
        with factory() as session:
            job = session.get(Job, job_id)
            job.status = Status.CANCEL_REQUESTED
            session.commit()
        with pytest.raises(TaskCancelled):
            control.checkpoint()


def test_checkpoint_retries_after_transient_sqlite_lock(monkeypatch, tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")

    class LockedSession:
        def __enter__(self):
            raise OperationalError("SELECT status", {}, Exception("database is locked"))

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("infraresearch.worker.SessionLocal", LockedSession)

    JobControl("job-test", settings).checkpoint()


def test_worker_completes_persisted_job_idempotently(monkeypatch, tmp_path) -> None:
    factory = make_factory(tmp_path)
    settings = Settings(
        data_dir=tmp_path / "data",
        vector_backend="sqlite",
        llm_base_url="http://127.0.0.1:1/v1",
        llm_timeout_seconds=0.01,
    )
    monkeypatch.setattr("infraresearch.worker.SessionLocal", factory)
    with factory() as session:
        run = ResearchRun(question="hello persistent worker", mode="naive")
        session.add(run)
        session.flush()
        enqueue_job(session, "research", run.id)
        session.commit()
        run_id = run.id

    worker = Worker(settings, worker_id="test-worker")
    assert worker.run_once()
    assert not worker.run_once()
    with factory() as session:
        run = session.get(ResearchRun, run_id)
        job = session.scalar(select(Job).where(Job.target_id == run_id))
        assert run.status == Status.COMPLETED
        assert job.status == Status.COMPLETED
        assert job.attempts == 1
