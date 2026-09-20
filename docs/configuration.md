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
| `RERANKER_BACKEND` | `identity` | `identity` 关闭精排，`fastembed` 启用 ONNX Cross-Encoder |
| `RERANKER_MODEL` | `BAAI/bge-reranker-base` | FastEmbed Cross-Encoder 模型 |
| `RERANKER_CACHE_DIR` | `~/.cache/fastembed` | Reranker 模型缓存目录 |
| `RERANKER_LOCAL_FILES_ONLY` | `false` | `true` 时只使用已缓存 Reranker |
| `CANDIDATE_K` | `20` | 每个检索工具的候选召回上限 |
| `EVIDENCE_K` | `6` | 精排后进入 Evidence 的数量 |
| `RETRIEVAL_MODE` | `hybrid` | `hybrid`、`dense` 或 `lexical` |
| `HYBRID_RRF_K` | `60` | Weighted RRF 平滑常数 |
| `HYBRID_DENSE_WEIGHT` | `1.0` | Dense 通道权重 |
| `HYBRID_LEXICAL_WEIGHT` | `1.0` | BM25 通道权重 |
| `CHUNK_SIZE` | `1200` | 目标字符数 |
| `CHUNK_OVERLAP` | `120` | 重叠字符数 |
| `PDF_LAYOUT_ENABLED` | `true` | 保留 PDF reading order 与 bbox |
| `PDF_OCR_ENABLED` | `true` | 文本层不足时启用可选 OCR fallback |
| `PDF_OCR_LANGUAGE` | `eng` | Tesseract language 参数 |
| `LLM_BASE_URL` | `http://127.0.0.1:8001/v1` | OpenAI-compatible 根路径 |
| `LLM_MODEL` | `Qwen/Qwen3-0.6B` | 模型名 |
| `LLM_TIMEOUT_SECONDS` | `60` | 单次请求超时 |
| `LLM_TEMPERATURE` | `0.1` | 生成温度；公平评测显式设为 `0` |
| `WORKER_POLL_SECONDS` | `0.25` | worker 空闲轮询间隔 |
| `WORKER_HEARTBEAT_SECONDS` | `2` | 活跃 job 心跳间隔 |
| `WORKER_STALE_SECONDS` | `30` | 无心跳后可安全重排的租约期限 |
| `MAX_UPLOAD_MB` | `25` | 单文件上限 |
| `MAX_WEB_MB` | `5` | URL 页面下载上限 |
| `MAX_REPO_FILES` | `2000` | 仓库可索引文件上限 |
| `MAX_GITHUB_ISSUES` | `1000` | 单仓库分页同步 Issue 上限 |
| `MAX_AGENT_REWRITES` | `2` | 查询重写上限 |
| `TOKEN_BUDGET` | `6000` | 单次生成总预算近似值 |
| `CITATION_REPAIR_ENABLED` | `true` | 是否清理无效引用并对缺失引用执行一次修复 |
| `CITATION_SUPPORT_THRESHOLD` | `0.18` | claim/evidence 支持分阈值 |
| `WEB_SEARCH_ENABLED` | `false` | 本地证据不足后允许外部搜索 |
| `WEB_SEARCH_ENDPOINT` | Brave Search API | 搜索 JSON API |
| `WEB_SEARCH_API_KEY` | 空 | 搜索服务密钥，不得提交 |
| `INPUT_COST_PER_MILLION_TOKENS` | `0` | 输入 Token 成本估算单价 |
| `OUTPUT_COST_PER_MILLION_TOKENS` | `0` | 输出 Token 成本估算单价 |

`GITHUB_TOKEN` 不带 InfraResearch 前缀，用于公开 Issue REST API。不要提交真实
`.env`。修改 Embedding 后端、模型、维度或 chunk 配置会强制重建本地向量
collection。首次启用 FastEmbed Reranker 会下载约 1GB 的模型；生产或离线演示
应预先缓存并记录模型 revision。`GET /api/v1/health` 显示 API 读取到的配置；
worker 是否发生词法、Reranker 或 extractive 降级，应以完成运行的
`metrics.vector_backend/reranker_status/provider` 为准。

OCR 依赖是可选项：`uv sync --project backend --extra ocr`，同时需要系统安装
Tesseract 及所配置语言包。OCR 不可用时保留普通文本提取结果并继续导入。

`start-vllm.sh` 还读取不属于应用 Settings 的启动变量，例如
`INFRARESEARCH_VLLM_MODEL_PATH`、`INFRARESEARCH_VLLM_MAX_MODEL_LEN`、
`INFRARESEARCH_VLLM_GPU_MEMORY_UTILIZATION` 与
`INFRARESEARCH_VLLM_PREFIX_CACHING=1|0`；完整示例见 [GPU/WSL 指南](gpu-wsl.md)。
