# API

交互 OpenAPI 位于 `/docs`，机器可读 schema 位于 `/openapi.json`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 版本、向量后端和 Embedding 配置 |
| POST | `/api/v1/sources/files` | multipart 上传文件，返回 202 ingestion |
| POST | `/api/v1/sources/github` | 导入公开 GitHub 仓库 |
| GET | `/api/v1/sources?page=1&page_size=20&q=&status=&kind=` | 分页、搜索和过滤数据源 |
| POST | `/api/v1/sources/{id}/reindex` | 使用原始文件或仓库配置重新索引 |
| DELETE | `/api/v1/sources/{id}` | 移出检索索引，保留历史证据快照 |
| GET | `/api/v1/ingestions/{id}` | 导入进度与错误 |
| POST | `/api/v1/ingestions/{id}/cancel` | 取消 pending/running 导入 |
| POST | `/api/v1/research` | 创建 `naive` 或 `agentic` 运行 |
| GET | `/api/v1/research?page=1&page_size=20&q=&status=&mode=` | 分页、搜索和过滤研究历史 |
| POST | `/api/v1/research/{id}/retry` | 基于原问题创建一次新运行 |
| POST | `/api/v1/research/{id}/cancel` | 取消 pending/running 研究 |
| GET | `/api/v1/research/{id}` | 报告、计划、证据、引用、轨迹和指标 |
| GET | `/api/v1/research/{id}/events` | SSE 实时事件 |
| GET | `/api/v1/metrics/summary` | 聚合 P50/P95、Token 和工具调用 |

研究请求：

```json
{"question": "Prefix caching 如何工作？", "mode": "agentic", "top_k": 6}
```

列表响应统一为 `{"items":[],"page":1,"page_size":20,"total":0,"pages":0}`。
`page_size` 范围为 1–100；来源支持 `status/kind`，研究支持 `status/mode`。

SSE 事件按递增 `sequence` 发送。终止事件为 `run_completed`、`run_failed` 或
`run_cancelled`；
反向代理必须禁用响应缓冲。

错误使用标准 HTTP 状态和 `{"detail":"..."}`：不支持格式为 415，超大文件为
413，不存在为 404，输入错误为 422。异步导入或运行错误保存在资源的 `error`
字段和轨迹中。

API 与 worker 可独立重启。pending 任务保留在 SQLite；running 任务由心跳租约
保护，租约过期后安全重排。取消接口具有幂等的 cancelled 读取语义；已经终止的
非 cancelled 任务返回 409。
