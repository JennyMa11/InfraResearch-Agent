# InfraResearch Agent 项目升级 Goal

## 总目标

将当前 InfraResearch 从功能完整的 Agentic RAG 原型，升级为一个具有以下特点的
可展示 Agent 与大模型应用项目：

- 检索效果可以通过固定评测集和真实指标验证；
- 支持“候选召回 → Reranker → Evidence”的两阶段检索；
- Agent 的计划、工具调用、观察、评分、重规划和最终回答可以被清楚解释；
- 文档、代码和 GitHub Issue 检索具有统一工具接口，并可通过 MCP 暴露；
- README、Demo 和简历描述中的结论都能由代码与评测结果复现。

实施时优先提升检索效果和实验可信度，不继续扩张 Worker、分布式存储等基础设施。

## 当前基础

项目已经具备：

- PDF、Markdown、文本、代码、公开 GitHub 仓库和 Issue 导入；
- FastEmbed、Qdrant Local 与 SQLite 词法降级；
- Naive RAG 和有边界的单 Agent 工作流；
- Planner、Retriever、Evidence Grader、Query Rewrite、Generator 和 Citation Verifier；
- 文档页码、代码行号、Git commit 和 Issue 链接等稳定证据定位符；
- SSE Agent 轨迹、证据抽屉、来源管理和研究历史；
- SQLite 持久任务队列、独立 Worker、任务租约、崩溃恢复和协作式取消；
- 固定20题评测集及 JSON、CSV、Markdown 报告；
- 后端、前端、API、Worker、Playwright 和 GPU 验证脚本。

## 实施原则

1. 先保存当前基线，再修改检索算法。
2. 每项优化都必须可以通过开关关闭，以支持消融实验。
3. 不覆盖原始检索分数，分别保存召回分数与精排分数。
4. 不为了展示效果选择性删除失败题目。
5. 所有性能或质量提升必须来自实际评测，禁止编造指标。
6. 核心 Agent 不应强依赖 MCP 网络调用；MCP 作为统一工具接口的适配层。
7. 保持现有离线降级、任务恢复、取消和历史证据能力不退化。

## 里程碑一：冻结并保存当前基线

目标：建立后续优化可以公平比较的实验基准。

- [x] 固定20题数据集、语料、Embedding 模型、生成模型及其 revision。
- [x] 固定硬件、随机种子、`top_k`、Token 预算和 Agent 最大重写次数。
- [x] 在评测报告中记录运行时间、Git commit、模型、硬件和完整配置。
- [x] 运行并保存当前 Naive RAG 基线。
- [x] 运行并保存当前 Agentic RAG 基线。
- [x] 为每道题保存查询、候选证据、最终 Evidence、答案、引用和指标。
- [x] 汇总正确性、Recall@K、Citation Precision/Recall、工具准确率、Agent Steps、
      Token、P50/P95 和端到端延迟。

验收标准：

- 20道题全部产生结果，不隐藏失败案例；
- 汇总指标能够追溯到逐题记录；
- 使用同一配置重复评测时结果基本稳定；
- 基线报告提交到明确的版本目录，不覆盖后续实验。

## 里程碑二：实现两阶段检索与 Reranker

目标：把当前直接选取向量检索结果的流程改造成可评测的候选召回和精排流程。

- [x] 定义统一的 `Reranker` Protocol。
- [x] 实现 `IdentityReranker`，用于关闭精排和运行基线。
- [x] 实现可配置的真实模型 Reranker。
- [x] 将 `top_k` 拆分为 `candidate_k` 和 `evidence_k`。
- [x] 文档、代码和 Issue 工具先分别召回候选，再合并和去重。
- [x] 使用 Reranker 对合并后的候选进行统一精排。
- [x] 在 `SearchHit`、数据库和 API 中分别保存 `retrieval_score` 与
      `rerank_score`。
- [x] 在运行指标中记录 Reranker 名称、模型、执行状态和耗时。
- [x] Reranker 加载或执行失败时回退原始排序，并明确记录降级状态。
- [x] 增加排序、去重、配置开关、超时和降级测试。

建议默认流程：

```text
Document / Code / Issue Retrieval
               ↓
       Candidate Merge & Dedup
               ↓
             Reranker
               ↓
          Top Evidence Selection
               ↓
          Evidence Grader
```

