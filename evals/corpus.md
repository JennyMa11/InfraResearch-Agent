# Prefix caching

Prefix caching 会复用具有相同提示前缀的 KV cache block，从而减少重复 prefill。
缓存只复用确定性的前缀 KV 计算；它不修改模型权重、后续采样算法，也不改变输出。
KV cache usage 表示可用 KV block 中当前已占用部分的比例。

# 性能指标

TTFT 从请求开始计时，到流式响应收到首个 token 为止。端到端延迟从请求开始持续
到最终事件。SSE 成功结束事件是 `run_completed`，失败结束事件是 `run_failed`。

# 公平基线

Naive RAG 与 Agentic RAG 使用相同索引、Embedding 和生成模型以控制变量并保证
对比公平。效果依赖问题和语料，因此不预设 Agentic RAG 一定更好；评测目标是
产生可复现、可解释的真实对比，而不是预设结论。

# Agent 工作流

Evidence Grader 评价证据相关性、问题覆盖度和来源多样性。证据不足时 Agent 重写
查询并重新检索，最多重试两次。引用验证失败最多修复一次；仍不可靠时移除无效
引用并明确标注“证据不足”。单个工具失败会记录在轨迹中，已有证据仍用于生成
透明的部分答案。

# 稳定定位符

PDF 证据使用文件名和页码。代码证据包含仓库、commit SHA、文件路径和起止行号。
仓库固定到 commit SHA 可让证据与评测可重现，并避免分支移动导致定位失效。

# GitHub 数据源

没有 GitHub Token 时仓库代码仍可索引，但 Issue API 具有更低的速率限制并显示
提示。索引器忽略 `.git`、构建目录、二进制、模型权重和秘密文件。

# 本地推理

默认模型为 `Qwen/Qwen3-1.7B`，通过 OpenAI-compatible API 调用。GPU 不可用时
可以切换 Ollama 或其他兼容端点；完全离线演示使用确定性抽取 provider。

# 存储与路线

Qdrant Local 与 Qdrant 服务端使用相同客户端 API，后续迁移不需要修改业务检索
接口。MCP 推迟到 v0.2.0：首版先稳定 Retriever 和 Tool 接口并保持原型最小，
后续再将接口封装成 MCP tools 和 resources。
