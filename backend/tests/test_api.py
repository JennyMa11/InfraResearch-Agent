import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from infraresearch.agent import ResearchAgent
from infraresearch.config import Settings
from infraresearch.database import get_db
from infraresearch.ingestion import IngestionService
from infraresearch.main import app
from infraresearch.models import Base
from infraresearch.provider import LLMProvider
from infraresearch.retrieval import VectorIndex


@pytest.mark.asyncio
async def test_health_and_validation_expose_clear_status(monkeypatch, tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    index = VectorIndex(settings)
    monkeypatch.setattr(
        "infraresearch.api._services",
        lambda: (
            settings,
            index,
            IngestionService(settings, index),
            ResearchAgent(settings, index, LLMProvider(settings)),
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["vector_backend"] == "sqlite_lexical"
        invalid = await client.post(
            "/api/v1/sources/files",
            files={"file": ("weight.safetensors", b"binary", "application/octet-stream")},
        )
        assert invalid.status_code == 415
        assert "unsupported file type" in invalid.json()["detail"]


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
    index = VectorIndex(settings)
    services = (
        settings,
        index,
        IngestionService(settings, index),
        ResearchAgent(settings, index, LLMProvider(settings)),
    )
    monkeypatch.setattr("infraresearch.api.SessionLocal", factory)
    monkeypatch.setattr("infraresearch.api._services", lambda: services)
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
            for _ in range(50):
                ingestion = (await client.get(f"/api/v1/ingestions/{ingestion_id}")).json()
                if ingestion["status"] != "pending" and ingestion["status"] != "running":
                    break
                await asyncio.sleep(0.02)
            assert ingestion["status"] == "completed"

            created = await client.post(
                "/api/v1/research",
                json={"question": "How does prefix caching reuse KV blocks?", "mode": "agentic"},
            )
            assert created.status_code == 202
            run_id = created.json()["id"]
            for _ in range(100):
                result = (await client.get(f"/api/v1/research/{run_id}")).json()
                if result["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.02)
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
    finally:
        app.dependency_overrides.clear()
