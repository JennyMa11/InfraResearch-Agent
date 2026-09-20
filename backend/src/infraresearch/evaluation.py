from __future__ import annotations

import random
import re
import unicodedata
from statistics import mean
from typing import Any


def normalize_fact_text(value: str) -> str:
    """Normalize wording without turning the evaluator into a tokenizer benchmark."""

    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("的", "")
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE)


def answer_rubric(item: dict[str, Any]) -> list[list[str]]:
    """Return independently scored facts, each with one or more accepted wordings."""

    configured = item.get("answer_rubric")
    if configured is not None:
        return [
            [str(alias) for alias in (fact if isinstance(fact, list) else [fact])]
            for fact in configured
        ]
    return [[str(term)] for term in item.get("required_evidence", [])]


def fact_coverage_score(item: dict[str, Any], answer: str) -> float:
    """Score required answer facts; aliases make paraphrases explicitly auditable."""

    rubric = answer_rubric(item)
    if not rubric:
        return 0.0
    normalized_answer = normalize_fact_text(answer)
    matched = sum(
        any(normalize_fact_text(alias) in normalized_answer for alias in aliases)
        for aliases in rubric
    )
    return matched / len(rubric)


def bootstrap_mean_ci(
    values: list[float],
    *,
    seed: int,
    samples: int = 2_000,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return a deterministic percentile bootstrap interval for a mean."""

    if not values:
        return 0.0, 0.0
    if len(set(values)) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    size = len(values)
    estimates = sorted(
        mean(values[rng.randrange(size)] for _ in range(size)) for _ in range(samples)
    )
    tail = (1 - confidence) / 2
    low = estimates[max(0, int(tail * samples))]
    high = estimates[min(samples - 1, int((1 - tail) * samples) - 1)]
    return low, high


def paired_bootstrap_delta_ci(
    baseline: list[float],
    candidate: list[float],
    *,
    seed: int,
    samples: int = 2_000,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Return mean(candidate-baseline) and its paired bootstrap interval."""

    if len(baseline) != len(candidate) or not baseline:
        raise ValueError("paired samples must be non-empty and have equal length")
    differences = [right - left for left, right in zip(baseline, candidate, strict=True)]
    low, high = bootstrap_mean_ci(
        differences,
        seed=seed,
        samples=samples,
        confidence=confidence,
    )
    return mean(differences), low, high
