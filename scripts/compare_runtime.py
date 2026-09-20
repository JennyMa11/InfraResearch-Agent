#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path, expected_mode: str) -> dict[str, Any]:
    report = json.loads(path.read_text())
    actual = report.get("configuration", {}).get("prefix_cache_mode")
    if actual != expected_mode:
        raise SystemExit(f"{path} is labelled {actual!r}, expected {expected_mode!r}")
    return report


def percent_change(baseline: float, candidate: float) -> float | None:
    if baseline == 0:
        return None
    return (candidate - baseline) / baseline * 100


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare controlled prefix-cache runs")
    parser.add_argument("--off", type=Path, required=True)
    parser.add_argument("--on", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    off = load(args.off, "off")
    on = load(args.on, "on")
    off_summary = off["prefix_cache_summary"]
    on_summary = on["prefix_cache_summary"]
    off_ttft = float(off_summary["warm_median_ttft_ms"])
    on_ttft = float(on_summary["warm_median_ttft_ms"])
    comparison = {
        "off": str(args.off),
        "on": str(args.on),
        "warm_median_ttft_ms": {"off": off_ttft, "on": on_ttft},
        "ttft_change_percent": percent_change(off_ttft, on_ttft),
        "cache_on_counter_deltas": {
            "hits": on_summary.get("prefix_cache_hits_delta"),
            "queries": on_summary.get("prefix_cache_queries_delta"),
        },
        "concurrency": {"off": off["concurrency"], "on": on["concurrency"]},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2))
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
