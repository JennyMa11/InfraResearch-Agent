# 故障排查

| 现象 | 检查与处理 |
|---|---|
| 数据源一直 pending | 查看后端日志与 `/ingestions/{id}`；确认写入 `data/` 权限 |
| Qdrant 打不开 | 停止重复后端进程；设 `VECTOR_BACKEND=sqlite` 临时降级 |
| Embedding 配置变化后结果异常 | 重启服务；配置指纹会重建 collection |
| GitHub 403 | 设置 `GITHUB_TOKEN`，检查 rate-limit 响应 |
| 仓库导入超限 | 调高 `MAX_REPO_FILES` 前确认磁盘与课程范围 |
| 模型超时 | 检查 `/v1/models`；降低 context 或使用 extractive 降级 |
| WSL 看不到 GPU | 按 [gpu-wsl.md](gpu-wsl.md) 更新驱动与 WSL |
| SSE 到最后才出现 | 在反向代理关闭 buffering，保留 `X-Accel-Buffering: no` |
| 报告没有引用 | 这是验证失败信号；补充资料，不要手工伪造 marker |
