# InfraResearch 面试与简历指南

本文只引用当前可复现结果。真实 50 题报告见
`evals/results/roadmap-50q-qwen3-{0.6b,1.7b}-full-v2/`，缓存对照与长文档结果分别见
`evals/results/runtime-prefix-comparison.json`、`evals/results/stress-ingestion.json`。

## 30 秒介绍

InfraResearch 是面向技术文档、源码和 Issue 的单 Agent RAG 工作台。我实现了
OCR/layout/符号级 ingestion、Dense+BM25/RRF 两阶段检索、可降级 Reranker、证据
充分性判断、查询改写、claim 级引用校验和完整持久 Trace；同一 Tool Registry 同时
服务本地 Agent 与 MCP。系统用 SQLite lease worker 承载长任务，支持取消、SIGKILL
恢复和增量索引。项目不只展示功能：固定 50 题分别报告 Retrieval 与端到端指标，
并保存所有失败样本。

## 可直接改写的简历 Bullet

- 设计并实现可解释单 Agent RAG：统一文档、代码、Issue 与可选 Web Search 工具，
  持久化 Plan → Tool → Observation → Decision → Citation Trace；Qwen3-1.7B 真实
  50 题实验中 Agentic 原始正确性为 0.837、Citation Recall 为 0.680、拒答准确率
  为 0.940，并用配对区间明确说明相对 Naive 的提升未达统计显著。
- 实现 PDF OCR fallback、双栏 reading order/page/bbox、Markdown heading 与代码
  class/function symbol chunking；通过文件 hash、Git commit、Issue `updated_at` 做增量
  更新，并用 120 页 PDF、1,000 文件 Repo、500 Issues 的 2,620-chunk 合成夹具验证。
- 构建 Hash/E5 Dense + BM25 + weighted RRF 候选召回和可选 Cross-Encoder 精排，保留
  dense/lexical/retrieval/rerank 分数；真实 FastEmbed 50 题 Retrieval Benchmark 达到
  Recall@10 0.890、MRR 0.885、nDCG@10 0.978，并如实记录旧 Reranker 实验的退化。
- 构建 SQLite 持久任务队列的 CAS claim、heartbeat lease、协作取消和 crash recovery；
  实际 SIGKILL 测试中替代 Worker 在 attempt 2 恢复并一致写入 2,143 chunks；真实
  Qwen 1/4/8/16 并发最终成功率均为 100%，Prefix Cache 使热态 TTFT 降低 38.5%。

## 高频设计问题

### 为什么需要 Agent，而不是一次 RAG？

一次 RAG 无法根据 evidence type 选择代码/Issue 工具，也无法在证据不足时决定改写、
拒答或使用 Web Search。这里的 Agent 是有界状态机，不是开放式自治系统：决策有限、
步骤可追踪、每次外部调用有 Schema 和错误边界。是否值得增加 Agent，要由消融决定。

### 为什么固定二次检索没有提升？

真实实验中固定二次检索相对 Naive：0.6B 持平（0.717），1.7B 略低（0.793 对
0.803）。原因是无条件第二轮重复了已充分问题，还可能把改写噪声带入候选。Agentic
的 Early Stop 避免部分浪费；领域化 Rewrite 已从离线零成功提升到 3/20 次，但真实
模型实验仍只有 2/22 次，下一步应按失败类型学习改写并以增量收益作为停止条件。

### 为什么 Reranker 没提升？如何改？

旧 20 题语料首轮 Recall 已接近饱和，Cross-Encoder 没有新增候选，只能重排；当
candidate set 缺少答案时更不可能修复召回。模型还可能与中英混合基础设施语料不匹配。
改进顺序是：先扩大困难集和 candidate diversity，再扫 candidate_k/evidence_k，比较
多语言 reranker，并画质量/延迟曲线，而不是只比较一个点。

### MCP 与 Function Calling 有什么区别？

