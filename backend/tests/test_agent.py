import hashlib
import json

from sqlalchemy import select

from infraresearch.agent import ResearchAgent
from infraresearch.config import Settings
from infraresearch.models import Chunk, ResearchRun, Source, Status, ToolCall, TraceEvent
from infraresearch.provider import Generation, LLMProvider
from infraresearch.retrieval import VectorIndex
from infraresearch.schemas import Evidence


class FixedProvider(LLMProvider):
    def __init__(self, settings: Settings, answer: str = "Supported claim [S1]."):
        super().__init__(settings)
        self.answer = answer
        self.seen_evidence: list[Evidence] = []

    def generate(self, question: str, evidence: list[Evidence], on_token=None) -> Generation:
        self.seen_evidence = evidence
        if on_token:
            on_token(self.answer)
        return Generation(
            text=self.answer,
            prompt_tokens=sum(len(item.content) // 4 for item in evidence),
            completion_tokens=4,
            ttft_ms=2,
            provider="fixed",
        )


def seed(session, content: str = "Prefix caching reuses KV cache blocks.") -> None:
    source = Source(kind="file", name="cache.md", uri="cache.md", status=Status.COMPLETED)
    session.add(source)
    session.flush()
    session.add(
        Chunk(
            source_id=source.id,
            content=content,
            locator="cache.md#L1-L3",
            metadata_json=json.dumps({"category": "docs"}),
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        )
    )
    session.commit()


def make_agent(tmp_path, provider: FixedProvider) -> ResearchAgent:
    settings = provider.settings
    return ResearchAgent(settings, VectorIndex(settings), provider)


def test_agentic_run_is_bounded_and_preserves_trace(session, tmp_path) -> None:
    seed(session)
    settings = Settings(
        data_dir=tmp_path,
        vector_backend="sqlite",
        max_agent_rewrites=2,
        token_budget=1000,
    )
    provider = FixedProvider(settings)
    run = ResearchRun(question="unmatched obscure question", mode="agentic")
    session.add(run)
    session.commit()
    make_agent(tmp_path, provider).execute(session, run.id, top_k=3)
    session.refresh(run)
    events = session.scalars(
        select(TraceEvent).where(TraceEvent.run_id == run.id).order_by(TraceEvent.sequence)
    ).all()
    rewrites = [event for event in events if event.event_type == "query_rewritten"]
    assert run.status == Status.COMPLETED
    assert len(rewrites) <= 2
    assert events[0].event_type == "run_started"
    assert events[-1].event_type == "run_completed"


def test_naive_run_only_retrieves_once(session, tmp_path) -> None:
    seed(session)
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    provider = FixedProvider(settings)
    run = ResearchRun(question="How are prefix cache blocks reused?", mode="naive")
    session.add(run)
    session.commit()
    make_agent(tmp_path, provider).execute(session, run.id)
    metrics = json.loads(run.metrics_json)
    assert metrics["retrieval_rounds"] == 1
    assert run.status == Status.COMPLETED


def test_invalid_citation_is_repaired_only_once(session, tmp_path) -> None:
    seed(session)
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    provider = FixedProvider(settings, "Unsupported marker [S99].")
    run = ResearchRun(question="Explain prefix caching", mode="agentic")
    session.add(run)
    session.commit()
    make_agent(tmp_path, provider).execute(session, run.id)
    session.refresh(run)
    assert "[S99]" not in (run.answer or "")
    assert "证据不足" in (run.answer or "")
    verifier = [
        json.loads(event.data_json)
        for event in run.events
        if event.event_type == "node_completed" and event.node == "citation_verifier"
    ]
    assert verifier[0]["repair_attempts"] == 1


def test_token_budget_truncates_generation_context(session, tmp_path) -> None:
    seed(session, "cache " * 5000)
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite", token_budget=1000)
    provider = FixedProvider(settings)
    run = ResearchRun(question="cache", mode="naive")
    session.add(run)
    session.commit()
    make_agent(tmp_path, provider).execute(session, run.id)
    assert sum(len(item.content) // 4 for item in provider.seen_evidence) <= 256


def test_single_tool_failure_keeps_other_evidence(session, tmp_path) -> None:
    seed(session, "def prefix_cache(): reuse_blocks()")
    session.scalar(select(Chunk)).metadata_json = json.dumps({"category": "code"})
    session.commit()
    settings = Settings(data_dir=tmp_path, vector_backend="sqlite")
    provider = FixedProvider(settings)

    class PartiallyBrokenIndex(VectorIndex):
        def search(self, session, query, top_k=6):
            raise TimeoutError("semantic backend timed out")

    run = ResearchRun(question="How is prefix_cache implemented in code?", mode="agentic")
    session.add(run)
    session.commit()
    ResearchAgent(settings, PartiallyBrokenIndex(settings), provider).execute(session, run.id)
    session.refresh(run)
    tools = session.scalars(select(ToolCall)).all()
    assert run.status == Status.COMPLETED
    assert any(tool.status == Status.FAILED for tool in tools)
    assert provider.seen_evidence