验收标准：

- 可以分别配置候选数量和最终 Evidence 数量；
- 可以通过配置完全关闭 Reranker；
- 精排前后的顺序和分数可追踪；
- Reranker 失败不会导致整个研究任务失败；
- 现有 Naive、Agentic、引用验证和离线模式测试保持通过。

## 里程碑三：完成消融实验

目标：用真实数字说明 Agent Loop、Reranker、Top-K 和引用修复的作用与代价。

- [x] 对比 Naive RAG 与 Agentic RAG。
- [x] 对比无 Reranker 与有 Reranker。
- [x] 对比 `evidence_k = 3 / 5 / 8`。
- [x] 对比 Citation Repair 关闭与开启。
- [x] 记录质量提升以及 Token、步骤和延迟代价。
- [x] 选择3～5个代表性失败案例进行分析。
- [x] 至少覆盖错误召回、错误精排、查询改写偏离和引用修复失败。
- [x] 生成机器可读 JSON/CSV 和面向 README 的 Markdown 汇总表。

建议分组执行，避免一次运行完整组合爆炸：

1. Naive vs Agentic；
2. Agentic 无 Reranker vs Agentic 有 Reranker；
3. 在最佳候选配置上比较不同 Top-K；
4. 在固定检索配置上比较 Citation Repair。

验收标准：

- 每组实验只改变一个主要变量；
- 报告同时展示质量和成本指标；
- 报告包含提升、持平和退化案例；
- README 与简历使用的所有数字均可从报告中复现。

## 里程碑四：增强 Agent Loop 可解释性

目标：让用户能够理解 Agent 为什么调用某个工具、为什么再次检索以及为什么停止。

- [x] 为每次 Tool Call 增加结构化输入信息，包括工具名、query 和 top_k。
- [x] 增加结构化 Observation，包括结果数量、来源、最高分和结果摘要。
- [x] Evidence Grade 显示 relevance、coverage、diversity、阈值和最终结论。
- [x] 查询重写事件记录原查询、新查询和具体改写原因。
- [x] 记录继续检索或结束检索的 Decision 事件。
- [x] 展示 Reranker 前后的排名变化和淘汰原因。
- [x] 将前端轨迹整理为连续时间线。
- [x] 增加事件字段、顺序和最大循环次数测试。

前端目标流程：

```text
Plan
  → Tool Call
  → Observation
  → Evidence Score
  → Decision / Replan
  → Final Answer
  → Citation Verification
```

验收标准：

- 用户不查看后端日志也能理解一次完整运行；
- 第二轮检索必须展示触发原因和查询变化；
- 每个 Tool Call 都有对应 Observation；
- 页面刷新或 SSE 断开恢复后，时间线仍保持完整和有序。

## 里程碑五：统一 Tool 接口并接入 MCP

目标：把现有内部检索能力标准化为可验证、可复用、可对外暴露的工具。

- [x] 定义 `ToolSpec`、`ToolResult` 和统一 Tool Protocol。
- [x] 为文档、代码和 GitHub Issue 检索建立 Pydantic 输入 Schema。
- [x] 建立 Tool Registry，由 Agent 按工具名查找和执行工具。
- [x] 从 `ResearchAgent` 中移除具体检索工具的硬编码分支。
- [x] 统一实现参数校验、超时、最大结果数、取消和结构化错误。
- [x] 为可重试错误设置有限重试策略，不对参数错误盲目重试。
- [x] 保留工具调用审计、耗时、状态和结果摘要。
- [x] 通过 MCP Server 暴露文档、代码和 Issue 检索工具。
- [x] 可选暴露来源、chunk 和 evidence MCP Resources。
- [x] 增加工具发现、参数错误、正常结果、空结果和内部错误集成测试。

推荐结构：

```text
Research Agent
      ↓
 Tool Registry
   ├── Local Direct Invocation
   └── MCP Server Adapter
```

验收标准：

- 本地 Agent 继续使用相同工具实现，不强制经过 MCP 网络层；
- 标准 MCP Client 能发现并调用三个检索工具；
- 工具输入具有明确 JSON Schema；
- 非法输入在执行前被拒绝；
- 所有工具执行都有最大步骤、超时、重试和错误边界。

