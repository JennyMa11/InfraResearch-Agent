# 快速开始

## 1. 安装

```bash
cp .env.example .env
make install
```

`make install` 创建 `backend/.venv`，安装 FastEmbed 和前端依赖。首次使用
Qdrant Local 会下载 `multilingual-e5-small` ONNX 模型到
`~/.cache/fastembed`，并创建 `data/qdrant/`。Embedding 后端、模型、维度或
chunk 参数变化时会重建 collection。

完全离线且尚未缓存模型时，可在 `.env` 设置
`INFRARESEARCH_EMBEDDING_BACKEND=hash`。健康接口显示配置值；完成研究后应通过
`metrics.vector_backend` 和来源 metadata 确认 worker 的实际执行路径。

## 2. 启动

```bash
make dev
```

前端在 `http://localhost:5173`，API 在 `http://localhost:8000`。先上传
`evals/corpus.md`，等待状态变成 `completed`，再提交示例问题。

`make dev` 会同时启动前端、API 和独立 worker。分开运行时需要三个终端：

```bash
make frontend
make backend
make worker
```

API 只持久化任务；未启动 worker 时任务会保持 pending，并在 worker 启动后继续。
开发模块导航、任务状态机和提交要求见[开发指南](development.md)。

## 3. 可选模型服务

基础演示不需要生成模型服务。启动 OpenAI-compatible 端点后，在 `.env` 设置
`INFRARESEARCH_LLM_BASE_URL`、`INFRARESEARCH_LLM_MODEL` 和 API key。服务不可达
时会自动降级到抽取式 provider；结果中的 `metrics.provider` 可确认实际路径。

本机 WSL + 6GB RTX 3060 Laptop 的可重复启动流程：

```bash
make setup-vllm
INFRARESEARCH_VLLM_MODEL_PATH=/path/to/Qwen3-0.6B make start-vllm
```

## 4. 评测

```bash
make evaluate
make evaluate-gpu
```

离线结果写到 `evals/results/comparison.{json,csv,md}`，真实模型结果写到
`evals/results/gpu/`。运行结果默认不提交。
