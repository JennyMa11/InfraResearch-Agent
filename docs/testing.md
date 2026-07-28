# 测试指南

```bash
make test
make check
make build
```

后端单元测试覆盖 Markdown/代码分块、稳定行号、Retriever、GitHub URL 限制、
grader 重写上限、Naive 单轮检索、引用修复、token 预算和百分位。集成行为通过
临时 SQLite、固定 provider 与本地语料运行，不需要网络或 GPU。

前端 Vitest 覆盖数据源错误、轨迹、引用点击和证据预览。Playwright 冒烟：

```bash
npm --prefix frontend run test:e2e
```

GPU 测试应使用 `pytest -m gpu` 单独运行，不进入普通 CI。它需要验证模型健康、
流式首 token、8K 上下文、prefix cache 开关与 `/metrics` 抓取。
