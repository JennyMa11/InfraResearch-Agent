from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field
from sqlalchemy.orm import Session

from . import __version__
from .config import Settings, get_settings
from .database import SessionLocal, init_db
from .models import Chunk, EvidenceRecord, Source
from .retrieval import VectorIndex
from .tooling import ToolRegistry, create_tool_registry

SessionFactory = Callable[[], Session]


def _serialize_hits(result) -> dict[str, Any]:
    if result.status != "completed":
        raise RuntimeError(result.error or f"{result.name} failed")
    return {
        "tool": result.name,
        "status": result.status,
        "attempts": result.attempts,
        "duration_ms": round(result.duration_ms, 2),
        "error_type": result.error_type,
        "retryable": result.retryable,
        "results": [
            {
                "chunk_id": hit.chunk.id,
                "source_id": hit.chunk.source_id,
                "content": hit.chunk.content,
                "locator": hit.chunk.locator,
                "retrieval_score": hit.retrieval_score,
                "dense_score": hit.dense_score,
                "lexical_score": hit.lexical_score,
                "fusion_method": hit.fusion_method,
            }
            for hit in result.hits
        ],
    }


def create_mcp_server(
    settings: Settings | None = None,
    *,
    session_factory: SessionFactory | None = None,
    index: VectorIndex | None = None,
    registry: ToolRegistry | None = None,
) -> MCPServer:
    settings = settings or get_settings()
    session_factory = session_factory or SessionLocal
    index = index or VectorIndex(settings)
    registry = registry or create_tool_registry(index)
    server = MCPServer(
        "InfraResearch",
        description=(
            "Read-only search over imported technical documents, source code, "
            "and GitHub Issues."
        ),
        version=__version__,
    )

    def execute(name: str, query: str, top_k: int) -> dict[str, Any]:
        with session_factory() as session:
            result = registry.execute(
                session,
                name,
                {"query": query, "top_k": top_k},
            )
            return _serialize_hits(result)

    @server.tool(structured_output=True)
    def semantic_document_search(
        query: Annotated[str, Field(min_length=1, max_length=4000)],
        top_k: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Search imported documents and code with the configured vector backend."""

        return execute("semantic_document_search", query, top_k)

    @server.tool(structured_output=True)
    def code_keyword_search(
        query: Annotated[str, Field(min_length=1, max_length=4000)],
        top_k: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Search imported source-code chunks using token-aware keyword matching."""

        return execute("code_keyword_search", query, top_k)

    @server.tool(structured_output=True)
    def github_issue_search(
        query: Annotated[str, Field(min_length=1, max_length=4000)],
        top_k: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Search GitHub Issues that were explicitly imported into InfraResearch."""

        return execute("github_issue_search", query, top_k)

    if settings.web_search_enabled:

        @server.tool(structured_output=True)
        def web_search(
            query: Annotated[str, Field(min_length=1, max_length=4000)],
            top_k: Annotated[int, Field(ge=1, le=10)] = 5,
        ) -> dict[str, Any]:
            """Search the public web only when local indexed evidence is insufficient."""

            return execute("web_search", query, top_k)

    @server.resource(
        "infraresearch://source/{source_id}",
        name="source",
        description="Read source metadata by stable InfraResearch source ID.",
        mime_type="application/json",
    )
    def source_resource(source_id: str) -> str:
        with session_factory() as session:
            source = session.get(Source, source_id)
            if not source:
                raise ValueError("source not found")
            return json.dumps(
                {
                    "id": source.id,
                    "kind": source.kind,
                    "name": source.name,
                    "uri": source.uri,
                    "status": source.status,
                    "revision": source.revision,
                    "metadata": json.loads(source.metadata_json),
                },
                ensure_ascii=False,
            )

    @server.resource(
        "infraresearch://chunk/{chunk_id}",
        name="chunk",
        description="Read an indexed chunk and its stable locator.",
        mime_type="application/json",
    )
    def chunk_resource(chunk_id: str) -> str:
        with session_factory() as session:
            chunk = session.get(Chunk, chunk_id)
            if not chunk:
                raise ValueError("chunk not found")
            return json.dumps(
                {
                    "id": chunk.id,
                    "source_id": chunk.source_id,
                    "content": chunk.content,
                    "locator": chunk.locator,
                    "metadata": json.loads(chunk.metadata_json),
                },
                ensure_ascii=False,
            )

    @server.resource(
        "infraresearch://repo/{source_id}/file/{chunk_id}",
        name="repo_file",
        description="Read a repository file chunk with commit-stable path and line locator.",
        mime_type="application/json",
    )
    def repo_file_resource(source_id: str, chunk_id: str) -> str:
        with session_factory() as session:
            source = session.get(Source, source_id)
            chunk = session.get(Chunk, chunk_id)
            if (
                not source
                or source.kind != "github"
                or not chunk
                or chunk.source_id != source.id
            ):
                raise ValueError("repository file chunk not found")
            return json.dumps(
                {
                    "source_id": source.id,
                    "repository": source.name,
                    "revision": source.revision,
                    "chunk_id": chunk.id,
                    "path": chunk.path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "locator": chunk.locator,
                    "content": chunk.content,
                    "metadata": json.loads(chunk.metadata_json),
                },
                ensure_ascii=False,
            )

    @server.resource(
        "infraresearch://issue/{source_id}/{number}",
        name="issue",
        description="Read a complete imported GitHub Issue by source ID and issue number.",
        mime_type="application/json",
    )
    def issue_resource(source_id: str, number: str) -> str:
        with session_factory() as session:
            source = session.get(Source, source_id)
            if not source or source.kind != "issue":
                raise ValueError("issue source not found")
            chunks = session.query(Chunk).filter(Chunk.source_id == source.id).all()
            chunk = next(
                (
                    item
                    for item in chunks
                    if str(json.loads(item.metadata_json).get("number")) == number
                ),
                None,
            )
            if not chunk:
                raise ValueError("issue not found")
            return json.dumps(
                {
                    "source_id": source.id,
                    "repository": json.loads(source.metadata_json).get(
                        "parent_repository"
                    ),
                    "number": int(number),
                    "url": json.loads(chunk.metadata_json).get("url"),
                    "updated_at": json.loads(chunk.metadata_json).get("updated_at"),
                    "locator": chunk.locator,
                    "content": chunk.content,
                },
                ensure_ascii=False,
            )

    @server.resource(
        "infraresearch://evidence/{run_id}/{evidence_id}",
        name="evidence",
        description="Read an immutable evidence snapshot from a research run.",
        mime_type="application/json",
    )
    def evidence_resource(run_id: str, evidence_id: str) -> str:
        with session_factory() as session:
            evidence = session.get(EvidenceRecord, f"{run_id}:{evidence_id}")
            if not evidence:
                raise ValueError("evidence not found")
            return json.dumps(
                {
                    "id": evidence_id,
                    "run_id": evidence.run_id,
                    "chunk_id": evidence.chunk_id,
                    "source_id": evidence.source_id,
                    "content": evidence.content,
                    "locator": evidence.locator,
                    "retrieval_score": evidence.retrieval_score,
                    "rerank_score": evidence.rerank_score,
                    "final_score": evidence.score,
                    "metadata": json.loads(evidence.metadata_json),
                },
                ensure_ascii=False,
            )

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    init_db()
    server = create_mcp_server()
    transport = args.transport
    if transport == "streamable-http":
        server.run(transport=transport, host=args.host, port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
