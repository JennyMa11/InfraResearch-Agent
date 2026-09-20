# InfraResearch 可复现评测

- 版本：`v0.2.0`
- Git：`90662ed`（dirty=true）
- 数据集：50 题，SHA256 `7efe7fa1eedd50f9843851df3fb02fd86345eceb603b9500be2552855afa59d4`
- 运行时间：2026-09-20T04:44:55.449746+00:00
- Provider：`extractive` / `Qwen/Qwen3-0.6B`
- 向量后端：`qdrant_local+bm25`
- Embedding：`hash`
- Reranker：`identity` / `BAAI/bge-reranker-base` @ `unrecorded`
- Reranker Warmup：`0.0 ms`
- Candidate K / Evidence K：`20` / `6`
- Citation Repair：`true`

| 模式 | 正确性 | 引用忠实度 | 引用召回率 | Recall@K | 拒答准确率 | Rewrite 成功率 | 平均步数 | P50 ms | P95 ms | 平均 Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Naive | 0.767 | 1.000 | 0.843 | 0.843 | 0.820 | 0.000 | 5.00 | 38.6 | 42.2 | 210.2 |
| Fixed_Retrieval | 0.764 | 1.000 | 0.863 | 0.863 | 0.820 | 0.780 | 8.00 | 66.3 | 73.8 | 209.9 |
| Agentic | 0.820 | 0.980 | 0.923 | 0.923 | 0.860 | 0.000 | 6.88 | 46.5 | 85.7 | 190.5 |

> 本报告如实记录固定语料上的离线结果，不预设 Agentic RAG 或 Reranker 必然优于基线。逐题证据、工具调用和非 token 轨迹保存在 `comparison.json`。
