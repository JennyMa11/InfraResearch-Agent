from __future__ import annotations

from collections.abc import Sequence
from math import exp
from typing import Protocol

from .config import Settings
from .retrieval import SearchHit


class Reranker(Protocol):
    """Rank a bounded candidate set without mutating retrieval scores."""

    name: str

    def rerank(self, query: str, hits: Sequence[SearchHit]) -> list[SearchHit]: ...


class IdentityReranker:
    name = "identity"

    def rerank(self, query: str, hits: Sequence[SearchHit]) -> list[SearchHit]:
        del query
        for hit in hits:
            hit.rerank_score = None
        return sorted(hits, key=lambda hit: hit.retrieval_score, reverse=True)


class FastEmbedReranker:
    """ONNX cross-encoder backed by FastEmbed.

    Model loading is intentionally lazy so the API and ingestion worker can start
    without downloading a reranker. A failed load is handled by ResearchAgent and
    falls back to IdentityReranker for that run.
    """

    def __init__(self, settings: Settings):
        self.name = f"fastembed:{settings.reranker_model}"
        self.model_name = settings.reranker_model
        self.cache_dir = settings.resolved_reranker_cache_dir
        self.local_files_only = settings.reranker_local_files_only
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(
                model_name=self.model_name,
                cache_dir=str(self.cache_dir),
                local_files_only=self.local_files_only,
            )
        return self._model

    def rerank(self, query: str, hits: Sequence[SearchHit]) -> list[SearchHit]:
        if not hits:
            return []
        scores = list(self._load().rerank(query, [hit.chunk.content for hit in hits]))
        if len(scores) != len(hits):
            raise ValueError(
                f"reranker returned {len(scores)} scores for {len(hits)} candidates"
            )
        reranked: list[SearchHit] = []
        for hit, score in zip(hits, scores, strict=True):
            raw_score = max(-60.0, min(60.0, float(score)))
            hit.rerank_score = 1.0 / (1.0 + exp(-raw_score))
            reranked.append(hit)
        return sorted(reranked, key=lambda hit: hit.score, reverse=True)


def create_reranker(settings: Settings) -> Reranker:
    if settings.reranker_backend == "identity":
        return IdentityReranker()
    if settings.reranker_backend == "fastembed":
        return FastEmbedReranker(settings)
    raise ValueError(
        "INFRARESEARCH_RERANKER_BACKEND must be 'identity' or 'fastembed', "
        f"got {settings.reranker_backend!r}"
    )
