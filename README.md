# InfraResearch Agent

面向基础设施与系统软件资料的可解释 Agentic RAG 课程原型。它可以索引本地
PDF/Markdown/代码或公开 GitHub 仓库，并以稳定的页码、行号和 Issue 链接生成
可验证的技术报告。

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
make preflight     # 检查 GPU、模型端点和目录
```

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

当前课程原型版本为 `v0.1.0`。范围和非目标见 [PLAN.md](PLAN.md)，变更记录见
[CHANGELOG.md](CHANGELOG.md)。
