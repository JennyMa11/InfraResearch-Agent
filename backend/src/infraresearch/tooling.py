from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from .retrieval import SearchHit, VectorIndex


class SearchToolInput(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=20, ge=1, le=100)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    timeout_seconds: float = 10.0
    max_results: int = 100
    max_retries: int = 1

    @property
    def input_schema(self) -> dict:
        return self.input_model.model_json_schema()


@dataclass(slots=True)
class ToolResult:
    name: str
    status: str
    hits: list[SearchHit] = field(default_factory=list)
    attempts: int = 0
    duration_ms: float = 0
    error: str | None = None
    retryable: bool = False

    @property
    def summary(self) -> str:
        return f"{len(self.hits)} results"


class Tool(Protocol):
    spec: ToolSpec

    def run(self, session: Session, arguments: BaseModel) -> list[SearchHit]: ...


class SemanticDocumentSearch:
    spec = ToolSpec(
        name="semantic_document_search",
        description="Search active document and code chunks using the configured vector backend.",
        input_model=SearchToolInput,
    )

    def __init__(self, index: VectorIndex):
        self.index = index

    def run(self, session: Session, arguments: BaseModel) -> list[SearchHit]:
        payload = SearchToolInput.model_validate(arguments)
        return self.index.search(session, payload.query, payload.top_k)


class CodeKeywordSearch:
    spec = ToolSpec(
        name="code_keyword_search",
        description=(
            "Search active source-code chunks using exact and token-aware keyword matching."
        ),
        input_model=SearchToolInput,
    )

    def __init__(self, index: VectorIndex):
        self.index = index

    def run(self, session: Session, arguments: BaseModel) -> list[SearchHit]:
        payload = SearchToolInput.model_validate(arguments)
        return self.index.keyword_search(session, payload.query, payload.top_k)


class GitHubIssueSearch:
    spec = ToolSpec(
        name="github_issue_search",
        description="Search imported public GitHub Issue chunks.",
        input_model=SearchToolInput,
    )

    def __init__(self, index: VectorIndex):
        self.index = index

    def run(self, session: Session, arguments: BaseModel) -> list[SearchHit]:
        payload = SearchToolInput.model_validate(arguments)
        return self.index.issue_search(session, payload.query, payload.top_k)


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"tool already registered: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def execute(
        self,
        session: Session,
        name: str,
        arguments: dict,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            tool = self.get(name)
            payload = tool.spec.input_model.model_validate(arguments)
        except (KeyError, ValidationError, ValueError) as exc:
            return ToolResult(
                name=name,
                status="failed",
                duration_ms=(time.perf_counter() - started) * 1000,
                error=str(exc)[:500],
                retryable=False,
            )

        if isinstance(payload, SearchToolInput):
            payload.top_k = min(payload.top_k, tool.spec.max_results)

        retryable_errors = (TimeoutError, ConnectionError)
        attempts = 0
        while attempts <= tool.spec.max_retries:
            attempts += 1
            if cancel_check:
                cancel_check()
            attempt_started = time.perf_counter()
            try:
                hits = tool.run(session, payload)
                elapsed = time.perf_counter() - attempt_started
                if elapsed > tool.spec.timeout_seconds:
                    raise TimeoutError(
                        f"tool exceeded {tool.spec.timeout_seconds:.1f}s timeout"
                    )
                return ToolResult(
                    name=name,
                    status="completed",
                    hits=hits[: tool.spec.max_results],
                    attempts=attempts,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            except retryable_errors as exc:
                if attempts <= tool.spec.max_retries:
                    continue
                return ToolResult(
                    name=name,
                    status="failed",
                    attempts=attempts,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=str(exc)[:500],
                    retryable=True,
                )
            except Exception as exc:
                return ToolResult(
                    name=name,
                    status="failed",
                    attempts=attempts,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=str(exc)[:500],
                    retryable=False,
                )

        raise AssertionError("tool retry loop exited unexpectedly")


def create_tool_registry(index: VectorIndex) -> ToolRegistry:
    return ToolRegistry(
        [
            SemanticDocumentSearch(index),
            CodeKeywordSearch(index),
            GitHubIssueSearch(index),
        ]
    )
