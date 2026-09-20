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
        "results": [
            {
                "chunk_id": hit.chunk.id,
                "source_id": hit.chunk.source_id,
                "content": hit.chunk.content,
                "locator": hit.chunk.locator,
                "retrieval_score": hit.retrieval_score,
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
