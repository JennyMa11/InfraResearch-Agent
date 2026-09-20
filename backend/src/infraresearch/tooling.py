from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Chunk, Source, Status
from .retrieval import SearchHit, VectorIndex
from .web import validate_public_url


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
    error_type: str | None = None

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


class WebSearch:
    spec = ToolSpec(
        name="web_search",
        description=(
            "Search the public web when indexed local evidence is insufficient; "
            "results are registered as citable Evidence."
        ),
        input_model=SearchToolInput,
        timeout_seconds=20,
        max_results=10,
        max_retries=1,
    )

    def __init__(self, index: VectorIndex):
        self.index = index
        self.settings = index.settings

    def run(self, session: Session, arguments: BaseModel) -> list[SearchHit]:
        payload = SearchToolInput.model_validate(arguments)
        if not self.settings.web_search_api_key:
            raise ValueError("web search is enabled but no API key is configured")
        response = httpx.get(
            self.settings.web_search_endpoint,
            params={"q": payload.query, "count": min(payload.top_k, self.spec.max_results)},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.settings.web_search_api_key,
            },
            timeout=self.spec.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("web", {}).get("results", body.get("results", []))
        hits: list[SearchHit] = []
        changed: list[Chunk] = []
        for rank, item in enumerate(results[: payload.top_k], start=1):
            url = str(item.get("url") or "")
            try:
                validate_public_url(url, resolve=False)
            except ValueError:
                continue
            title = str(item.get("title") or url)
            snippet = str(item.get("description") or item.get("snippet") or "")
            content = f"# {title}\n\n{snippet}".strip()
            if not snippet:
                continue
            source = session.scalar(
                select(Source).where(Source.kind == "web_search", Source.uri == url)
            )
            if source is None:
                source = Source(
                    kind="web_search",
                    name=title[:512],
                    uri=url,
                    status=Status.COMPLETED,
                    metadata_json=json.dumps({"search_query": payload.query}),
                )
                session.add(source)
                session.flush()
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            chunk = session.scalar(
                select(Chunk).where(
                    Chunk.source_id == source.id,
                    Chunk.content_hash == content_hash,
                )
            )
            if chunk is None:
                chunk = Chunk(
                    source_id=source.id,
                    content=content,
                    locator=url,
                    path=None,
                    metadata_json=json.dumps(
                        {"category": "web", "url": url, "search_query": payload.query}
                    ),
                    content_hash=content_hash,
                )
                session.add(chunk)
                session.flush()
                changed.append(chunk)
            hits.append(
                SearchHit(
                    chunk=chunk,
                    retrieval_score=1 / rank,
                    tool="web_search",
                    query=payload.query,
                    fusion_method="web_rank",
                )
            )
        self.index.upsert(changed)
        return hits


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

    def has(self, name: str) -> bool:
        return name in self._tools

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
                error_type="validation",
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
                    error_type=(
                        "timeout" if isinstance(exc, TimeoutError) else "connection"
                    ),
                )
            except Exception as exc:
                return ToolResult(
                    name=name,
                    status="failed",
                    attempts=attempts,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=str(exc)[:500],
                    retryable=False,
                    error_type="internal",
                )

        raise AssertionError("tool retry loop exited unexpectedly")


def create_tool_registry(index: VectorIndex) -> ToolRegistry:
    tools: list[Tool] = [
        SemanticDocumentSearch(index),
        CodeKeywordSearch(index),
        GitHubIssueSearch(index),
    ]
    if index.settings.web_search_enabled:
        tools.append(WebSearch(index))
    return ToolRegistry(tools)
