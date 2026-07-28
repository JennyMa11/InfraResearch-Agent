# ADR 0003：OpenAI-compatible Provider

- 状态：Accepted
- 日期：2026-07-28

所有生成通过兼容 Chat Completions 的 provider，使 Qwen vLLM、Ollama 或其他
端点可配置替换。服务不可用时使用确定性抽取 provider，保证 CI 和无 GPU 演示。
降级路径必须写入指标，不能误报成本地 Qwen 结果。
