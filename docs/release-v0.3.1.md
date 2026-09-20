# v0.3.1 发布说明

发布日期：2026-09-20

这是 v0.3.0 的 CI 权限修复版本。`compare_evaluations.py`、`compare_runtime.py` 和
`verify_ocr.py` 现在以 Git mode `100755` 发布，解决干净 checkout 中 Ruff `EXE001`
错误。功能、模型实验数据与 v0.3.0 保持不变。

修复后完整 `make verify` 通过，包括 Python/TypeScript 静态检查、70 个后端测试、
9 个前端测试、Playwright、生产构建、隔离 API/Worker 流程和 50 题三模式评测。
