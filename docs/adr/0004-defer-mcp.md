# ADR 0004：MCP 延后到 v0.2.0

- 状态：Accepted
- 日期：2026-07-28

首版先验证 Retriever 和 Tool 的输入输出、错误与可观测性。过早加入 MCP 会扩大
部署和权限面，不能提高本课程的核心对比价值。稳定接口后再映射为 MCP tools、
resources 和 prompts，避免协议层绑定内部数据模型。
