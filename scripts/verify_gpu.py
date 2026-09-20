#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from typing import Any

import httpx
from infraresearch.metrics import parse_prometheus


class GPUVerificationFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GPUVerificationFailure(message)


def metric(values: dict[str, float], *names: str) -> float | None:
    return next((values[name] for name in names if name in values), None)


def fetch_metrics(client: httpx.Client) -> dict[str, float]:
    response = client.get("/metrics")
    response.raise_for_status()
    values = parse_prometheus(response.text)
    require(values, "vLLM metrics endpoint returned no Prometheus samples")
    return values


def stream_completion(
    client: httpx.Client,
    model: str,
    prompt: str,
    *,
    max_tokens: int,
) -> tuple[str, float, dict[str, Any]]:
    started = time.perf_counter()
    first_token_at: float | None = None
    text: list[str] = []
    usage: dict[str, Any] = {}
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": prompt}],
    }
    if "qwen3" in model.lower():
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json=payload,
        timeout=300,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line.removeprefix("data:").strip()
            if payload == "[DONE]":
                break
            body = json.loads(payload)
            usage = body.get("usage") or usage
            choices = body.get("choices") or []
            token = choices[0].get("delta", {}).get("content") if choices else None
            if token:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                text.append(token)
    require(first_token_at is not None, "stream returned no token")
    result = "".join(text)
    require(result, "streamed completion is empty")
    return result, (first_token_at - started) * 1000, usage


def verify_gpu_is_visible() -> str:
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        # WSL exposes the Windows driver utility here without always adding it
        # to PATH. Treat that as a normal GPU installation, not a failed probe.
        wsl_nvidia_smi = "/usr/lib/wsl/lib/nvidia-smi"
        if os.access(wsl_nvidia_smi, os.X_OK):
            nvidia_smi = wsl_nvidia_smi
    require(nvidia_smi is not None, "nvidia-smi was not found in PATH or the WSL driver path")
    result = subprocess.run(
        [
            nvidia_smi,
            "--query-gpu=index,name,memory.total,memory.used,driver_version",
            "--format=csv,noheader",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    gpu = result.stdout.strip()
    require(bool(gpu), "nvidia-smi returned no GPU")
    return gpu


def run(args: argparse.Namespace) -> dict[str, Any]:
    gpu = verify_gpu_is_visible()
    base_url = args.base_url.removesuffix("/v1").rstrip("/")
    with httpx.Client(base_url=base_url, trust_env=False, timeout=30) as client:
        health = client.get("/health")
        health.raise_for_status()
        models_response = client.get("/v1/models")
        models_response.raise_for_status()
        models = models_response.json()["data"]
        require(models, "vLLM exposed no models")
        model = args.model or models[0]["id"]
        registered = {item["id"] for item in models}
        require(model in registered, f"model {model!r} is not registered: {sorted(registered)}")
        model_info = next(item for item in models if item["id"] == model)

        before = fetch_metrics(client)
        prompt = "Explain prefix caching in one short sentence."
        first_text, first_ttft, first_usage = stream_completion(
            client,
            model,
            prompt,
            max_tokens=48,
        )
        require("<think>" not in first_text.lower(), "Qwen exposed a private <think> block")
        _, second_ttft, second_usage = stream_completion(
            client,
            model,
            prompt,
            max_tokens=48,
        )
        after = fetch_metrics(client)

        query_names = (
            "vllm:prefix_cache_queries_total",
            "vllm:gpu_prefix_cache_queries_total",
        )
        hit_names = (
            "vllm:prefix_cache_hits_total",
            "vllm:gpu_prefix_cache_hits_total",
        )
        before_queries = metric(before, *query_names)
        after_queries = metric(after, *query_names)
        before_hits = metric(before, *hit_names)
        after_hits = metric(after, *hit_names)
        require(after_queries is not None, "prefix cache query metric is missing")
        require(after_hits is not None, "prefix cache hit metric is missing")
        if before_queries is not None:
            require(after_queries > before_queries, "prefix cache query counter did not increase")
        if before_hits is not None:
            require(after_hits >= before_hits, "prefix cache hit counter decreased")

        long_context: dict[str, Any] | None = None
        if not args.skip_long_context:
            max_model_len = int(model_info.get("max_model_len") or 8192)
            target_tokens = min(args.context_tokens, max(512, max_model_len - 256))
            long_prompt = ("cache " * target_tokens) + "\nReply with one word."
            _, long_ttft, long_usage = stream_completion(
                client,
                model,
                long_prompt,
                max_tokens=1,
            )
            long_context = {
                "target_tokens": target_tokens,
                "ttft_ms": round(long_ttft, 2),
                "usage": long_usage,
            }

        return {
            "gpu": gpu,
            "model": model,
            "max_model_len": model_info.get("max_model_len"),
            "streaming": {
                "first_ttft_ms": round(first_ttft, 2),
                "second_ttft_ms": round(second_ttft, 2),
                "first_usage": first_usage,
                "second_usage": second_usage,
                "sample": first_text[:160],
            },
            "prefix_cache": {
                "queries_before": before_queries,
                "queries_after": after_queries,
                "hits_before": before_hits,
                "hits_after": after_hits,
            },
            "long_context": long_context,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a live GPU-backed vLLM endpoint.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("INFRARESEARCH_LLM_BASE_URL", "http://127.0.0.1:8001/v1"),
    )
    parser.add_argument("--model", default=os.getenv("INFRARESEARCH_LLM_MODEL"))
    parser.add_argument("--context-tokens", type=int, default=7000)
    parser.add_argument("--skip-long-context", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args)
    except (
        GPUVerificationFailure,
        httpx.HTTPError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        raise SystemExit(f"GPU verification failed: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
