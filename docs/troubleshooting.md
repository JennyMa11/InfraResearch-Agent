# 故障排查

| 现象 | 检查与处理 |
|---|---|
| 数据源或研究一直 pending | 确认 `python -m infraresearch.worker` 正在运行；查看 worker 日志、`jobs` 表和资源详情接口 |
| 状态一直 cancel_requested | worker 未运行或没有到达 checkpoint；确认心跳和外部 Git/模型请求是否仍活跃 |
| worker 重启后任务重新变 pending | 前一 worker 的心跳租约过期，任务已安全重排；检查旧进程为何退出 |
| Qdrant 打不开或提示 storage lock | Qdrant Local 只能由一个 worker 进程持有；停止重复 worker，再设 `VECTOR_BACKEND=sqlite` 临时降级 |
| Embedding 配置变化后结果异常 | 重启服务；配置指纹会重建 collection |
| GitHub 403 | 设置 `GITHUB_TOKEN`，检查 rate-limit 响应 |
| 仓库导入超限 | 调高 `MAX_REPO_FILES` 前确认磁盘与课程范围 |
| 模型超时 | 检查 `/v1/models`；降低 context 或使用 extractive 降级 |
| WSL 看不到 GPU | 按 [gpu-wsl.md](gpu-wsl.md) 更新驱动与 WSL |
| SSE 到最后才出现 | 在反向代理关闭 buffering，保留 `X-Accel-Buffering: no` |
| 报告没有引用 | 这是验证失败信号；补充资料，不要手工伪造 marker |
| API 正常但健康信息与研究指标不同 | 健康接口反映配置；以完成运行的 `metrics.vector_backend/provider` 确认实际执行路径 |

任务仍异常时，先检查最近 job：

```bash
sqlite3 data/infraresearch.db \
  'select id, kind, target_id, status, attempts, error from jobs order by created_at desc limit 20;'
```

不要直接把 running job 改成 completed。需要恢复时先停止旧 worker，让租约过期后由
新 worker 执行安全重排。
