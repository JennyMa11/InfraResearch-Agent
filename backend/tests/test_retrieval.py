import hashlib
import json

from infraresearch.config import Settings
from infraresearch.models import Chunk, Source, Status
from infraresearch.retrieval import VectorIndex


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
