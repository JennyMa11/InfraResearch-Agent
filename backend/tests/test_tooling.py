from __future__ import annotations

import time

from pydantic import BaseModel

from infraresearch.config import Settings
from infraresearch.retrieval import VectorIndex
from infraresearch.tooling import (
    SearchToolInput,
    ToolRegistry,
    ToolSpec,
    create_tool_registry,
)


def test_registry_exposes_json_schemas_and_rejects_invalid_arguments(
    session, tmp_path
) -> None:
    index = VectorIndex(Settings(data_dir=tmp_path, vector_backend="sqlite"))
    registry = create_tool_registry(index)

    specs = {spec.name: spec for spec in registry.specs()}
    assert set(specs) == {
        "semantic_document_search",
        "code_keyword_search",
        "github_issue_search",
    }
    assert specs["semantic_document_search"].input_schema["properties"]["query"]

    invalid = registry.execute(
        session,
        "semantic_document_search",
        {"query": "", "top_k": 500},
    )
    assert invalid.status == "failed"
    assert invalid.attempts == 0
    assert invalid.retryable is False


def test_registry_caps_results_and_retries_only_retryable_errors(session) -> None:
    calls = 0

    class RetryingTool:
        spec = ToolSpec(
            name="retrying",
            description="test",
            input_model=SearchToolInput,
            max_results=2,
            max_retries=1,
        )

        def run(self, session, arguments: BaseModel):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary")
            return [object(), object(), object()]

    registry = ToolRegistry([RetryingTool()])
    result = registry.execute(session, "retrying", {"query": "cache", "top_k": 10})

    assert result.status == "completed"
    assert result.attempts == 2
    assert len(result.hits) == 2


def test_registry_does_not_retry_non_retryable_errors(session) -> None:
    calls = 0

    class BrokenTool:
        spec = ToolSpec(
            name="broken",
            description="test",
            input_model=SearchToolInput,
            max_retries=2,
        )

        def run(self, session, arguments: BaseModel):
            nonlocal calls
            calls += 1
            raise ValueError("bad request")

    result = ToolRegistry([BrokenTool()]).execute(
        session, "broken", {"query": "cache", "top_k": 1}
    )

    assert result.status == "failed"
    assert result.retryable is False
    assert result.attempts == 1
    assert calls == 1


def test_registry_reports_timeout_boundary(session) -> None:
    class TimedOutTool:
        spec = ToolSpec(
            name="timed_out",
            description="test",
            input_model=SearchToolInput,
            timeout_seconds=0,
            max_retries=0,
        )

        def run(self, session, arguments: BaseModel):
            time.sleep(0.001)
            return []

    result = ToolRegistry([TimedOutTool()]).execute(
        session, "timed_out", {"query": "cache", "top_k": 1}
    )

    assert result.status == "failed"
    assert result.retryable is True
    assert result.attempts == 1
    assert "timeout" in (result.error or "")
