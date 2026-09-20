from __future__ import annotations

import hashlib
import json

from mcp import Client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from infraresearch.config import Settings
from infraresearch.mcp_server import create_mcp_server
from infraresearch.models import Base, Chunk, Source, Status
from infraresearch.retrieval import VectorIndex
from infraresearch.tooling import ToolResult


async def test_mcp_client_discovers_and_calls_search_tools(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        source = Source(
            kind="file",
            name="cache.md",
            uri="cache.md",
            status=Status.COMPLETED,
        )
        session.add(source)
        session.flush()
        chunk = Chunk(
            source_id=source.id,
            content="Prefix caching reuses KV cache blocks.",
            locator="cache.md#L1-L2",
            metadata_json=json.dumps({"category": "docs"}),
            content_hash=hashlib.sha256(b"prefix-cache").hexdigest(),
        )
        session.add(chunk)
        session.commit()
        chunk_id = chunk.id

    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    server = create_mcp_server(
        settings,
        session_factory=factory,
        index=VectorIndex(settings),
    )
    async with Client(server) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "semantic_document_search",
            "code_keyword_search",
            "github_issue_search",
        }
        semantic = next(
            tool for tool in listed.tools if tool.name == "semantic_document_search"
        )
        assert semantic.input_schema["properties"]["top_k"]["maximum"] == 100

        result = await client.call_tool(
            "semantic_document_search",
            {"query": "prefix KV cache", "top_k": 3},
        )
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["results"][0]["chunk_id"] == chunk_id

        invalid = await client.call_tool(
            "semantic_document_search",
            {"query": "prefix", "top_k": 101},
        )
        assert invalid.is_error is True


async def test_mcp_client_reads_chunk_resource(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        source = Source(
            kind="file",
            name="guide.md",
            uri="guide.md",
            status=Status.COMPLETED,
        )
        session.add(source)
        session.flush()
        chunk = Chunk(
            source_id=source.id,
            content="Evidence snapshot content.",
            locator="guide.md#L4",
            metadata_json="{}",
            content_hash=hashlib.sha256(b"resource").hexdigest(),
        )
        session.add(chunk)
        session.commit()
        chunk_id = chunk.id

    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    server = create_mcp_server(
        settings,
        session_factory=factory,
        index=VectorIndex(settings),
    )
    async with Client(server) as client:
        templates = await client.list_resource_templates()
        assert any(
            template.uri_template == "infraresearch://chunk/{chunk_id}"
            for template in templates.resource_templates
        )
        resource = await client.read_resource(f"infraresearch://chunk/{chunk_id}")
        assert "Evidence snapshot content." in resource.contents[0].text


async def test_mcp_client_handles_empty_results_and_internal_errors(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    server = create_mcp_server(
        settings,
        session_factory=factory,
        index=VectorIndex(settings),
    )
    async with Client(server) as client:
        empty = await client.call_tool(
            "semantic_document_search",
            {"query": "nothing indexed", "top_k": 3},
        )
        assert empty.is_error is False
        assert empty.structured_content is not None
        assert empty.structured_content["results"] == []

    class BrokenRegistry:
        def execute(self, session, name, arguments):
            return ToolResult(
                name=name,
                status="failed",
                attempts=1,
                error="index unavailable",
            )

    broken_server = create_mcp_server(
        settings,
        session_factory=factory,
        registry=BrokenRegistry(),
    )
    async with Client(broken_server) as client:
        failed = await client.call_tool(
            "semantic_document_search",
            {"query": "cache", "top_k": 3},
        )
        assert failed.is_error is True
        assert "Error executing tool" in failed.content[0].text


async def test_mcp_client_reads_complete_issue_resource(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        source = Source(
            kind="issue",
            name="owner/repo issues",
            uri="https://github.com/owner/repo/issues",
            status=Status.COMPLETED,
            metadata_json=json.dumps({"parent_repository": "owner/repo"}),
        )
        session.add(source)
        session.flush()
        session.add(
            Chunk(
                source_id=source.id,
                content="# Cache leak\n\nKV blocks remain allocated.",
                locator="owner/repo#issue-42 https://github.com/owner/repo/issues/42",
                metadata_json=json.dumps(
                    {
                        "category": "issue",
                        "number": 42,
                        "url": "https://github.com/owner/repo/issues/42",
                        "updated_at": "2026-09-20T00:00:00Z",
                    }
                ),
                content_hash=hashlib.sha256(b"issue-42").hexdigest(),
            )
        )
        session.commit()
        source_id = source.id

    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    server = create_mcp_server(settings, session_factory=factory, index=VectorIndex(settings))
    async with Client(server) as client:
        resource = await client.read_resource(f"infraresearch://issue/{source_id}/42")
        body = json.loads(resource.contents[0].text)
        assert body["number"] == 42
        assert body["repository"] == "owner/repo"
        assert "KV blocks" in body["content"]
