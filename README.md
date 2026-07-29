# InfraResearch Agent

面向基础设施与系统软件资料的可解释 Agentic RAG 课程原型。它可以索引本地
PDF/Markdown/代码或公开 GitHub 仓库，并以稳定的页码、行号和 Issue 链接生成
可验证的技术报告。默认使用 FastEmbed 与 `multilingual-e5-small` 生成真实语义
向量；无网络的最小演示可显式选择哈希向量回退。

工作台支持来源状态持续刷新、重新索引和删除，也可以恢复最近研究记录并重试
因服务重启或外部错误失败的任务。导入和研究由 SQLite 持久队列中的独立 worker
执行，支持取消、崩溃恢复，以及来源/历史分页搜索。

## 快速开始

要求 Python 3.12、Node.js 20+。GPU 不是基础演示的必需条件。

```bash
cp .env.example .env
make install
make dev
```

打开 <http://localhost:5173>。后端 API 和交互文档分别位于
<http://localhost:8000/api/v1/health> 与 <http://localhost:8000/docs>。

也可以只运行后端：

```bash
uv sync --project backend --extra dev
uv run --project backend uvicorn infraresearch.main:app --reload
```

默认会在 `data/` 保存 SQLite、Qdrant Local 和仓库副本。没有可用的
OpenAI-compatible 服务时，系统自动使用确定性抽取式 provider，并在运行结果中
标记 `provider=extractive`。

## 常用命令

```bash
make test          # 后端与前端单元测试
make check         # Python lint + TypeScript 类型检查
make build         # 前端生产构建
make evaluate      # 运行内置 20 题双基线评测
make evaluate-gpu  # 使用实际 Qwen、Qdrant 和 E5 运行 20 题评测
make preflight     # 检查 GPU、模型端点和目录
make verify        # 完整离线验收
make verify-gpu    # 验证实际 GPU/vLLM、8K context 和 prefix cache
make worker        # 单独启动持久任务 worker（make dev 已包含）
```

RTX 3060 Laptop / WSL 环境可使用 `make setup-vllm` 创建独立持久环境，然后运行
`make start-vllm`。预下载模型路径通过 `INFRARESEARCH_VLLM_MODEL_PATH` 指定。

详细资料见 [docs/quickstart.md](docs/quickstart.md) 和
[docs/architecture.md](docs/architecture.md)。

## 项目结构

```text
backend/     FastAPI、索引、检索、Agent 和持久化
frontend/    React 研究工作台
evals/       20 题数据集与报告模板
scripts/     预检、评测和本地服务脚本
docs/        架构、API、配置、测试和运维文档
```

## 版本

当前课程原型版本为 `v0.1.1`。范围和非目标见 [PLAN.md](PLAN.md)，变更记录见
[CHANGELOG.md](CHANGELOG.md)。
