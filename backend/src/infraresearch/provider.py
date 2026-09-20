from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .config import Settings
from .errors import TaskCancelled
from .schemas import Evidence, ResearchPlan, SubQuestion

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.IGNORECASE | re.DOTALL)
UNFINISHED_THINK_RE = re.compile(r"<think>.*$", re.IGNORECASE | re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Remove model-private reasoning blocks from user-visible output."""
    cleaned = THINK_BLOCK_RE.sub("", text)
    cleaned = UNFINISHED_THINK_RE.sub("", cleaned)
    return cleaned.strip()


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
        cancel_check: Callable[[], None] | None = None,
    ) -> Generation:
        if cancel_check:
            cancel_check()
        if evidence:
            remote = self._remote_generate(question, evidence, on_token, cancel_check)
            if remote is not None:
                return remote
        result = self._extractive_generate(question, evidence)
        if on_token:
            for index in range(0, len(result.text), 120):
                if cancel_check:
                    cancel_check()
                on_token(result.text[index : index + 120])
        return result

    def _remote_generate(
        self,
        question: str,
        evidence: list[Evidence],
        on_token: Callable[[str], None] | None,
        cancel_check: Callable[[], None] | None,
    ) -> Generation | None:
        sources = "\n\n".join(
            f"[{item.id}] locator={item.locator}\n{item.content[:2400]}" for item in evidence
        )
        system = (
            "You are InfraResearch. Answer only from the registered evidence. "
            "Write a concise Markdown technical report. Cite every factual paragraph "
            "with the exact evidence marker such as [S1]. Never invent markers or locators. "
            "Return only the final report; never expose chain-of-thought or <think> blocks."
        )
        payload = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question:\n{question}\n\nEvidence:\n{sources}"},
            ],
        }
        if "qwen3" in self.settings.llm_model.lower():
            payload["chat_template_kwargs"] = {"enable_thinking": False}
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
                        if cancel_check:
                            cancel_check()
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
            text = strip_reasoning("".join(chunks))
            if not text:
                return None
            if on_token:
                for index in range(0, len(text), 120):
                    on_token(text[index : index + 120])
            return Generation(
                text=text,
                prompt_tokens=int(usage.get("prompt_tokens", len(sources.split()))),
                completion_tokens=int(usage.get("completion_tokens", len(text.split()))),
                ttft_ms=ttft_ms,
                provider="openai_compatible",
            )
        except TaskCancelled:
            raise
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
