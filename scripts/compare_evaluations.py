#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from infraresearch.evaluation import paired_bootstrap_delta_ci


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def validate_comparable(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    left = baseline["summary"]
    right = candidate["summary"]
    checks = {
        "dataset sha256": (left["dataset"]["sha256"], right["dataset"]["sha256"]),
        "corpus sha256": (left["corpus"]["sha256"], right["corpus"]["sha256"]),
        "actual retrieval": (left["actual"], right["actual"]),
    }
    for key in (
        "seed",
        "temperature",
        "provider",
        "vector_backend",
        "embedding_backend",
        "candidate_k",
        "evidence_k",
        "max_agent_rewrites",
        "token_budget",
        "citation_repair",
        "modes",
    ):
        checks[f"configuration.{key}"] = (
            left["configuration"][key],
            right["configuration"][key],
        )
    mismatches = [name for name, pair in checks.items() if pair[0] != pair[1]]
    if mismatches:
        raise SystemExit(f"runs are not comparable: {', '.join(mismatches)}")


def rows_by_mode(report: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    return sorted(
        (row for row in report["rows"] if row["mode"] == mode),
        key=lambda row: row["id"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two controlled evaluation runs")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    args = parser.parse_args()

    baseline = load(args.baseline)
    candidate = load(args.candidate)
    validate_comparable(baseline, candidate)
    left_summary = baseline["summary"]
    right_summary = candidate["summary"]
    seed = int(left_summary["configuration"]["seed"])
    modes: dict[str, Any] = {}
    for offset, mode in enumerate(left_summary["configuration"]["modes"]):
        left_rows = rows_by_mode(baseline, mode)
        right_rows = rows_by_mode(candidate, mode)
        if [row["id"] for row in left_rows] != [row["id"] for row in right_rows]:
            raise SystemExit(f"{mode}: question IDs differ")
        left_scores = [float(row["correctness"]) for row in left_rows]
        right_scores = [float(row["correctness"]) for row in right_rows]
        delta, low, high = paired_bootstrap_delta_ci(
            left_scores,
            right_scores,
            seed=seed + offset,
            samples=args.bootstrap_samples,
        )
        modes[mode] = {
            "questions": len(left_rows),
            "baseline_correctness": mean(left_scores),
            "candidate_correctness": mean(right_scores),
            "correctness_delta": delta,
            "correctness_delta_ci95": {"low": low, "high": high},
            "baseline_raw_citation_recall": mean(
                float(row["raw_citation_recall"]) for row in left_rows
            ),
            "candidate_raw_citation_recall": mean(
                float(row["raw_citation_recall"]) for row in right_rows
            ),
            "baseline_citation_repair_rate": mean(
                bool(row["citation_repair_triggered"]) for row in left_rows
            ),
            "candidate_citation_repair_rate": mean(
                bool(row["citation_repair_triggered"]) for row in right_rows
            ),
        }

    output = {
        "baseline": {
            "path": str(args.baseline),
            "model": left_summary["configuration"]["llm_model"],
            "revision": left_summary["configuration"]["llm_revision"],
        },
        "candidate": {
            "path": str(args.candidate),
            "model": right_summary["configuration"]["llm_model"],
            "revision": right_summary["configuration"]["llm_revision"],
        },
        "dataset_sha256": left_summary["dataset"]["sha256"],
        "corpus_sha256": left_summary["corpus"]["sha256"],
        "bootstrap_samples": args.bootstrap_samples,
        "modes": modes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
