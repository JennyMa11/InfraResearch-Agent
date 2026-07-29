# 配置

所有后端变量以 `INFRARESEARCH_` 开头，可放在根目录 `.env`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_DIR` | `./data` | SQLite、向量和仓库数据 |
| `DATABASE_URL` | `sqlite:///./data/infraresearch.db` | SQLAlchemy URL |
| `VECTOR_BACKEND` | `qdrant` | `qdrant` 或 `sqlite` 降级 |
| `EMBEDDING_BACKEND` | `fastembed` | `fastembed` 真实语义向量或显式 `hash` 回退 |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-small` | FastEmbed 模型名 |
| `EMBEDDING_DIMENSIONS` | `384` | 向量维度 |
| `EMBEDDING_CACHE_DIR` | `~/.cache/fastembed` | ONNX 模型缓存目录 |
| `EMBEDDING_LOCAL_FILES_ONLY` | `false` | `true` 时禁止模型联网下载 |
| `CHUNK_SIZE` | `1200` | 目标字符数 |
| `CHUNK_OVERLAP` | `120` | 重叠字符数 |
| `LLM_BASE_URL` | `http://127.0.0.1:8001/v1` | OpenAI-compatible 根路径 |
| `LLM_MODEL` | `Qwen/Qwen3-0.6B` | 模型名 |
| `LLM_TIMEOUT_SECONDS` | `60` | 单次请求超时 |
| `WORKER_POLL_SECONDS` | `0.25` | worker 空闲轮询间隔 |
| `WORKER_HEARTBEAT_SECONDS` | `2` | 活跃 job 心跳间隔 |
| `WORKER_STALE_SECONDS` | `30` | 无心跳后可安全重排的租约期限 |
| `MAX_UPLOAD_MB` | `25` | 单文件上限 |
| `MAX_REPO_FILES` | `2000` | 仓库可索引文件上限 |
| `MAX_AGENT_REWRITES` | `2` | 查询重写上限 |
| `TOKEN_BUDGET` | `6000` | 单次生成总预算近似值 |

`GITHUB_TOKEN` 不带 InfraResearch 前缀，用于公开 Issue REST API。不要提交真实
`.env`。修改 Embedding 后端、模型、维度或 chunk 配置会强制重建本地向量
collection。`GET /api/v1/health` 显示 API 读取到的配置；worker 是否发生词法或
extractive 降级，应以完成运行的 `metrics.vector_backend/provider` 为准。
