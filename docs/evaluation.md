# 评测方法

`evals/questions.json` 固定 50 题，每题包含参考答案、必需证据、期望来源、工具和
困难类型；其中 5 题明确不可回答。`scripts/evaluate.py` 向临时数据库导入同一语料，
然后依次运行 Naive、固定二次检索与 Agentic 自适应策略。
每次运行写入独立目录，已经存在的运行目录不会被覆盖。报告记录 Git commit、dirty
状态、操作系统、模型 revision、数据集/语料 SHA256 和完整实验配置。

主指标与诊断指标：

- 原始正确性：**引用修复前**答案对每题事实 rubric 的覆盖率。rubric 支持一组事实的
  多个等价表达，例如中文术语与英文术语；这是报告中的主正确性指标。
- 95% CI：对 50 道题做 2,000 次确定性 bootstrap；模式差值使用按题配对 bootstrap。
- 修复后正确性：Citation Verifier 处理后的答案，仅作为端到端质量指标，不能代替
  原始正确性评价模型。
- 原始 Citation Precision/Faithfulness/Recall：在修复前答案上计算，用于测量模型
  自己遵循引用协议的能力。
- 修复后 Citation 指标：测量用户最终收到的报告是否具有合法、受证据支持的引用。
- Citation Repair Rate：50 题中触发引用修复的比例。高修复率是模型能力风险，不能
  被修复后的高引用分掩盖。
- 词项参考答案召回：保留在 JSON/CSV 中的旧诊断指标，不再作为主正确性。
- 独立 Retrieval Benchmark：Recall@1/3/5/10、MRR 与 nDCG@10。
- Evidence Recall：必需证据短语是否出现在最终 Evidence。
- 拒答准确率：可回答题是否回答、不可回答题是否明确拒答。
- Rewrite 触发率、成功率、检索上下文 Token 与估算成本。
- 工具准确率：实际调用是否包含期望工具。
- Agent 步数、Token、P50/P95 和端到端耗时。

`comparison.json` 额外保存逐题答案、候选分数、Evidence、引用、工具调用和去除
token 后的完整轨迹，同时保留 `raw_answer` 与修复后 `answer`。典型实验命令：

```bash
uv run --project backend python scripts/evaluate.py \
  --run-name identity-k5 --candidate-k 20 --evidence-k 5
uv run --project backend python scripts/evaluate.py \
  --run-name reranker-k6 --candidate-k 20 --evidence-k 6 \
  --reranker-backend fastembed --reranker-model BAAI/bge-reranker-base
uv run --project backend python scripts/evaluate.py \
  --run-name no-citation-repair --no-citation-repair
```

真实模型公平对比命令（两个模型只替换 model/path/revision）：

```bash
INFRARESEARCH_VLLM_MODEL_PATH=/path/to/Qwen3-0.6B \
INFRARESEARCH_LLM_MODEL=Qwen/Qwen3-0.6B make start-vllm

uv run --project backend python scripts/evaluate.py \
  --run-name roadmap-50q-qwen3-0.6b-full-v2 \
  --provider live --vector-backend qdrant --embedding-backend fastembed \
  --modes naive fixed_retrieval agentic --temperature 0 \
  --llm-model Qwen/Qwen3-0.6B --llm-revision <commit>
```

离线抽取 provider 让 CI 可复现，但不能代表 Qwen 的最终答案质量。不要为了展示效果
选择性删除题目；Agentic 可能与 Naive 持平或更差。

仓库中的真实模型结果位于
`evals/results/roadmap-50q-qwen3-0.6b-full-v2/` 与
`evals/results/roadmap-50q-qwen3-1.7b-full-v2/`。二者使用相同数据 SHA、Qdrant、
FastEmbed、检索参数、三种模式和 temperature=0。离线控制结果位于
`evals/results/roadmap-50q-hybrid/`，其中 Hash Dense 只用于控制管线。

## 2026-09-20 真实模型结论

| 模型 / 模式 | 原始正确性 [95% CI] | 修复后正确性 | 原始引用召回 | 修复率 |
|---|---:|---:|---:|---:|
| Qwen3-0.6B / Naive | 0.717 [0.590, 0.833] | 0.840 | 0.347 | 0.72 |
| Qwen3-0.6B / Fixed | 0.717 [0.587, 0.833] | 0.830 | 0.347 | 0.72 |
| Qwen3-0.6B / Agentic | 0.783 [0.667, 0.880] | 0.870 | 0.447 | 0.60 |
| Qwen3-1.7B / Naive | 0.803 [0.717, 0.883] | 0.823 | 0.677 | 0.40 |
| Qwen3-1.7B / Fixed | 0.793 [0.700, 0.883] | 0.813 | 0.670 | 0.32 |
| Qwen3-1.7B / Agentic | 0.837 [0.763, 0.903] | 0.900 | 0.680 | 0.34 |

同口径下 1.7B 三个模式均优于 0.6B；旧结果相反的主要原因是不同检索后端、只运行
部分模式、旧词项评分以及将引用修复后的输出混入模型能力评价。当前实验修正了这些
混杂因素。跨模型点估计差为 0.087/0.077/0.053，但配对 95% CI 均跨零，不能声称
统计显著。单次 temperature=0 实验的 bootstrap 区间反映题目采样不确定性，不等价于
多随机种子生成方差。可复现模型比较命令：

```bash
uv run --project backend python scripts/compare_evaluations.py \
  --baseline evals/results/roadmap-50q-qwen3-0.6b-full-v2/comparison.json \
  --candidate evals/results/roadmap-50q-qwen3-1.7b-full-v2/comparison.json \
  --output evals/results/model-comparison-qwen3.json
```