Function Calling 通常是某个模型 API 的调用描述与返回协议；MCP 进一步标准化工具发现、
Resources URI、transport 和跨客户端复用。本项目内部 Agent 不经过 MCP 网络，而是直接
调用同一个 Tool Registry；MCP 只是适配层，因此不会把核心可靠性绑定到额外服务。

### Qdrant 与 BM25、Dense 与 Sparse 如何取舍？

Dense 擅长语义改写和跨语言，BM25 擅长函数名、错误码、配置键等精确术语。Qdrant
保存 Dense 向量并支持未来服务化，SQLite-owned chunk 是事实来源；BM25 是独立通道和
Qdrant/Embedding 故障降级。两类分数不可直接相加，所以使用 weighted RRF 按名次融合，
并保留通道分数用于诊断。

### Reranker 的原理是什么？

Bi-encoder 分别编码 query/document，适合大规模候选召回；Cross-Encoder 联合编码
query-document pair，交互更充分但成本与候选数线性增长。系统先取 candidate_k，再由
Reranker 排到 evidence_k；超时/加载失败回退 retrieval order，并记录 degraded。

## 指标问题

- **Recall@K**：至少一个或一定比例相关证据是否进入前 K；适合衡量召回覆盖。
- **MRR**：第一个相关结果排名的倒数均值；适合只有一个首要答案的搜索体验。
- **nDCG@K**：按位置折损的 graded relevance，并用理想排序归一化；适合多个证据且
  相关程度不同的场景。
- **Citation Precision**：生成引用中有效且支持 claim 的比例；低说明乱引或错引。
- **Citation Recall**：回答所需证据中实际被引用的比例；低说明有结论没有覆盖依据。
- **Faithfulness**：每个 claim 是否被它指向的 Evidence 支持。本项目当前是可复现的
  token-overlap verifier，不应表述成 NLI 级语义蕴含或“完全消除幻觉”。

## Worker 可靠性问题

- **Lease/Heartbeat**：claim 写入 worker、claimed_at、heartbeat_at；超过 stale deadline
  才允许恢复，避免活 Worker 的任务被重复执行。
- **幂等**：Job 用 target ID 唯一约束；恢复研究任务前清理 partial trace/evidence/tool
  records，文件与仓库用 hash/commit 复用未变化 chunks。
- **Cancellation**：API 将状态置为 cancel_requested；Worker 在子进程、遍历、chunk、
  tool 和生成边界 checkpoint，安全停止并持久化 cancelled。
- **Crash Recovery**：启动时扫描过期 lease，重置 target 后重新入 pending。实际 SIGKILL
  暴露过 SQLite lock 误判问题；修复后 checkpoint 对瞬时 lock 延后重试，attempt 2 完成。

## 扩展到百万级文档

1. SQLite 拆为高可用元数据数据库，原文进入对象存储；chunk/version 使用内容寻址。
2. Qdrant Local 换集群，按 tenant/corpus 分片并建立独立 ingestion/retrieval 服务。
3. SQLite Job 换支持 visibility timeout、DLQ 和弹性 consumer 的队列；所有阶段使用
   idempotency key 和 outbox，避免数据库与向量索引双写不一致。
4. BM25 迁到专用倒排引擎，Dense/Sparse 并行召回，RRF 与 Reranker 独立扩缩容。
5. 建立按 query type、模型、索引 revision 的在线质量/成本/延迟监控，分层缓存 query、
   embedding、retrieval 和 prefix KV；用 canary 与离线回放控制升级风险。

## 必须主动说明的限制

- 真实模型实验是单台 RTX 3060 Laptop、temperature=0 的单次运行；bootstrap CI
  反映题目采样不确定性，不是多随机种子的生成方差。
- OCR 已用真实 image-only PDF 和 Tesseract 验收，但语言只覆盖英文测试页。
- Compose 配置已通过官方 Compose 校验；当前 WSL 没有 Docker socket，容器级启动
  仍需在启用 Docker Desktop WSL 集成的宿主环境完成。
- claim 支持检查是确定性 lexical verifier，不是训练过的 NLI judge。
