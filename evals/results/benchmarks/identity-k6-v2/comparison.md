# InfraResearch 可复现评测

- 版本：`v0.1.1`
- Git：`0329081`（dirty=true）
- 数据集：20 题，SHA256 `417a721e1b1a699104c1c466f0ceab0f28ea03b1aec588a064a1078f221dfa29`
- 运行时间：2026-09-20T03:41:11.979068+00:00
- Provider：`extractive` / `Qwen/Qwen3-0.6B`
- 向量后端：`sqlite_lexical`
- Embedding：`none`
- Reranker：`identity` / `BAAI/bge-reranker-base`
- Reranker Warmup：`0.0 ms`
- Candidate K / Evidence K：`20` / `6`
- Citation Repair：`true`

| 模式 | 正确性 | 引用精确率 | 引用召回率 | Recall@K | 工具准确率 | 平均步数 | 平均工具数 | P50 ms | P95 ms | Rerank ms | 平均 Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Naive | 0.901 | 1.000 | 0.975 | 0.975 | 1.000 | 5.00 | 1.00 | 38.4 | 43.6 | 0.0 | 153.9 |
| Agentic | 0.901 | 1.000 | 0.975 | 0.975 | 1.000 | 6.20 | 3.25 | 46.1 | 51.4 | 0.0 | 153.9 |

> 本报告如实记录固定语料上的离线结果，不预设 Agentic RAG 或 Reranker 必然优于基线。逐题证据、工具调用和非 token 轨迹保存在 `comparison.json`。
