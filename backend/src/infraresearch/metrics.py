from __future__ import annotations

import re

import httpx

from .config import Settings

METRIC_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*})?\s+([-+0-9.eE]+)$")


def parse_prometheus(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        match = METRIC_LINE.match(line.strip())
        if match:
            values[match.group(1)] = values.get(match.group(1), 0) + float(match.group(2))
    return values


def fetch_vllm_metrics(settings: Settings) -> dict[str, int | float | None]:
    base = settings.llm_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = client.get(f"{base}/metrics")
            response.raise_for_status()
        values = parse_prometheus(response.text)

        def first(*names: str) -> float | None:
            return next((values[name] for name in names if name in values), None)

        hits = first(
            "vllm:prefix_cache_hits_total",
            "vllm:gpu_prefix_cache_hits_total",
        )
        queries = first(
            "vllm:prefix_cache_queries_total",
            "vllm:gpu_prefix_cache_queries_total",
        )
        usage = first(
            "vllm:kv_cache_usage_perc",
            "vllm:gpu_cache_usage_perc",
        )
        return {
            "prefix_cache_hits": int(hits) if hits is not None else None,
            "prefix_cache_queries": int(queries) if queries is not None else None,
            "kv_cache_usage": usage,
        }
    except Exception:
        return {
            "prefix_cache_hits": None,
            "prefix_cache_queries": None,
            "kv_cache_usage": None,
        }
