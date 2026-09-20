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

# PDF 解析管线

扫描版 PDF 在文本层为空或字符过少时触发 OCR fallback，并记录 OCR 引擎。布局解析
保留 page、bbox、标题、段落、表格、Caption 和 reading order；双栏页面按左栏再右栏
恢复阅读顺序。Section-aware chunking 按章节、小节和段落切分，同时保留 section metadata。

# 混合检索

Hybrid Retrieval 独立运行 Dense Retrieval 与 BM25 词法召回，再用 weighted RRF 融合。
RRF 根据名次而不是不可比较的原始分数融合，并保留 dense score、lexical score 和最终
fusion score。Dense 后端失效时系统降级到 BM25，而不是让整个研究任务失败。

# 检索评测指标

Recall@1/3/5/10 衡量相关证据是否出现在不同深度；MRR 关注第一个相关结果的倒数排名；
nDCG 同时考虑相关性等级和排名位置。检索评测与最终答案评测分开，避免生成模型掩盖
召回问题。困难查询覆盖跨文档、多跳、模糊表达、精确术语、代码定位和 Issue 联合检索。

# 引用忠实度

Citation Precision 检查已生成的引用中有多少有效，Citation Recall 检查所需证据有多少
被引用。Faithfulness 还要求每个 claim 的词义内容由对应 Evidence 支持；仅仅使用已登记
的 `[S1]` marker 并不足够。无支持 claim 会触发一次 citation repair 或被标记为无效。

# Agent 消融与提前停止

Agent 决策消融比较 Naive 单次检索、固定二次检索和 Agentic 自适应检索。Agentic 模式
首次证据足够时 Early Stop，证据不足才 Query Rewrite。实验记录 rewrite 触发率、成功率、
质量增益、额外 token 和延迟，防止无效 Tool Call 只增加成本而不提升质量。

# Reranker 消融

Reranker 实验分别改变 candidate_k、evidence_k 和 reranker 模型，报告质量与延迟曲线。
召回分数与 rerank score 分开保存；模型超时或加载失败时回退原始排序并记录 degraded。

# 工具可靠性与 MCP

每个 Tool Call 在执行前通过 Pydantic JSON Schema 校验，并设置结果上限、超时、有限重试
和结构化错误分类。参数错误不可重试，连接超时可以有限重试。MCP Resources 可通过 URI
读取完整 Source、Repo File、Chunk 和 Evidence，而 Agent 本地调用相同 Tool Registry。

# Trace 与成本

Agent Trace 持久化 Plan、Tool、Observation、Decision、Rewrite、Generation 和 Citation
Verification。成本指标分别记录 prompt token、completion token、retrieval context token
和单任务估算成本，前端刷新后仍可按 sequence 恢复完整轨迹。

# 推理与压力实验

真实评测使用 Qwen/vLLM，而确定性 provider 仅验证离线管线。模型对比关注 Tool Selection、
Rewrite 和 Citation 表现。Prefix Cache 实验测量重复 Research Query 的 TTFT 和吞吐；并发
压测覆盖 1、4、8、16 并发下的 P50、P95、吞吐和任务成功率。

# 长文档、故障与增量索引

长文档压测覆盖 100 页以上 PDF、大型 GitHub Repo 和数百 Issues。Worker 故障注入在执行中
kill worker，并验证 lease recovery、幂等和任务一致性。Qdrant、Embedding、LLM 或 MCP
不可用时分别验证 fallback。文件 hash、Git commit 和 Issue 更新时间驱动重复导入检测与
增量索引；GitHub 同步只重新索引变更文件。

# 结构化源码与 Web

代码 Symbol 索引为函数、类和方法建立 locator，Markdown 按 heading、代码按 class/function
切分。Web Search 只在本地证据不足时可选触发，URL 页面导入后也生成 Evidence，并执行相同
Citation Verification，外部搜索不能绕过引用校验。

# Worker 与规模化

Worker 用 lease 和 heartbeat 表示任务所有权；幂等阶段避免重复副作用，Cancellation 使用
协作式 checkpoint，Crash Recovery 会重新领取过期 lease。扩展到百万级文档时，应将元数据、
对象存储、分布式向量数据库、检索服务和任务队列拆分，并通过分片、缓存和可观测性水平扩展。
