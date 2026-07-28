# 快速开始

## 1. 安装

```bash
cp .env.example .env
make install
```

`make install` 创建 `backend/.venv` 并安装前端依赖。首次使用 Qdrant Local 会创建
`data/qdrant/`；配置的 Embedding 模型、维度和 chunk 参数记录在索引元数据中，
任一参数变化都会重建 collection。

## 2. 启动

```bash
make dev
```

前端在 `http://localhost:5173`，API 在 `http://localhost:8000`。先上传
`evals/corpus.md`，等待状态变成 `completed`，再提交示例问题。

## 3. 可选模型服务

基础演示不需要模型服务。启动 OpenAI-compatible 端点后，在 `.env` 设置
`INFRARESEARCH_LLM_BASE_URL`、`INFRARESEARCH_LLM_MODEL` 和 API key。服务不可达
时会自动降级到抽取式 provider；结果中的 `metrics.provider` 可确认实际路径。

## 4. 评测

```bash
make evaluate
```

结果写到 `evals/results/comparison.{json,csv,md}`。运行结果默认不提交。
