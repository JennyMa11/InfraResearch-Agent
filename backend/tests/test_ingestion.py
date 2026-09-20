import json
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from infraresearch.config import Settings
from infraresearch.ingestion import IngestionService, parse_github_url, source_to_metadata
from infraresearch.models import Chunk, Ingestion, Source, Status
from infraresearch.retrieval import VectorIndex
from infraresearch.web import FetchedPage


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


def test_unchanged_file_reindex_keeps_chunk_identity(session, tmp_path) -> None:
    path = tmp_path / "guide.md"
    path.write_text("# Cache\n\nPrefix cache reuses blocks.")
    source = Source(kind="file", name=path.name, uri=str(path))
    session.add(source)
    session.flush()
    first = Ingestion(source_id=source.id)
    session.add(first)
    session.commit()
    settings = Settings(data_dir=tmp_path / "data", vector_backend="sqlite")
    service = IngestionService(settings, VectorIndex(settings))
    service.ingest_file(session, source.id, first.id, path)
    original_id = session.scalar(select(Chunk.id).where(Chunk.source_id == source.id))

    second = Ingestion(source_id=source.id)
    session.add(second)
    session.commit()
    service.ingest_file(session, source.id, second.id, path)

    assert session.scalar(select(Chunk.id).where(Chunk.source_id == source.id)) == original_id
    assert json.loads(source.metadata_json)["incremental_status"] == "unchanged"


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


def test_github_incremental_sync_reindexes_only_changed_files(
    session, tmp_path, monkeypatch
) -> None:
    settings = Settings(data_dir=tmp_path / "data", vector_backend="sqlite")
    source = Source(
        kind="github",
        name="owner/repo",
        uri="https://github.com/owner/repo",
        revision="oldsha",
        status=Status.COMPLETED,
        metadata_json=json.dumps(source_to_metadata(settings, "none")),
    )
    session.add(source)
    session.flush()
    existing = {}
    for name in ("unchanged.py", "changed.py", "deleted.py"):
        content = f"def {name.removesuffix('.py')}():\n    return 'old'"
        chunk = Chunk(
            source_id=source.id,
            content=content,
            locator=f"owner/repo@oldsha/{name}#L1-L2",
            path=name,
            metadata_json=json.dumps({"category": "code"}),
            content_hash=__import__("hashlib").sha256(content.encode()).hexdigest(),
        )
        session.add(chunk)
        existing[name] = chunk
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.commit()
    repo_dir = settings.repos_path / source.id
    (repo_dir / ".git").mkdir(parents=True)
    (repo_dir / "unchanged.py").write_text("def unchanged():\n    return 'old'")
    (repo_dir / "changed.py").write_text("def changed():\n    return 'new'")

    def fake_run(command, **kwargs):
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, "newsha\n", "")
        if "diff" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "M\tchanged.py\nD\tdeleted.py\n",
                "",
            )
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

    chunks = {
        chunk.path: chunk
        for chunk in session.scalars(select(Chunk).where(Chunk.source_id == source.id))
    }
    assert chunks["unchanged.py"].id == existing["unchanged.py"].id
    assert chunks["changed.py"].id != existing["changed.py"].id
    assert "deleted.py" not in chunks
    assert chunks["changed.py"].locator.startswith("owner/repo@newsha/")
    metadata = json.loads(source.metadata_json)
    assert metadata["incremental_status"] == "updated"
    assert metadata["changed_files"] == 1
    assert metadata["deleted_files"] == 1


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

    original_id = chunk.id
    count = service._ingest_issues(session, "owner", "repo", "abc123")
    session.commit()
    repeated = session.scalar(select(Chunk).where(Chunk.source_id == issue_source.id))
    assert count == 1
    assert repeated.id == original_id
    assert json.loads(issue_source.metadata_json)["issues_changed"] == 0


def test_url_ingestion_creates_citable_web_chunks(session, tmp_path, monkeypatch) -> None:
    source = Source(
        kind="web",
        name="example.com",
        uri="https://example.com/guide",
    )
    session.add(source)
    session.flush()
    ingestion = Ingestion(source_id=source.id)
    session.add(ingestion)
    session.commit()
    page = FetchedPage(
        url="https://example.com/guide",
        title="Cache Guide",
        text="# Cache\n\nPrefix caching reuses KV blocks.",
        content_type="text/html",
        etag='"v1"',
        last_modified="Sat, 20 Sep 2026 00:00:00 GMT",
    )
    monkeypatch.setattr("infraresearch.ingestion.fetch_web_page", lambda *a, **k: page)
    settings = Settings(data_dir=tmp_path / "data", vector_backend="sqlite")
    service = IngestionService(settings, VectorIndex(settings))

    service.ingest_url(session, source.id, ingestion.id)

    chunk = session.scalar(select(Chunk).where(Chunk.source_id == source.id))
    assert ingestion.status == Status.COMPLETED
    assert source.name == "Cache Guide"
    assert chunk.locator.startswith("https://example.com/guide#L")
    assert json.loads(chunk.metadata_json)["category"] == "web"
    assert json.loads(source.metadata_json)["etag"] == '"v1"'
