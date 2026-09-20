# MCP 工具服务

InfraResearch 使用官方 MCP Python SDK 2.x 暴露只读检索工具。MCP Server 与本地
Research Agent 复用同一个 Tool Registry，因此参数校验、结果上限、重试、超时和
错误语义保持一致；本地 Agent 不需要通过 MCP 网络调用自身工具。

## 启动

本地客户端推荐 stdio：

```bash
make mcp
```

Streamable HTTP：

```bash
uv run --project backend python -m infraresearch.mcp_server \
  --transport streamable-http --host 127.0.0.1 --port 8002
```

HTTP 端点为 `http://127.0.0.1:8002/mcp`。不要为新客户端使用已经被 Streamable
HTTP 取代的旧 SSE transport。

## Tools

| Tool | 输入 | 说明 |
|---|---|---|
| `semantic_document_search` | `query`, `top_k` | 使用配置的向量或词法后端检索文档与代码 |
| `code_keyword_search` | `query`, `top_k` | 只检索代码 chunk |
| `github_issue_search` | `query`, `top_k` | 检索已导入的 GitHub Issue |

`query` 长度为 1–4000，`top_k` 范围为 1–100。非法输入由 MCP Schema 在工具执行
前拒绝。

## Resources

- `infraresearch://source/{source_id}`：来源元数据；
- `infraresearch://chunk/{chunk_id}`：chunk 内容与稳定定位符；
- `infraresearch://evidence/{run_id}/{evidence_id}`：历史研究的 Evidence 快照与双重分数。

## 验证

```bash
uv run --project backend pytest backend/tests/test_mcp_server.py
```

测试使用官方 MCP Client 对内存中的 MCP Server 执行工具发现、JSON Schema 检查、
正常调用、非法参数和 Resource 读取，不以直接调用 Python 函数代替协议验收。
