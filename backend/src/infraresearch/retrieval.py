from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Chunk, Source

TOKEN_RE = re.compile(r"[\w.+#/-]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text):
        token = raw.lower()
        if re.search(r"[\u3400-\u9fff]", token):
            cjk = [character for character in token if "\u3400" <= character <= "\u9fff"]
            tokens.extend(cjk)
            tokens.extend("".join(cjk[index : index + 2]) for index in range(len(cjk) - 1))
            latin = re.findall(r"[a-z0-9_.+#/-]{2,}", token)
            tokens.extend(latin)
        elif len(token) > 1:
            tokens.append(token)
    return tokens


def hashed_embedding(text: str, dimensions: int) -> list[float]:
    """Dependency-light deterministic embedding used by the local prototype.

    The configured production embedding model is persisted as collection metadata.
    This projection keeps tests and first-run demos offline; deployments can replace
    this function behind the same VectorIndex interface.
    """

    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1
    return [value / norm for value in vector]


@dataclass(slots=True)
class SearchHit:
    chunk: Chunk
    score: float
    tool: str = "semantic_document_search"


class VectorIndex:
    collection = "infraresearch_chunks_v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend = "sqlite_lexical"
        self._client: Any = None
        if settings.vector_backend != "qdrant":
            return
        try:
            from qdrant_client import QdrantClient, models

            self._models = models
            self._client = QdrantClient(path=str(settings.qdrant_path))
            fingerprint = {
                "embedding_model": settings.embedding_model,
                "embedding_dimensions": settings.embedding_dimensions,
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
            }
            metadata_path = settings.qdrant_path / "index_metadata.json"
            previous = None
            if metadata_path.exists():
                previous = json.loads(metadata_path.read_text())
            if previous != fingerprint and self._client.collection_exists(self.collection):
                self._client.delete_collection(self.collection)
            if not self._client.collection_exists(self.collection):
                self._client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(
                        size=settings.embedding_dimensions,
                        distance=models.Distance.COSINE,
                    ),
                )
            metadata_path.write_text(json.dumps(fingerprint, indent=2))
            self.backend = "qdrant_local"
        except Exception:
            self._client = None

    def upsert(self, chunks: list[Chunk]) -> None:
        if not self._client or not chunks:
            return
        points = [
            self._models.PointStruct(
                id=int(hashlib.sha256(chunk.id.encode()).hexdigest()[:15], 16),
                vector=hashed_embedding(chunk.content, self.settings.embedding_dimensions),
                payload={"chunk_id": chunk.id, "source_id": chunk.source_id},
            )
            for chunk in chunks
        ]
        self._client.upsert(collection_name=self.collection, points=points, wait=True)

    def search(self, session: Session, query: str, top_k: int = 6) -> list[SearchHit]:
        if self._client:
            try:
                result = self._client.query_points(
                    collection_name=self.collection,
                    query=hashed_embedding(query, self.settings.embedding_dimensions),
                    limit=top_k,
                    with_payload=True,
                ).points
                ids = [str(item.payload["chunk_id"]) for item in result]
                chunks = session.scalars(select(Chunk).where(Chunk.id.in_(ids))).all()
                by_id = {chunk.id: chunk for chunk in chunks}
                hits = [
                    SearchHit(by_id[str(item.payload["chunk_id"])], float(item.score))
                    for item in result
                    if str(item.payload["chunk_id"]) in by_id
                ]
                if hits:
                    return hits
            except Exception:
                self.backend = "sqlite_lexical"
        return self._lexical_search(session, query, top_k)

    @staticmethod
    def _lexical_search(session: Session, query: str, top_k: int) -> list[SearchHit]:
        query_tokens = set(tokenize(query))
        chunks = session.scalars(select(Chunk)).all()
        scored: list[SearchHit] = []
        for chunk in chunks:
            tokens = tokenize(chunk.content)
            if not tokens:
                continue
            overlap = sum(1 for token in tokens if token in query_tokens)
            coverage = len(query_tokens & set(tokens)) / max(1, len(query_tokens))
            density = overlap / math.sqrt(len(tokens))
            score = 0.75 * coverage + 0.25 * min(1.0, density)
            if score > 0:
                scored.append(SearchHit(chunk, score))
        return sorted(scored, key=lambda hit: hit.score, reverse=True)[:top_k]

    @staticmethod
    def keyword_search(
        session: Session, query: str, top_k: int = 6, *, code_only: bool = True
    ) -> list[SearchHit]:
        query_tokens = set(tokenize(query))
        candidates = session.scalars(select(Chunk)).all()
        hits: list[SearchHit] = []
        for chunk in candidates:
            metadata = json.loads(chunk.metadata_json)
            if code_only and metadata.get("category") != "code":
                continue
            content_lower = chunk.content.lower()
            matched = sum(content_lower.count(token) for token in query_tokens)
            if matched:
                hits.append(
                    SearchHit(chunk=chunk, score=min(1.0, matched / 5), tool="code_keyword_search")
                )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]

    @staticmethod
    def issue_search(session: Session, query: str, top_k: int = 6) -> list[SearchHit]:
        issue_source_ids = session.scalars(select(Source.id).where(Source.kind == "issue")).all()
        if not issue_source_ids:
            return []
        query_tokens = set(tokenize(query))
        candidates = session.scalars(
            select(Chunk).where(Chunk.source_id.in_(issue_source_ids))
        ).all()
        hits = []
        for chunk in candidates:
            overlap = len(query_tokens & set(tokenize(chunk.content)))
            if overlap:
                hits.append(
                    SearchHit(
                        chunk,
                        min(1.0, overlap / max(1, len(query_tokens))),
                        "github_issue_search",
                    )
                )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]
