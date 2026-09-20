from __future__ import annotations

import hashlib
import json

from infraresearch.config import Settings
from infraresearch.models import Chunk, Source, Status
from infraresearch.reranking import FastEmbedReranker, IdentityReranker
from infraresearch.retrieval import SearchHit


def make_hits(session) -> list[SearchHit]:
    source = Source(kind="file", name="docs.md", uri="docs.md", status=Status.COMPLETED)
    session.add(source)
    session.flush()
    hits = []
    for content, score in (("irrelevant", 0.9), ("prefix cache", 0.4)):
        chunk = Chunk(
            source_id=source.id,
            content=content,
            locator=f"docs.md#{len(hits)}",
            metadata_json=json.dumps({"category": "docs"}),
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        )
        session.add(chunk)
        hits.append(SearchHit(chunk, score))
    session.commit()
    return hits


def test_identity_reranker_preserves_retrieval_order(session) -> None:
    hits = make_hits(session)
    hits[0].rerank_score = 0.01

    ranked = IdentityReranker().rerank("prefix", hits)

    assert [hit.chunk.content for hit in ranked] == ["irrelevant", "prefix cache"]
    assert all(hit.rerank_score is None for hit in ranked)


def test_fastembed_reranker_keeps_both_scores_without_downloading(session, tmp_path) -> None:
    hits = make_hits(session)
    reranker = FastEmbedReranker(Settings(data_dir=tmp_path))

    class FixedCrossEncoder:
        def rerank(self, query, documents):
            assert query == "prefix"
            assert list(documents) == ["irrelevant", "prefix cache"]
            return [-3.0, 3.0]

    reranker._model = FixedCrossEncoder()
    ranked = reranker.rerank("prefix", hits)

    assert ranked[0].chunk.content == "prefix cache"
    assert ranked[0].retrieval_score == 0.4
    assert ranked[0].rerank_score is not None
    assert ranked[0].rerank_score > 0.9
