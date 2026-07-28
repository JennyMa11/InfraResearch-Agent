# API

交互 OpenAPI 位于 `/docs`，机器可读 schema 位于 `/openapi.json`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 版本、向量后端和 Embedding 配置 |
| POST | `/api/v1/sources/files` | multipart 上传文件，返回 202 ingestion |
| POST | `/api/v1/sources/github` | 导入公开 GitHub 仓库 |
| GET | `/api/v1/sources` | 数据源状态 |
| GET | `/api/v1/ingestions/{id}` | 导入进度与错误 |
| POST | `/api/v1/research` | 创建 `naive` 或 `agentic` 运行 |
| GET | `/api/v1/research/{id}` | 报告、计划、证据、引用、轨迹和指标 |
| GET | `/api/v1/research/{id}/events` | SSE 实时事件 |
| GET | `/api/v1/metrics/summary` | 聚合 P50/P95、Token 和工具调用 |

研究请求：

```json
{"question": "Prefix caching 如何工作？", "mode": "agentic", "top_k": 6}
```

SSE 事件按递增 `sequence` 发送。终止事件为 `run_completed` 或 `run_failed`；
反向代理必须禁用响应缓冲。

错误使用标准 HTTP 状态和 `{"detail":"..."}`：不支持格式为 415，超大文件为
413，不存在为 404，输入错误为 422。异步导入或运行错误保存在资源的 `error`
字段和轨迹中。
