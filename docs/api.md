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
{
  "question": "Prefix caching 如何工作？",
  "mode": "agentic",
  "candidate_k": 20,
  "evidence_k": 6
}
```

`candidate_k` 控制各工具的候选召回数，`evidence_k` 控制精排后登记的 Evidence
数量。旧客户端仍可发送 `top_k`，它作为 `evidence_k` 的兼容默认值；
`candidate_k` 不得小于最终 Evidence 数量。

列表响应统一为 `{"items":[],"page":1,"page_size":20,"total":0,"pages":0}`。
`page_size` 范围为 1–100；来源支持 `status/kind`，研究支持 `status/mode`。

SSE 事件按递增 `sequence` 发送。Agent 研究路径包含 `plan_created`、
`tool_started`、`observation_created`、`rerank_completed`、`evidence_graded`、
`decision_made`、`query_rewritten` 和引用验证节点。终止事件为 `run_completed`、`run_failed` 或
`run_cancelled`；
反向代理必须禁用响应缓冲。

错误使用标准 HTTP 状态和 `{"detail":"..."}`：不支持格式为 415，超大文件为
413，不存在为 404，输入错误为 422。异步导入或运行错误保存在资源的 `error`
字段和轨迹中。

API 与 worker 可独立重启。pending 任务保留在 SQLite；running 任务由心跳租约
保护，租约过期后安全重排。取消接口具有幂等的 cancelled 读取语义；已经终止的
非 cancelled 任务返回 409。
