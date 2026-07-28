from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .config import Settings
from .schemas import Evidence, ResearchPlan, SubQuestion


@dataclass(slots=True)
class Generation:
    text: str
    prompt_tokens: int
    completion_tokens: int
    ttft_ms: float | None
    provider: str


class LLMProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def plan(self, question: str) -> ResearchPlan:
        lower = question.lower()
        evidence_type = "mixed"
        if any(word in lower for word in ("issue", "bug", "报错", "故障")):
            evidence_type = "issues"
        elif any(word in lower for word in ("code", "function", "class", "实现", "源码")):
            evidence_type = "code"
        parts = [
            part.strip(" ?？.")
            for part in __import__("re").split(r"[;；。]|\band\b|\b以及\b", question)
            if part.strip()
        ][:3]
        return ResearchPlan(
            question_type="technical",
            subquestions=[
                SubQuestion(question=part or question, expected_evidence=evidence_type)
                for part in (parts or [question])
            ],
        )

    def generate(
        self,
        question: str,
        evidence: list[Evidence],
        on_token: Callable[[str], None] | None = None,
    ) -> Generation:
        if evidence:
            remote = self._remote_generate(question, evidence, on_token)
            if remote is not None:
                return remote
        result = self._extractive_generate(question, evidence)
        if on_token:
            for index in range(0, len(result.text), 120):
                on_token(result.text[index : index + 120])
        return result

    def _remote_generate(
        self,
        question: str,
        evidence: list[Evidence],
        on_token: Callable[[str], None] | None,
    ) -> Generation | None:
        sources = "\n\n".join(
            f"[{item.id}] locator={item.locator}\n{item.content[:2400]}" for item in evidence
        )
        system = (
            "You are InfraResearch. Answer only from the registered evidence. "
            "Write a concise Markdown technical report. Cite every factual paragraph "
            "with the exact evidence marker such as [S1]. Never invent markers or locators."
        )
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.1,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question:\n{question}\n\nEvidence:\n{sources}"},
            ],
        }
        started = time.perf_counter()
        try:
            chunks: list[str] = []
            usage: dict = {}
            ttft_ms: float | None = None
            with httpx.Client(trust_env=False) as client:
                with client.stream(
                    "POST",
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.settings.llm_timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        body = json.loads(data)
                        usage = body.get("usage") or usage
                        choices = body.get("choices") or []
                        content = choices[0].get("delta", {}).get("content") if choices else None
                        if content:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - started) * 1000
                            chunks.append(content)
                            if on_token:
                                on_token(content)
            text = "".join(chunks)
            if not text:
                return None
            return Generation(
                text=text,
                prompt_tokens=int(usage.get("prompt_tokens", len(sources.split()))),
                completion_tokens=int(usage.get("completion_tokens", len(text.split()))),
                ttft_ms=ttft_ms,
                provider="openai_compatible",
            )
        except Exception:
            # Provider connectivity, proxy extras and malformed responses are all
            # recoverable in local-first mode. The run records the actual fallback.
            return None

    @staticmethod
    def _extractive_generate(question: str, evidence: list[Evidence]) -> Generation:
        if not evidence:
            text = (
                "## 结论\n\n证据不足：当前索引中没有找到能够回答该问题的资料。"
                "请先导入相关文档或仓库。"
            )
        else:
            sections = ["## 研究结论", f"\n问题：{question}\n"]
            for item in evidence[:5]:
                excerpt = " ".join(item.content.strip().split())
                if len(excerpt) > 360:
                    excerpt = excerpt[:357] + "..."
                sections.append(f"- {excerpt} [{item.id}]")
            sections.append("\n## 限制\n\n以上结论仅基于当前已索引的证据。")
            text = "\n".join(sections)
        return Generation(
            text=text,
            prompt_tokens=sum(len(item.content.split()) for item in evidence),
            completion_tokens=len(text.split()),
            ttft_ms=0,
            provider="extractive",
        )
