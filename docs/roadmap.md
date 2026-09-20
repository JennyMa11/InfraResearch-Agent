# 路线图

## v0.1.x

- 提升 PDF 表格与代码符号分块。
- 增加 OpenAPI 自动生成 TypeScript client。
- 收集更多 GPU 评测配置并优化 Qwen prompt。
- 增加软删除保留期限、垃圾回收和 SQLite 外键迁移。
- 扩展 worker 多进程压力、强制终止和长时生成取消验收。

已在 v0.1.1 完成：独立 SQLite 持久 worker、任务取消、来源/研究历史分页过滤与
搜索、worker 租约恢复和旧任务接管。

## v0.2.0

已完成：

- Retriever/Tool Registry 与 MCP tools/resources；
- 可选 FastEmbed Cross-Encoder Reranker；
- 可复现消融评测与可解释 Agent 决策时间线。

延后：OCR、Web Search、多 Agent supervisor、用户认证、配额和数据源权限。

## 更长期

知识图谱、PR 检索、Speculative Decoding、分布式 Qdrant 和团队级评测平台。路线
图不是承诺；每项进入版本前都需要 ADR、资源预算和可复现验收。
