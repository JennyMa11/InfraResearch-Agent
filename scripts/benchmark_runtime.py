#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import httpx


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction + 0.999) - 1))
    return ordered[index]


def summarize_prefix_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    ttft = [float(run["metrics"]["ttft_ms"]) for run in runs]
    warm = ttft[1:] or ttft
    first_metrics = runs[0]["metrics"]
    last_metrics = runs[-1]["metrics"]

    def counter_delta(name: str) -> int | None:
        before = first_metrics.get(name)
        after = last_metrics.get(name)
        if before is None or after is None:
            return None
        return int(after) - int(before)

    return {
        "first_ttft_ms": ttft[0],
        "warm_median_ttft_ms": median(warm),
        "warm_p95_ttft_ms": percentile(warm, 0.95),
        "prefix_cache_hits_delta": counter_delta("prefix_cache_hits"),
        "prefix_cache_queries_delta": counter_delta("prefix_cache_queries"),
    }


async def execute_run(
    client: httpx.AsyncClient,
    question: str,
    mode: str,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = await client.post("/api/v1/research", json={"question": question, "mode": mode})
    response.raise_for_status()
    run_id = response.json()["id"]
    deadline = time.monotonic() + timeout
    poll_transport_errors: list[str] = []
    while time.monotonic() < deadline:
        try:
            result = await client.get(f"/api/v1/research/{run_id}")
        except httpx.TransportError as exc:
            poll_transport_errors.append(type(exc).__name__)
            await asyncio.sleep(poll_interval)
            continue
        result.raise_for_status()
        body = result.json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            body["client_latency_ms"] = (time.perf_counter() - started) * 1000
            body["poll_transport_errors"] = poll_transport_errors
            return body
        await asyncio.sleep(poll_interval)
    raise TimeoutError(f"research run {run_id} exceeded {timeout:.1f}s")


async def concurrency_trial(
    client: httpx.AsyncClient,
    question: str,
    mode: str,
    concurrency: int,
    requests: int,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(index: int) -> dict[str, Any]:
        async with semaphore:
            return await execute_run(
                client,
                f"{question} [load-{index}]",
                mode,
                timeout,
                poll_interval,
            )

    started = time.perf_counter()
    results = await asyncio.gather(
        *(bounded(index) for index in range(requests)),
        return_exceptions=True,
    )
    elapsed = time.perf_counter() - started
    completed = [item for item in results if isinstance(item, dict)]
    errors = [f"{type(item).__name__}: {item}" for item in results if isinstance(item, Exception)]
    poll_transport_errors = [
        error
        for item in completed
        for error in item.get("poll_transport_errors", [])
    ]
    latencies = [float(item["client_latency_ms"]) for item in completed]
    return {
        "concurrency": concurrency,
        "requests": requests,
        "completed": len(completed),
        "failed": requests - len(completed),
        "errors": errors,
        "poll_transport_retries": len(poll_transport_errors),
        "poll_transport_error_types": sorted(set(poll_transport_errors)),
        "success_rate": len(completed) / requests,
        "throughput_tasks_per_second": len(completed) / elapsed,
        "p50_ms": median(latencies) if latencies else None,
        "p95_ms": percentile(latencies, 0.95) if latencies else None,
        "wall_seconds": elapsed,
    }


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.http_timeout) as client:
        health = (await client.get("/api/v1/health")).json()
        prefix_runs = [
            await execute_run(
                client,
                args.question,
                args.mode,
                args.task_timeout,
                args.poll_interval,
            )
            for _ in range(args.prefix_repeats)
        ]
        concurrency = [
            await concurrency_trial(
                client,
                args.question,
                args.mode,
                level,
                max(args.requests_per_level, level),
                args.task_timeout,
                args.poll_interval,
            )
            for level in args.concurrency
        ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "health": health,
        "configuration": {
            "mode": args.mode,
            "question": args.question,
            "prefix_repeats": args.prefix_repeats,
            "concurrency": args.concurrency,
            "requests_per_level": args.requests_per_level,
            "poll_interval_seconds": args.poll_interval,
            "http_timeout_seconds": args.http_timeout,
            "prefix_cache_mode": args.prefix_cache_mode,
        },
        "prefix_cache_summary": summarize_prefix_runs(prefix_runs),
        "prefix_cache": [
            {
                "run_id": run["id"],
                "ttft_ms": run["metrics"]["ttft_ms"],
                "total_latency_ms": run["metrics"]["total_latency_ms"],
                "client_latency_ms": run["client_latency_ms"],
                "prefix_cache_hits": run["metrics"]["prefix_cache_hits"],
                "prefix_cache_queries": run["metrics"]["prefix_cache_queries"],
                "provider": run["metrics"]["provider"],
            }
            for run in prefix_runs
        ],
        "concurrency": concurrency,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prefix-cache and concurrent API benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--question", default="Explain prefix caching and its effect on output.")
    parser.add_argument(
        "--mode",
        choices=("naive", "fixed_retrieval", "agentic"),
        default="agentic",
    )
    parser.add_argument("--prefix-repeats", type=int, default=5)
    parser.add_argument(
        "--prefix-cache-mode",
        choices=("on", "off", "uncontrolled"),
        default="uncontrolled",
        help="Label the server configuration; restart vLLM to change the actual mode.",
    )
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8, 16])
    parser.add_argument("--requests-per-level", type=int, default=16)
    parser.add_argument("--task-timeout", type=float, default=300)
    parser.add_argument("--http-timeout", type=float, default=60)
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument("--output", type=Path, default=Path("evals/results/runtime.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(main_async(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
