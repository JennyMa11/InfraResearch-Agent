import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from infraresearch.config import Settings
from infraresearch.database import get_db
from infraresearch.main import app
from infraresearch.models import Base
from infraresearch.schemas import ResearchRequest
from infraresearch.worker import Worker


def test_research_request_rejects_candidate_limit_below_evidence_limit() -> None:
    with pytest.raises(ValidationError, match="candidate_k"):
        ResearchRequest(
            question="Explain prefix caching",
            candidate_k=3,
            evidence_k=6,
        )


@pytest.mark.asyncio
async def test_health_and_validation_expose_clear_status(monkeypatch, tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    monkeypatch.setattr("infraresearch.api.get_settings", lambda: settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["vector_backend"] == "sqlite_lexical"
        assert response.json()["embedding_backend"] == "none"
        assert response.json()["reranker_backend"] == "identity"
        assert response.json()["candidate_k"] == 20
        assert response.json()["evidence_k"] == 6
        assert response.json()["version"] == "0.3.1"
        invalid = await client.post(
            "/api/v1/sources/files",
            files={"file": ("weight.safetensors", b"binary", "application/octet-stream")},
        )
        assert invalid.status_code == 415
        assert "unsupported file type" in invalid.json()["detail"]
        invalid_repository = await client.post(
            "/api/v1/sources/github",
            json={
                "url": "https://gitlab.com/example/repository",
                "include_issues": False,
            },
        )
        assert invalid_repository.status_code == 422
        assert "only public" in invalid_repository.json()["detail"]
        private_page = await client.post(
            "/api/v1/sources/url",
            json={"url": "http://127.0.0.1/admin"},
        )
        assert private_page.status_code == 422
        assert "private or local" in private_page.json()["detail"]


@pytest.mark.asyncio
async def test_file_to_agentic_report_and_sse(monkeypatch, tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'integration.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    async def override_db():
        with factory() as session:
            yield session

    settings = Settings(
        data_dir=tmp_path / "data",
        vector_backend="sqlite",
        llm_base_url="http://127.0.0.1:1/v1",
        llm_timeout_seconds=0.01,
    )
    monkeypatch.setattr("infraresearch.api.SessionLocal", factory)
    monkeypatch.setattr("infraresearch.api.get_settings", lambda: settings)
    monkeypatch.setattr("infraresearch.worker.SessionLocal", factory)
    worker = Worker(settings, worker_id="test-worker")
    app.dependency_overrides[get_db] = override_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            upload = await client.post(
                "/api/v1/sources/files",
                files={
                    "file": (
                        "cache.md",
                        b"# Prefix cache\n\nPrefix caching reuses KV blocks.",
                        "text/markdown",
                    )
                },
            )
            assert upload.status_code == 202
            ingestion_id = upload.json()["id"]
            assert worker.run_once()
            ingestion = (await client.get(f"/api/v1/ingestions/{ingestion_id}")).json()
            assert ingestion["status"] == "completed"

            reindex = await client.post(f"/api/v1/sources/{upload.json()['source_id']}/reindex")
            assert reindex.status_code == 202
            assert worker.run_once()
            reindexed = (
                await client.get(f"/api/v1/ingestions/{reindex.json()['id']}")
            ).json()
            assert reindexed["status"] == "completed"
            assert reindexed["chunks_indexed"] == ingestion["chunks_indexed"]

            duplicate = await client.post(
                "/api/v1/sources/files",
                files={
                    "file": (
                        "cache-copy.md",
                        b"# Prefix cache\n\nPrefix caching reuses KV blocks.",
                        "text/markdown",
                    )
                },
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["status"] == "completed"
            assert duplicate.json()["chunks_indexed"] == 0
            duplicate_source_id = duplicate.json()["source_id"]
            duplicate_source = next(
                item
                for item in (await client.get("/api/v1/sources", params={"page_size": 100})).json()[
                    "items"
                ]
                if item["id"] == duplicate_source_id
            )
            assert duplicate_source["metadata"]["duplicate_of"] == upload.json()["source_id"]
            duplicate_deleted = await client.delete(
                f"/api/v1/sources/{duplicate_source_id}"
            )
            assert duplicate_deleted.status_code == 204
            assert worker.run_once()  # duplicate source cleanup

            created = await client.post(
                "/api/v1/research",
                json={"question": "How does prefix caching reuse KV blocks?", "mode": "agentic"},
            )
            assert created.status_code == 202
            run_id = created.json()["id"]
            assert worker.run_once()
            result = (await client.get(f"/api/v1/research/{run_id}")).json()
            assert result["status"] == "completed"
            assert "[S1]" in result["answer"]
            assert result["evidence"][0]["locator"].startswith("cache.md#L")
            assert [event["sequence"] for event in result["events"]] == list(
                range(1, len(result["events"]) + 1)
            )

            stream = await client.get(f"/api/v1/research/{run_id}/events")
            assert stream.status_code == 200
            assert "event: run_started" in stream.text
            assert "event: run_completed" in stream.text

            history = await client.get("/api/v1/research")
            assert history.status_code == 200
            assert history.json()["items"][0]["id"] == run_id
            assert history.json()["items"][0]["provider"] == "extractive"

            retried = await client.post(f"/api/v1/research/{run_id}/retry")
            assert retried.status_code == 202
            retry_id = retried.json()["id"]
            assert worker.run_once()
            retry_result = (await client.get(f"/api/v1/research/{retry_id}")).json()
            assert retry_result["status"] == "completed"

            deleted = await client.delete(f"/api/v1/sources/{upload.json()['source_id']}")
            assert deleted.status_code == 204
            assert (await client.get("/api/v1/sources")).json()["items"] == []
            preserved = (await client.get(f"/api/v1/research/{run_id}")).json()
            assert "Prefix caching" in preserved["evidence"][0]["content"]
            assert not (
                settings.data_dir / "uploads" / upload.json()["source_id"]
            ).exists()

            after_delete = await client.post(
                "/api/v1/research",
                json={"question": "How does prefix caching work?", "mode": "naive"},
            )
            deleted_run_id = after_delete.json()["id"]
            assert worker.run_once()  # source vector cleanup
            assert worker.run_once()  # research
            deleted_result = (
                await client.get(f"/api/v1/research/{deleted_run_id}")
            ).json()
            assert deleted_result["status"] == "completed"
            assert deleted_result["evidence"] == []

            pending = await client.post(
                "/api/v1/research",
                json={"question": "Cancel this pending research", "mode": "naive"},
            )
            cancelled = await client.post(
                f"/api/v1/research/{pending.json()['id']}/cancel"
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            assert cancelled.json()["events"][-1]["event_type"] == "run_cancelled"

            filtered = await client.get(
                "/api/v1/research",
                params={"q": "prefix caching reuse", "status": "completed", "page_size": 1},
            )
            assert filtered.status_code == 200
            assert filtered.json()["total"] >= 1
            assert len(filtered.json()["items"]) == 1
    finally:
        app.dependency_overrides.clear()
