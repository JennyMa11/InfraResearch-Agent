# InfraResearch 可复现评测

- 版本：`v0.3.0`
- Git：`90662ed`（dirty=true）
- 数据集：50 题，SHA256 `6de98701ed19f4e50bbdc7b083201ad042a97b2ba07d8c0a8dbe4a0dee0f6785`
- 运行时间：2026-09-20T06:24:54.961703+00:00
- Provider：`live` / `Qwen/Qwen3-0.6B`
- 向量后端：`qdrant_local+bm25`
- Embedding：`fastembed`
- Reranker：`identity` / `BAAI/bge-reranker-base` @ `unrecorded`
- Reranker Warmup：`0.0 ms`
- Candidate K / Evidence K：`20` / `6`
- Citation Repair：`true`
- 生成温度：`0.0`
- 主正确性：引用修复前答案的事实 rubric 覆盖率（95% bootstrap CI）
- 词项参考答案召回：仅保留在 JSON/CSV 中作为诊断指标

| 模式 | 原始正确性 [95% CI] | 修复后正确性 | 原始引用忠实度 | 原始引用召回 | 修复后引用召回 | 修复率 | Recall@K | 拒答准确率 | P50 ms | 平均 Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Naive | 0.717 [0.590, 0.833] | 0.840 | 0.358 | 0.347 | 0.857 | 0.720 | 0.883 | 0.820 | 3170.4 | 872.0 |
| Fixed_Retrieval | 0.717 [0.587, 0.833] | 0.830 | 0.347 | 0.347 | 0.877 | 0.720 | 0.883 | 0.840 | 2845.7 | 868.5 |
| Agentic | 0.783 [0.667, 0.880] | 0.870 | 0.450 | 0.447 | 0.957 | 0.600 | 0.963 | 0.900 | 3006.0 | 820.2 |

| 配对比较 | 原始正确性差值 [95% CI] | 题数 |
|---|---:|---:|
| fixed_retrieval − naive | +0.000 [-0.040, +0.040] | 50 |
| agentic − naive | +0.067 [-0.007, +0.147] | 50 |

> 本报告如实记录固定语料上的真实模型结果，不预设 Agentic RAG 或 Reranker 必然优于基线。逐题证据、工具调用和非 token 轨迹保存在 `comparison.json`。
