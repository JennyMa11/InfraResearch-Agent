# Roadmap 实施与验收状态

更新日期：2026-09-20。`完成` 表示代码和当前机器上的证据均存在；`待外部环境`
表示实现/命令已具备，但缺少本机不可提供的服务或系统依赖，不能声称实验完成。

| Roadmap 项 | 状态 | 权威证据 |
|---|---|---|
| PDF OCR fallback | 完成 | 真实 image-only PDF 经 PyMuPDF + Tesseract，见 `ocr-real.json` |
| PDF layout / 双栏 / title / table / caption / order | 完成 | `test_chunking.py` layout tests |
| Section-aware chunk + page/bbox metadata | 完成 | chunk/ingestion metadata tests |
| Dense + BM25 + weighted RRF | 完成 | `retrieval.py`、50 题 Hybrid 报告 |
| Recall@1/3/5/10、MRR、nDCG | 完成 | `roadmap-50q-hybrid/comparison.json` |
| 50–100 题与困难类型 | 完成（50 题） | `evals/questions.json` |
| 多跳/模糊/术语/代码/Issue 类型 | 完成 | question `category` 与分类汇总 |
| 证据不足拒答 | 完成 | q46–q50、abstention accuracy |
| Citation Precision/Faithfulness | 完成（lexical verifier） | citation support score、修复测试 |
| Query Rewrite 触发/成功/增益/成本 | 完成 | 领域化改写；离线 3/20、真实模型 2/22 次成功 |
| Naive/固定二次/Agentic 消融 | 完成 | 50 题三策略表 |
| Reranker candidate/evidence/model 消融 | 完成（历史 20 题） | `evals/results/benchmarks/` |
| Evidence Grader / Early Stop | 完成 | informative-token grader、Trace tests |
| Tool Schema/timeout/retry/error class | 完成 | Tool Registry tests、persisted error fields |
| MCP Evidence/Source/Repo File/Issue | 完成 | MCP resource tests与 `mcp_server.py` |
| Agent Trace 持久化 | 完成 | ordered TraceEvent 与 SSE recovery tests |
| Prompt/completion/context token 与成本 | 完成 | RunMetrics fields、50 题 details |
| 真实 Qwen/vLLM 完整评测 | 完成 | RTX 3060、0.6B/1.7B、各 50 题 × 3 模式 |
| 不同真实模型比较 | 完成 | 同数据/检索/温度；1.7B 三模式均高于 0.6B |
| Prefix Cache TTFT/吞吐实验 | 完成 | On/Off 热态 TTFT 49.7/80.9 ms，命中增量 2,688 |
| 1/4/8/16 并发压测 | 完成（offline + GPU） | 两组最终成功率均 1.0；保留传输重试计数 |
| 100+ 页/大 Repo/数百 Issues | 完成（synthetic） | `stress-ingestion.json` |
| Worker kill/lease recovery | 完成 | 实际 SIGKILL，attempt 2，2,143 chunks |
| Qdrant/Embedding/LLM/MCP 降级 | 完成 | retrieval/provider/tool/MCP failure tests |
| 重复导入与更新检测 | 完成 | file hash、commit、Issue timestamp tests |
| GitHub 变更文件增量同步 | 完成 | changed/deleted/unchanged identity test |
| 函数/类/方法 Symbol locator | 完成 | AST symbol chunk test |
| Markdown/代码结构化 Chunk | 完成 | heading hierarchy与 class/function tests |
| 可选 Web Search | 完成（API mock 验收） | opt-in tool、SSRF URL filtering tests |
| URL/Web Page 导入 | 完成（API mock 验收） | URL ingestion与 HTML reader tests |
| Web Evidence + Citation Verification | 完成 | Web result 持久为 Source/Chunk 后走统一 verifier |
| README diagrams / real tables / cases | 完成 | `README.md` |
| Docker Compose / make demo | 部分完成 | `make demo` 实跑；Compose v5.5.1 config 通过，WSL 无 Docker socket |
| 固定离线 Demo Dataset | 完成 | `evals/corpus.md`、`scripts/demo.sh` |
| 1–2 分钟演示 | 完成 | `agent-demo.gif`，ffprobe 66.75 秒 |
| 发布前密钥/大文件检查 | 完成 | secret pattern scan无命中；依赖与 data 均 ignore |
| Benchmark 可复现命令 | 完成 | Makefile、evaluation docs |
| 3–4 条真实简历 Bullet | 完成 | `docs/interview-guide.md` |
| 面试问题与百万级演进 | 完成 | `docs/interview-guide.md` |

## 外部环境剩余验收门

仅剩容器实际启动：当前 WSL 没有 `/var/run/docker.sock`，因此无法执行
`make docker-demo`。官方 Docker Compose v5.5.1 已验证 `docker-compose.yml config`
通过；在 Windows Docker Desktop 中启用该 WSL 发行版集成后，运行：

```bash
docker compose config
make docker-demo
curl --fail http://127.0.0.1:8000/api/v1/health
```

这属于宿主环境阻塞，不是仓库内代码或配置缺口。在实际容器健康检查通过前，发布说明
只声明“Compose 配置验收通过”，不声明“容器运行验收通过”。
