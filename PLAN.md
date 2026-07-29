# InfraResearch Agent v0.1.0 实施计划

## 目标

构建一个可离线演示和测试的最小课程原型：React + FastAPI、单 Agent
Agentic RAG、本地技术文档与公开 GitHub 仓库/Issue 检索、OpenAI-compatible
本地模型、可验证引用、SSE 执行轨迹，以及 20 题 Naive RAG / Agentic RAG
对比评测。

## v0.1.0 范围

- 文件数据源：PDF、Markdown、文本和常见代码文件。
- GitHub 数据源：公开仓库的固定 commit 浅克隆，以及可选 Issue API。
- 持久化：SQLite 保存业务数据；Qdrant Local 保存向量。Qdrant 不可用时使用
  SQLite 词法检索降级，且在 API 中明确暴露降级状态。
- 工作流：Router → Planner → Retriever → Evidence Grader → Generator →
  Citation Verifier。证据不足时最多重写两次，引用最多修复一次。
- 基线：Naive RAG 与 Agentic RAG 使用同一批 chunk 和生成 provider。
- 推理：默认 `Qwen/Qwen3-0.6B`，通过 OpenAI-compatible API 访问；未启动模型
  服务时使用确定性 extractive provider，保证演示和 CI 不依赖 GPU。
- 界面：数据源、研究问题、计划、时间线、工具调用、证据引用和性能指标。
- 评测：版本控制内包含 20 题数据集，并生成 JSON、CSV、Markdown 报告。

## 约束与安全

- 只接受 `github.com` 的公开 HTTPS 仓库。
- 限制上传大小、仓库文件数、chunk 数、Agent 重试和 token 预算。
- 忽略秘密文件、二进制、模型权重、`.git` 和构建产物。
- 所有引用必须绑定已登记 evidence ID 和稳定定位符；验证失败不得伪造。
- 单个工具失败会写入轨迹，保留已有证据并继续执行。

## API

- `POST /api/v1/sources/files`
- `POST /api/v1/sources/github`
- `GET /api/v1/sources`
- `GET /api/v1/ingestions/{id}`
- `POST /api/v1/research`
- `GET /api/v1/research/{run_id}/events`
- `GET /api/v1/research/{run_id}`
- `GET /api/v1/metrics/summary`

## 验收

1. 示例文件和本地模拟 GitHub 仓库可导入。
2. Naive 和 Agentic 模式均生成含有效定位符的报告。
3. SSE 顺序、重试上限、引用验证和错误降级有自动化测试。
4. React 构建、TypeScript 检查、Python 测试和静态检查通过。
5. `scripts/evaluate.py` 一条命令生成双基线对比报告。
6. 文档、CHANGELOG、ADR、示例配置及 `v0.1.0` 标签齐全。

## 后续版本

v0.2.0+ 考虑 MCP Server、Reranker、知识图谱、多 Agent、Web Search、OCR、
Speculative Decoding、认证和细粒度权限。

## v0.1.1 工程收口

- SQLite 持久任务队列与独立 worker 进程，使用原子 claim、心跳租约和安全重排。
- 导入/研究任务 cooperative cancellation，并持久化 `cancel_requested/cancelled`。
- 来源和研究历史分页、状态过滤与文本搜索。
- worker 并发 claim、过期租约恢复、旧任务接管和任务幂等性阶段测试。
