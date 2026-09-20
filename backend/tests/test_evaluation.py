from infraresearch.evaluation import (
    bootstrap_mean_ci,
    fact_coverage_score,
    normalize_fact_text,
    paired_bootstrap_delta_ci,
)


def test_fact_coverage_accepts_declared_paraphrases() -> None:
    item = {
        "required_evidence": ["unused fallback"],
        "answer_rubric": [
            ["兼容端点", "兼容的端点"],
            ["确定性抽取 provider", "extractive provider"],
        ],
    }

    assert fact_coverage_score(item, "切换到兼容的端点，并使用 extractive provider。") == 1
    assert fact_coverage_score(item, "只能切换端点。") == 0


def test_fact_coverage_defaults_to_required_evidence() -> None:
    item = {"required_evidence": ["KV cache", "不改变输出"]}

    assert fact_coverage_score(item, "复用 KV-cache，而且不改变输出。") == 1
    assert normalize_fact_text("KV-cache") == normalize_fact_text("KV cache")


def test_bootstrap_intervals_are_deterministic_and_paired() -> None:
    first = bootstrap_mean_ci([0.0, 0.5, 1.0], seed=17, samples=200)
    second = bootstrap_mean_ci([0.0, 0.5, 1.0], seed=17, samples=200)
    delta, low, high = paired_bootstrap_delta_ci(
        [0.0, 0.5, 0.5],
        [0.5, 0.5, 1.0],
        seed=17,
        samples=200,
    )

    assert first == second
    assert low <= delta <= high
    assert delta == 1 / 3
