# 评测方法

`evals/questions.json` 固定 20 题，每题包含参考答案、必需证据、期望来源和工具。
`scripts/evaluate.py` 向临时数据库导入同一语料，然后依次运行 Naive 与 Agentic。
每次运行写入独立目录，已经存在的运行目录不会被覆盖。报告记录 Git commit、dirty
状态、操作系统、模型 revision、数据集/语料 SHA256 和完整实验配置。

主要确定性指标：

- 正确性：参考答案与生成答案的归一化 token 覆盖率。
- Citation Precision：行内 marker 映射到已登记 evidence 的比例。
- Citation Recall：被引用证据对题目必需证据短语的覆盖率。
- Recall@K：必需证据短语是否出现在 top-k evidence。
- 工具准确率：实际调用是否包含期望工具。
- Agent 步数、Token、P50/P95 和端到端耗时。

`comparison.json` 额外保存逐题答案、候选分数、Evidence、引用、工具调用和去除
token 后的完整轨迹。典型实验命令：

```bash
uv run --project backend python scripts/evaluate.py \
  --run-name identity-k5 --candidate-k 20 --evidence-k 5
uv run --project backend python scripts/evaluate.py \
  --run-name reranker-k6 --candidate-k 20 --evidence-k 6 \
  --reranker-backend fastembed --reranker-model BAAI/bge-reranker-base
uv run --project backend python scripts/evaluate.py \
  --run-name no-citation-repair --no-citation-repair
```

离线抽取 provider 让 CI 可复现，但不能代表 Qwen 的最终答案质量。GPU/真实模型
结果应另存为带硬件、模型 revision、配置和时间戳的运行文件。不要为了展示效果
选择性删除题目；Agentic 可能与 Naive 持平或更差。
