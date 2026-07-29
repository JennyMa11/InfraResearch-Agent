import hashlib
import json

import pytest
from sqlalchemy import delete

from infraresearch.config import Settings
from infraresearch.models import Chunk, Source, Status
from infraresearch.retrieval import HashEncoder, VectorIndex, create_encoder


def add_chunk(session, content: str, locator: str, category: str = "docs") -> Chunk:
    source = Source(kind="file", name="doc.md", uri="doc.md", status=Status.COMPLETED)
    session.add(source)
    session.flush()
    chunk = Chunk(
        source_id=source.id,
        content=content,
        locator=locator,
        metadata_json=json.dumps({"category": category}),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    session.add(chunk)
    session.commit()
    return chunk


def test_lexical_retrieval_ranks_relevant_chunk(session, tmp_path) -> None:
    relevant = add_chunk(
        session,
        "Prefix caching reuses KV blocks for requests with the same prompt prefix.",
        "cache.md#L1-L3",
    )
    add_chunk(session, "CUDA graphs reduce kernel launch overhead.", "cuda.md#L1-L2")
    index = VectorIndex(
        Settings(data_dir=tmp_path, database_url="sqlite://", vector_backend="sqlite")
    )
    hits = index.search(session, "How does prefix caching reuse KV blocks?", top_k=2)
    assert hits[0].chunk.id == relevant.id
    assert hits[0].score > 0


def test_code_keyword_search_filters_documents(session, tmp_path) -> None:
    code = add_chunk(
        session, "def allocate_kv_cache(blocks): return blocks", "cache.py#L1-L1", "code"
    )
    add_chunk(session, "allocate cache in the user guide", "guide.md#L1-L1", "docs")
    index = VectorIndex(Settings(data_dir=tmp_path, vector_backend="sqlite"))
    hits = index.keyword_search(session, "allocate_kv_cache", top_k=5)
    assert [hit.chunk.id for hit in hits] == [code.id]


def test_hash_encoder_is_an_explicit_offline_fallback(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        vector_backend="qdrant",
        embedding_backend="hash",
        embedding_dimensions=32,
    )
    encoder = create_encoder(settings)
    assert isinstance(encoder, HashEncoder)
    assert encoder.encode_query("prefix cache") == encoder.encode_query("prefix cache")
    assert len(encoder.encode_query("prefix cache")) == 32

    with pytest.raises(ValueError, match="EMBEDDING_BACKEND"):
        create_encoder(Settings(data_dir=tmp_path, embedding_backend="unknown"))


def test_qdrant_source_replacement_removes_stale_points(session, tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        vector_backend="qdrant",
        embedding_backend="hash",
        embedding_dimensions=32,
    )
    settings.ensure_directories()
    index = VectorIndex(settings)
    assert index.backend == "qdrant_local"

    source = Source(kind="file", name="guide.md", uri="guide.md", status=Status.COMPLETED)
    session.add(source)
    session.flush()
    old_chunk = Chunk(
        source_id=source.id,
        content="old prefix cache content",
        locator="guide.md#L1",
        metadata_json=json.dumps({"category": "docs"}),
        content_hash=hashlib.sha256(b"old prefix cache content").hexdigest(),
    )
    session.add(old_chunk)
    session.commit()
    index.upsert([old_chunk])

    session.execute(delete(Chunk).where(Chunk.source_id == source.id))
    new_chunk = Chunk(
        source_id=source.id,
        content="new KV block content",
        locator="guide.md#L2",
        metadata_json=json.dumps({"category": "docs"}),
        content_hash=hashlib.sha256(b"new KV block content").hexdigest(),
    )
    session.add(new_chunk)
    session.flush()
    index.replace_source(source.id, [new_chunk])
    session.commit()

    points, _ = index._client.scroll(
        collection_name=index.collection,
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert [point.payload["chunk_id"] for point in points] == [new_chunk.id]
    index._client.close()


def test_qdrant_synchronizes_from_sqlite_after_collection_rebuild(session, tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        vector_backend="qdrant",
        embedding_backend="hash",
        embedding_dimensions=32,
    )
    settings.ensure_directories()
    index = VectorIndex(settings)
    chunk = add_chunk(
        session,
        "Prefix caching reuses KV blocks.",
        "cache.md#L1",
    )
    assert index._client.count(collection_name=index.collection, exact=True).count == 0

    hits = index.search(session, "prefix caching KV blocks", top_k=1)

    assert hits[0].chunk.id == chunk.id
    assert index._client.count(collection_name=index.collection, exact=True).count == 1
    index._client.close()
