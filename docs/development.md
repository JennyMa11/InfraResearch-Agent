# 开发规范

- Python 3.12，格式与 lint 使用 Ruff；TypeScript 开启 strict。
- Conventional Commits：`feat:`, `fix:`, `test:`, `docs:`, `chore:`。
- 功能从短分支进入 `main`；合并前运行 `make test check build`。
- 数据库模型只负责持久化，Pydantic schema 是公开 API 类型。
- Retriever、Provider 和工具调用必须保留统一边界，异常必须写入轨迹。
- 新节点要说明进入条件、退出条件、重试上限和 token 影响。
- 禁止把秘密、下载仓库、数据库、模型缓存或评测运行文件提交到 Git。

前端类型当前与 OpenAPI schema 对齐。生产项目可使用 `openapi-typescript` 从
`/openapi.json` 重新生成，v0.1.0 保留无额外代码生成依赖的轻量版本。