## 里程碑六：README、Demo 与简历包装

目标：让面试官在几分钟内理解系统价值、Agent 流程和实验效果。

- [x] README 首屏说明系统解决的问题。
- [x] 增加一张简洁 Agent 流程图。
- [x] 增加 Naive、Agentic、Reranker 和 Top-K 实验结果表。
- [x] 明确说明质量提升对应的 Token、步骤和延迟代价。
- [x] 增加一个30～60秒 GIF 或视频。
- [x] Demo 展示导入资料、提交问题、证据不足、查询重写、再次检索和引用核对。
- [x] 更新架构、配置、API、评测与故障排查文档。
- [x] 编写准确的简历项目描述和30秒面试介绍。
- [x] 仅在功能和测试完成后写入 MCP、Reranker 与量化指标。

验收标准：

- 新用户可以根据 README 完成安装和最小演示；
- README 首页能回答“解决什么问题、Agent 怎么运行、效果怎么样”；
- Demo 可以稳定复现，不依赖未记录的手工步骤；
- 简历中没有多 Agent、通用自主 Agent或生产级分布式系统等不准确表述。

## 最终完成标准

只有同时满足以下条件，整个 Goal 才算完成：

- [x] 当前基线和全部消融实验都有可复现报告；
- [x] 两阶段检索和 Reranker 已实现、可关闭、可降级并通过测试；
- [x] Agent 时间线完整展示 Plan、Tool、Observation、Grade、Replan 和 Answer；
- [x] 三类检索工具通过统一 Tool Registry 调用；
- [x] MCP Client 可以发现并调用这些工具；
- [x] 完整离线验收、前后端测试、类型检查和生产构建通过；
- [x] README、Demo 和简历描述只引用已验证功能及真实指标；
- [x] 已记录已知限制、失败案例和后续工作。

## 非目标

本轮升级不实现：

- 多 Agent 或 Supervisor；
- 知识图谱；
- OCR；
- Web Search；
- 分布式 Qdrant；
- 新的分布式任务队列；
- 用户认证、多租户或细粒度权限；
- 与上述目标无关的 Worker 基础设施扩张。

## 建议执行顺序

```text
基线评测
  → Reranker
  → 消融实验
  → 可解释 Agent Loop
  → Tool Registry
  → MCP
  → README / Demo / 简历
```

## 可直接使用的 Goal 描述

> 将 InfraResearch 升级为一个可量化、可解释、工具标准化的技术研究 Agent。
> 首先冻结20题 Naive RAG 与 Agentic RAG 基线；随后实现可开关、可降级的两阶段
> 检索和 Reranker，保留原始召回分数与精排分数；扩展评测以比较 Naive/Agentic、
> 有无 Reranker、不同 Top-K 和 Citation Repair，并保存逐题结果、失败案例、质量与
> 成本指标。然后完善 Plan → Tool Call → Observation → Evidence Score → Replan →
> Final Answer 的结构化事件和前端时间线。最后将文档、代码、GitHub Issue 检索抽象
> 为带参数校验、超时、有限重试、取消和错误边界的统一 Tool Registry，并通过 MCP
> Server 暴露。完成后更新测试、README、Demo 和简历材料。所有结论必须有可复现
> 评测支持；不得扩展到多 Agent、知识图谱、OCR、Web Search、分布式基础设施或
> 用户权限系统。

## 完成记录

本 Goal 于 2026-09-20 完成并实际验收：

- 升级前基线保存在 `evals/results/pre-upgrade-baseline/`；
- 六组可复现消融保存在 `evals/results/benchmarks/*-v2/`，汇总和失败
  案例见 `evals/results/benchmarks/summary.md`；
- FastEmbed Cross-Encoder 实际模型 revision 为
  `2cfc18c9415c912f9d8155881c133215df768a70`；
- `make verify` 通过：46 个后端测试、9 个前端测试、4 个 Playwright 场景、
  隔离 API/worker 工作流、20题 Naive/Agentic 双基线、类型检查与生产构建；
- 约67秒演示由 `scripts/record-demo.sh` 生成到 `docs/assets/agent-demo.gif`；
- 当前离线集上 Reranker 没有带来质量收益，Agentic 也未优于 Naive；
  README 与简历材料已明确保留这些限制，未将退化结果包装为提升。
