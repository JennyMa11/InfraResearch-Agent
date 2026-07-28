from infraresearch.agent import latency_percentile
from infraresearch.metrics import parse_prometheus


def test_latency_percentiles() -> None:
    values = [100, 200, 300, 400, 500]
    assert latency_percentile(values, 0.5) == 300
    assert latency_percentile(values, 0.95) == 500
    assert latency_percentile([], 0.95) is None


def test_vllm_prometheus_metrics_are_aggregated() -> None:
    values = parse_prometheus(
        """
# HELP vllm:kv_cache_usage_perc cache
vllm:kv_cache_usage_perc 0.42
vllm:prefix_cache_hits_total{model_name="qwen"} 8
vllm:prefix_cache_hits_total{model_name="other"} 2
"""
    )
    assert values["vllm:kv_cache_usage_perc"] == 0.42
    assert values["vllm:prefix_cache_hits_total"] == 10
