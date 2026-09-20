from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

from sqlalchemy import func, select
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
    """Dependency-light deterministic fallback embedding."""

    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1
    return [value / norm for value in vector]


class TextEncoder(Protocol):
    name: str

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    def encode_query(self, text: str) -> list[float]: ...


class HashEncoder:
    name = "hash"

    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [hashed_embedding(text, self.dimensions) for text in texts]

    def encode_query(self, text: str) -> list[float]:
        return hashed_embedding(text, self.dimensions)


class FastEmbedEncoder:
    name = "fastembed"

    def __init__(self, settings: Settings):
        from fastembed import TextEmbedding

        supported = {
            str(item["model"])
            for item in TextEmbedding.list_supported_models()
            if "model" in item
        }
        if settings.embedding_model not in supported:
            from fastembed.common.model_description import ModelSource, PoolingType

            TextEmbedding.add_custom_model(
                model=settings.embedding_model,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=settings.embedding_model),
                dim=settings.embedding_dimensions,
                model_file="onnx/model.onnx",
            )
        self.dimensions = settings.embedding_dimensions
        self.model_name = settings.embedding_model
        self.model = TextEmbedding(
            model_name=settings.embedding_model,
            cache_dir=str(settings.resolved_embedding_cache_dir),
            local_files_only=settings.embedding_local_files_only,
        )

    def _encode(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        prepared = [
            text if text.lstrip().lower().startswith(f"{prefix}:") else f"{prefix}: {text}"
            for text in texts
        ]
        vectors = [vector.tolist() for vector in self.model.embed(prepared)]
        if any(len(vector) != self.dimensions for vector in vectors):
            raise ValueError(
                f"embedding model {self.model_name!r} did not produce "
                f"{self.dimensions}-dimensional vectors"
            )
        return vectors

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts, "passage")

    def encode_query(self, text: str) -> list[float]:
        return self._encode([text], "query")[0]


def create_encoder(settings: Settings) -> TextEncoder:
    if settings.embedding_backend == "hash":
        return HashEncoder(settings.embedding_dimensions)
    if settings.embedding_backend == "fastembed":
        return FastEmbedEncoder(settings)
    raise ValueError(
        "INFRARESEARCH_EMBEDDING_BACKEND must be 'fastembed' or 'hash', "
        f"got {settings.embedding_backend!r}"
    )


@dataclass(slots=True)
class SearchHit:
    chunk: Chunk
    retrieval_score: float
    tool: str = "semantic_document_search"
    query: str = ""
    rerank_score: float | None = None
    dense_score: float | None = None
    lexical_score: float | None = None
    fusion_method: str | None = None

    @property
    def score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.retrieval_score


