# 测试指南

```bash
make test
make check
make build
```

后端单元测试覆盖 Markdown/代码分块、稳定行号、Retriever、GitHub URL 限制、
grader 重写上限、Naive 单轮检索、引用修复、token 预算和百分位。集成行为通过
临时 SQLite、固定 provider 与本地语料运行，不需要网络或 GPU。
测试还覆盖 Reranker 降级、Tool Schema/重试、SQLite 分数迁移，以及使用官方
MCP Client 的工具发现、调用与 Resource 读取。

worker 阶段测试覆盖多 worker 原子 claim、持久任务单次执行、旧进程内任务接管、
过期心跳租约安全重排，以及运行中 cooperative cancellation。API 集成测试还覆盖
pending 取消与来源/研究历史的分页搜索。

前端 Vitest 覆盖数据源错误、轨迹、引用点击和证据预览。Playwright 冒烟：

```bash
npm --prefix frontend run test:e2e
```

GPU 测试不进入普通 CI；使用下文的 `make verify-gpu` 单独运行。它验证模型健康、
流式首 token、长上下文、prefix cache 和 `/metrics` 抓取。
真实 OCR 探针可用 `make verify-ocr` 运行；脚本会创建无文本层的 image-only PDF，
并校验 Tesseract 输出、页码和 bbox。

## 分阶段验收

完整的离线验收使用临时数据库、随机端口和独立 Qdrant Local 目录，不会污染
`data/`：

```bash
make verify
```

它依次执行预检、Python/TypeScript 静态检查、后端/前端测试、生产构建、
Playwright、独立 API/worker 的真实 HTTP 全流程和 50 题三策略评测。也可以只运行一个阶段：

```bash
./scripts/verify.sh quality
./scripts/verify.sh unit
./scripts/verify.sh api
./scripts/verify.sh browser
./scripts/verify.sh evaluation
```

API 阶段覆盖 OpenAPI 契约、404/415/422 错误、Markdown 与代码上传、异步导入、
数据源元数据、Naive/Agentic/闲聊三条路径、检索工具、证据和引用、SSE 顺序及
聚合指标。测试组织与新增任务类型的最低覆盖要求见[开发指南](development.md)。

GPU/vLLM 和真实 GitHub 属于环境相关阶段，需要对应服务或网络：

```bash
make verify-gpu
make verify-online
make evaluate-gpu
```

GPU 阶段检查 `nvidia-smi`、模型注册、流式首 token、重复前缀的 cache 指标和长
上下文请求。`make evaluate-gpu` 进一步使用实际 Qwen、Qdrant 和
`multilingual-e5-small` 完成 50 题三策略评测。可用 `--skip-long-context`
单独运行较短探针：

```bash
uv run --project backend python scripts/verify_gpu.py --skip-long-context
```

Prefix Cache 开/关需要重启同一 vLLM，并分别运行：

```bash
uv run --project backend python scripts/benchmark_runtime.py \
  --prefix-cache-mode on --output evals/results/runtime-prefix-on.json
uv run --project backend python scripts/benchmark_runtime.py \
  --prefix-cache-mode off --output evals/results/runtime-prefix-off.json
uv run --project backend python scripts/compare_runtime.py \
  --off evals/results/runtime-prefix-off.json \
  --on evals/results/runtime-prefix-on.json \
  --output evals/results/runtime-prefix-comparison.json
```

在线阶段默认导入包含 Markdown 的 `octocat/Spoon-Knife` 并实际请求 GitHub
Issues API；可用环境变量替换仓库或关闭 Issues：

```bash
INFRARESEARCH_ACCEPTANCE_GITHUB_URL=https://github.com/owner/repo make verify-online
INFRARESEARCH_ACCEPTANCE_GITHUB_ISSUES=0 make verify-online
```

GitHub 未认证 REST API 有共享出口 IP 限额；收到 `remaining=0` 时应配置
`GITHUB_TOKEN`，或等待响应头中的 reset 时间后重试。测试不会把限流误判为通过。
