# InfraResearch v0.2.0 离线消融实验总结

## 实验范围

- 数据集：固定20题，SHA256
  `417a721e1b1a699104c1c466f0ceab0f28ea03b1aec588a064a1078f221dfa29`；
- 语料：`evals/corpus.md`；
- Provider：确定性 extractive；
- 首阶段检索：SQLite 词法检索，`candidate_k=20`；
- Reranker：Identity 或 FastEmbed `BAAI/bge-reranker-base`；
- Cross-Encoder revision：
  `2cfc18c9415c912f9d8155881c133215df768a70`；
- 每个目录中的 `manifest.json` 保存 Git、环境、配置和模型信息，
  `comparison.json` 保存逐题 Evidence、双重分数、工具调用和轨迹。

本报告只说明离线管线表现。它不能替代 FastEmbed E5 + Qdrant + Qwen 的真实模型
实验，也不预设 Agentic 或 Reranker 必须优于基线。

## Naive 与 Agentic

在 Identity、Evidence K=6 配置下：

| 模式 | 正确性 | Citation Precision | Citation Recall | Recall@K | 平均步骤 | 平均工具数 | P50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Naive | 0.901 | 1.000 | 0.975 | 0.975 | 5.00 | 1.00 | 38.4 ms |
| Agentic | 0.901 | 1.000 | 0.975 | 0.975 | 6.20 | 3.25 | 46.1 ms |

结论：该小语料中 Agentic 没有提升质量指标，代价是平均增加1.2个 Agent Step、
2.25次工具调用和约7.7 ms P50。这个结果说明评测管线不会为了展示效果预设
Agentic 必然胜出。

## Evidence K

以下均为 Agentic + Identity Reranker：

| Evidence K | 正确性 | Citation Recall | Recall@K | P50 | 平均 Tokens |
|---:|---:|---:|---:|---:|---:|
| 3 | 0.895 | 0.975 | 0.975 | 41.6 ms | 90.0 |
| 5 | 0.901 | 0.975 | 0.975 | 46.3 ms | 140.5 |
| 6 | 0.901 | 0.975 | 0.975 | 46.1 ms | 153.9 |
| 8 | 0.901 | 0.975 | 0.975 | 47.0 ms | 167.8 |

结论：K=3 少量降低正确性；K=5、6、8 的质量指标相同。当前离线语料下 K=5
使用最少 Token 达到完整质量，但需要真实 Qwen/E5 评测后才能修改产品默认值。

## 无 Reranker 与 Cross-Encoder

以下均为 Agentic、Evidence K=6，并排除模型下载和首次加载的 warmup 时间：

| Reranker | 正确性 | Citation Recall | Recall@K | 平均步骤 | P50 | Rerank耗时 |
|---|---:|---:|---:|---:|---:|---:|
| Identity | 0.901 | 0.975 | 0.975 | 6.20 | 46.1 ms | 0.0 ms |
| BGE Cross-Encoder | 0.898 | 0.975 | 0.975 | 6.40 | 475.7 ms | 451.1 ms |

结论：该实验中 Cross-Encoder 没有提升 Recall@K 或 Citation Recall，正确性下降
0.003，P50 增加约429.6 ms。Reranker 已作为可开关、可降级能力交付，但 README
不宣称它产生了质量提升。

## Citation Repair

Identity、Evidence K=6 下，开启和关闭 Citation Repair 的全部质量指标相同。这是
因为确定性 extractive provider 本身总是生成已登记 marker。该组实验验证了开关
和评测路径，但无法证明修复策略对真实 Qwen 输出的收益；后续需要在保存的真实模型
无效/缺失引用样本上复测。

## 代表性失败与退化案例

### q10：GitHub Issue 无 Token

正确证据在两组中都排第一，但 Cross-Encoder 将性能指标片段提升到第二位，改变了
抽取报告上下文，正确性从0.729降至0.708。说明只要最终上下文包含多个片段，精排
后的非关键证据仍可能稀释答案。

### q14：单工具失败时保留证据

核心 Agent 工作流证据仍排第一，但 Cross-Encoder 把 prefix caching 等非关键片段
移入最终上下文，正确性从0.641降至0.625。说明当前模型对项目内部的中文工程问题
没有稳定增益。

### q19：Qdrant Local 迁移优势

存储与迁移证据保持第一，但性能指标片段被提升，正确性从0.894降至0.872。该案例
再次表明“正确片段仍在 Top-K”不等于生成上下文没有噪声。

### q04：查询改写无效

Agentic 运行触发了2次查询改写，检索轮数从1增加到3，工具调用从1次
增加到9次，但正确性、Citation Recall 和 Recall@K 都保持1.0，延迟从
34.2 ms 增加到67.7 ms。这是查询改写没有改善答案、只增加成本的代表案例。

### Citation Repair 的边界

无有效 Evidence 时，Citation Repair 只能删除 `[S99]` 这类无效 marker，无法把
未受证据支持的内容变成有根据的答案。此边界由
`test_citation_repair_cannot_ground_answer_without_evidence` 固定；产品使用时应把
“无证据”视为不可修复状态，而不是声称引用修复成功。

### Agentic 总体持平

20题固定语料较小，首轮检索已经达到0.975 Recall@K，因此查询重写没有带来质量
收益，只增加步骤与工具调用。后续扩展评测集时应加入首轮召回失败、跨来源组合和
Issue/代码歧义问题，否则无法体现 Replan 的有效边界。

## 已知限制与下一步

1. 当前公开数字来自 extractive provider，不应包装成真实 Qwen 提升。
2. 需要运行 FastEmbed E5 + Qdrant + Qwen 的同配置实验并记录模型 revision。
3. Citation Repair 收益需要包含无效引用的真实生成样本。
4. 数据集需要增加困难负例和跨来源问题，避免 Recall@K 过早饱和。
5. 在新数据证明收益前，产品默认保持 Identity Reranker。