class VectorIndex:
    collection = "infraresearch_chunks_v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend = "sqlite_lexical"
        self.embedding_backend = "none"
        self._client: Any = None
        self._synchronized = False
        self._sync_lock = Lock()
        if settings.vector_backend != "qdrant":
            return
        try:
            from qdrant_client import QdrantClient, models

            self._encoder = create_encoder(settings)
            self.embedding_backend = self._encoder.name
            self._models = models
            self._client = QdrantClient(path=str(settings.qdrant_path))
            fingerprint = {
                "embedding_backend": self.embedding_backend,
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
                self._create_collection()
            metadata_path.write_text(json.dumps(fingerprint, indent=2))
            self.backend = "qdrant_local"
        except Exception:
            self._client = None
            self.embedding_backend = "unavailable"

    def _create_collection(self) -> None:
        self._client.create_collection(
            self.collection,
            vectors_config=self._models.VectorParams(
                size=self.settings.embedding_dimensions,
                distance=self._models.Distance.COSINE,
            ),
        )

    def upsert(self, chunks: list[Chunk]) -> None:
        if not self._client or not chunks:
            return
        vectors = self._encoder.encode_passages([chunk.content for chunk in chunks])
        points = [
            self._models.PointStruct(
                id=int(hashlib.sha256(chunk.id.encode()).hexdigest()[:15], 16),
                vector=vector,
                payload={"chunk_id": chunk.id, "source_id": chunk.source_id},
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(collection_name=self.collection, points=points, wait=True)

    def delete_source(self, source_id: str) -> None:
        if not self._client:
            return
        self._client.delete(
            collection_name=self.collection,
            points_selector=self._models.FilterSelector(
                filter=self._models.Filter(
                    must=[
                        self._models.FieldCondition(
                            key="source_id",
                            match=self._models.MatchValue(value=source_id),
                        )
                    ]
                )
            ),
            wait=True,
        )

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        if not self._client or not chunk_ids:
            return
        point_ids = [
            int(hashlib.sha256(chunk_id.encode()).hexdigest()[:15], 16)
            for chunk_id in chunk_ids
        ]
        self._client.delete(
            collection_name=self.collection,
            points_selector=self._models.PointIdsList(points=point_ids),
            wait=True,
        )

    def replace_source(self, source_id: str, chunks: list[Chunk]) -> None:
        self.delete_source(source_id)
        self.upsert(chunks)

    def synchronize(self, session: Session) -> None:
        """Rebuild Qdrant when its point count diverges from SQLite."""

        if not self._client or self._synchronized:
            return
        with self._sync_lock:
            if self._synchronized:
                return
            active_chunks = select(Chunk).join(Source).where(Source.status == "completed")
            chunks = list(session.scalars(active_chunks).all())
            sql_count = int(
                session.scalar(
                    select(func.count()).select_from(Chunk).join(Source).where(
                        Source.status == "completed"
                    )
                )
                or 0
            )
            vector_count = int(
                self._client.count(collection_name=self.collection, exact=True).count
            )
            if vector_count != sql_count:
                self._client.delete_collection(self.collection)
                self._create_collection()
                self.upsert(chunks)
            self._synchronized = True

    def search(self, session: Session, query: str, top_k: int = 6) -> list[SearchHit]:
        mode = self.settings.retrieval_mode.lower()
        if mode not in {"hybrid", "dense", "lexical"}:
            raise ValueError("retrieval_mode must be 'hybrid', 'dense', or 'lexical'")

        pool_size = max(top_k * 3, self.settings.candidate_k)
        dense_hits = self._dense_search(session, query, pool_size) if mode != "lexical" else []
        lexical_hits = (
            self._bm25_search(session, query, pool_size) if mode != "dense" else []
        )
        if mode == "hybrid" and dense_hits and lexical_hits:
            self.backend = "qdrant_local+bm25"
            return self._rrf_fuse(dense_hits, lexical_hits, top_k)
        if dense_hits:
            return dense_hits[:top_k]
        if mode == "dense":
            self.backend = "sqlite_bm25_fallback"
        return lexical_hits[:top_k] or self._bm25_search(session, query, top_k)

    def _dense_search(self, session: Session, query: str, top_k: int) -> list[SearchHit]:
        if not self._client:
            return []
        try:
            self.synchronize(session)
            result = self._client.query_points(
                collection_name=self.collection,
                query=self._encoder.encode_query(query),
                limit=top_k,
                with_payload=True,
            ).points
            ids = [str(item.payload["chunk_id"]) for item in result]
            chunks = session.scalars(
                select(Chunk)
                .join(Source)
                .where(Chunk.id.in_(ids), Source.status == "completed")
            ).all()
            by_id = {chunk.id: chunk for chunk in chunks}
            return [
                SearchHit(
                    chunk=by_id[str(item.payload["chunk_id"])],
                    retrieval_score=float(item.score),
                    dense_score=float(item.score),
                    fusion_method="dense",
                )
                for item in result
                if str(item.payload["chunk_id"]) in by_id
            ]
        except Exception:
            self.backend = "sqlite_bm25_fallback"
            return []

    def _rrf_fuse(
        self,
        dense_hits: list[SearchHit],
        lexical_hits: list[SearchHit],
        top_k: int,
    ) -> list[SearchHit]:
        """Weighted reciprocal-rank fusion with both channel scores preserved."""

        combined: dict[str, SearchHit] = {}
        scores: dict[str, float] = {}
        rrf_k = max(1, self.settings.hybrid_rrf_k)
        channels = (
            (dense_hits, self.settings.hybrid_dense_weight, "dense"),
            (lexical_hits, self.settings.hybrid_lexical_weight, "lexical"),
        )
        maximum_rrf = sum(weight for _, weight, _ in channels) / (rrf_k + 1)
        for hits, weight, channel in channels:
            for rank, hit in enumerate(hits, start=1):
                chunk_id = hit.chunk.id
                target = combined.setdefault(
                    chunk_id,
                    SearchHit(
                        chunk=hit.chunk,
                        retrieval_score=0,
                        query=hit.query,
                        fusion_method="weighted_rrf",
                    ),
                )
                scores[chunk_id] = scores.get(chunk_id, 0) + weight / (rrf_k + rank)
                if channel == "dense":
                    target.dense_score = hit.retrieval_score
                else:
                    target.lexical_score = hit.retrieval_score
        for chunk_id, hit in combined.items():
            hit.retrieval_score = scores[chunk_id] / max(maximum_rrf, 1e-12)
        return sorted(combined.values(), key=lambda hit: hit.retrieval_score, reverse=True)[:top_k]

    @staticmethod
    def _lexical_search(session: Session, query: str, top_k: int) -> list[SearchHit]:
        return VectorIndex._bm25_search(session, query, top_k)

    @staticmethod
    def _bm25_search(session: Session, query: str, top_k: int) -> list[SearchHit]:
        """BM25 over SQLite-owned chunks; deterministic fallback for FTS deployments."""

        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        chunks = session.scalars(
            select(Chunk).join(Source).where(Source.status == "completed")
        ).all()
        if not chunks:
            return []
        documents = [tokenize(chunk.content) for chunk in chunks]
        average_length = sum(len(tokens) for tokens in documents) / max(1, len(documents))
        document_frequency: Counter[str] = Counter()
        for tokens in documents:
            document_frequency.update(set(tokens))
        total = len(documents)
        k1, b = 1.5, 0.75
        scored: list[SearchHit] = []
        for chunk, tokens in zip(chunks, documents, strict=True):
            if not tokens:
                continue
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                frequency_docs = document_frequency[token]
                inverse_document_frequency = math.log(
                    1 + (total - frequency_docs + 0.5) / (frequency_docs + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * len(tokens) / max(1.0, average_length)
                )
                score += inverse_document_frequency * frequency * (k1 + 1) / denominator
            if score > 0:
                scored.append(
                    SearchHit(
                        chunk=chunk,
                        retrieval_score=score,
                        lexical_score=score,
                        fusion_method="bm25",
                    )
                )
        return sorted(scored, key=lambda hit: hit.retrieval_score, reverse=True)[:top_k]

    @staticmethod
    def keyword_search(
        session: Session, query: str, top_k: int = 6, *, code_only: bool = True
    ) -> list[SearchHit]:
        query_tokens = set(tokenize(query))
        candidates = session.scalars(
            select(Chunk).join(Source).where(Source.status == "completed")
        ).all()
        hits: list[SearchHit] = []
        for chunk in candidates:
            metadata = json.loads(chunk.metadata_json)
            if code_only and metadata.get("category") != "code":
                continue
            content_lower = chunk.content.lower()
            matched = sum(content_lower.count(token) for token in query_tokens)
            if matched:
                hits.append(
                    SearchHit(
                        chunk=chunk,
                        retrieval_score=min(1.0, matched / 5),
                        tool="code_keyword_search",
                    )
                )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]

    @staticmethod
    def issue_search(session: Session, query: str, top_k: int = 6) -> list[SearchHit]:
        issue_source_ids = session.scalars(
            select(Source.id).where(Source.kind == "issue", Source.status == "completed")
        ).all()
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
