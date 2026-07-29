import json
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from infraresearch.config import Settings
from infraresearch.ingestion import IngestionService, parse_github_url
from infraresearch.models import Chunk, Ingestion, Source, Status
from infraresearch.retrieval import VectorIndex


def test_only_public_github_repository_urls_are_accepted() -> None:
    assert parse_github_url("https://github.com/vllm-project/vllm") == (
        "vllm-project",
        "vllm",
    )
    assert parse_github_url("https://github.com/vllm-project/vllm.git") == (
        "vllm-project",
        "vllm",
    )
    with pytest.raises(ValueError):
        parse_github_url("https://gitlab.com/example/repo")
    with pytest.raises(ValueError):
        parse_github_url("file:///tmp/repo")
    with pytest.raises(ValueError):
        parse_github_url("https://github.com/owner/repo/issues/1")


def test_file_ingestion_persists_stable_chunks(session, tmp_path) -> None:
    path = tmp_path / "guide.md"
    path.write_text("# Cache\n\nPrefix cache reuses blocks.")
    source = Source(kind="file", name=path.name, uri=str(path))
    session.add(source)
    session.flush()
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.commit()
    settings = Settings(data_dir=tmp_path / "data", vector_backend="sqlite")
    service = IngestionService(settings, VectorIndex(settings))
    service.ingest_file(session, source.id, ingestion.id, path)
    session.refresh(ingestion)
    chunks = session.scalars(select(Chunk).where(Chunk.source_id == source.id)).all()
    assert ingestion.status == Status.COMPLETED
    assert ingestion.chunks_indexed == 1
    assert chunks[0].locator == "guide.md#L1-L3"
    assert json.loads(source.metadata_json)["embedding_backend"] == "none"


def test_github_ingestion_uses_fixed_sha(session, tmp_path, monkeypatch) -> None:
    source = Source(
        kind="github",
        name="owner/repo",
        uri="https://github.com/owner/repo",
    )
    session.add(source)
    session.flush()
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.commit()
    settings = Settings(data_dir=tmp_path / "data", vector_backend="sqlite")

    def fake_run(command, **kwargs):
        if command[1] == "clone":
            destination = Path(command[-1])
            destination.mkdir(parents=True)
            (destination / "README.md").write_text("# Repository\n\nPinned content.")
            return subprocess.CompletedProcess(command, 0, "", "")
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, "abc123\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    service = IngestionService(settings, VectorIndex(settings))
    monkeypatch.setattr(service, "_run_command", fake_run)
    service.ingest_github(
        session,
        source.id,
        ingestion.id,
        revision=None,
        include_issues=False,
    )
    session.refresh(source)
    session.refresh(ingestion)
    chunk = session.scalar(select(Chunk).where(Chunk.source_id == source.id))
    assert ingestion.status == Status.COMPLETED
    assert source.revision == "abc123"
    assert chunk is not None
    assert chunk.locator.startswith("owner/repo@abc123/")


def test_github_issue_api_creates_url_locator(session, tmp_path, monkeypatch) -> None:
    settings = Settings(data_dir=tmp_path / "data", vector_backend="sqlite")
    service = IngestionService(settings, VectorIndex(settings))
    request = httpx.Request("GET", "https://api.github.com/repos/owner/repo/issues")
    response = httpx.Response(
        200,
        request=request,
        json=[
            {
                "number": 42,
                "title": "Cache blocks not released",
                "body": "KV cache remains allocated.",
                "html_url": "https://github.com/owner/repo/issues/42",
            },
            {
                "number": 43,
                "title": "A pull request",
                "body": "",
                "html_url": "https://github.com/owner/repo/pull/43",
                "pull_request": {},
            },
        ],
    )
    monkeypatch.setattr("infraresearch.ingestion.httpx.get", lambda *args, **kwargs: response)
    count = service._ingest_issues(session, "owner", "repo", "abc123")
    session.commit()
    issue_source = session.scalar(select(Source).where(Source.kind == "issue"))
    chunk = session.scalar(select(Chunk).where(Chunk.source_id == issue_source.id))
    assert count == 1
    assert json.loads(chunk.metadata_json)["number"] == 42
    assert chunk.locator.endswith("https://github.com/owner/repo/issues/42")
