# Windows / WSL GPU 配置

先运行：

```bash
make preflight
```

WSL 内 `nvidia-smi` 应能看到 RTX 3060 Laptop 和驱动；不要在 WSL 内安装独立
Windows 显卡驱动。若不可见，更新 Windows NVIDIA 驱动与 `wsl --update`，重启
WSL，并确认 Docker Desktop 的 WSL 集成（仅当使用容器）。

把 vLLM 安装到项目专用的持久环境：

```bash
make setup-vllm
INFRARESEARCH_VLLM_MODEL_PATH=/home/kylin/huggingface/Qwen3-0.6B make start-vllm
```

当前已验证参数是 vLLM 0.23.0、Qwen3-0.6B、8192 context、单并发、0.75 GPU
内存利用率、eager execution 和 Automatic Prefix Caching。脚本同时固化了此
WSL 环境需要的旧 runner、非 FlashInfer sampler、GCC 12 和同步调度开关。

Prefix Cache 对照实验应分别以
`INFRARESEARCH_VLLM_PREFIX_CACHING=1` 和
`INFRARESEARCH_VLLM_PREFIX_CACHING=0` 重启同一模型，其他参数保持不变；两个结果
必须分别保存，不能用单次运行中的累计 counter 代替 on/off 对照。

2026-09-20 的 Qwen3-0.6B 控制实验中，Cache Off/On 的热态 TTFT 中位数分别为
80.9 ms 和 49.7 ms，开启后降低 38.5%，并观察到 2,688 个 cache-hit 增量。两组
1/4/8/16 并发任务最终成功率均为 100%；关闭组在 WSL 高并发观测中出现过瞬时
Connect/Read 错误，benchmark 会重试并在 JSON 中保留计数。原始结果见
`evals/results/runtime-prefix-{off,on,comparison}.json`。

所有参数都可通过 `.env` 覆盖；主要变量见 `.env.example`。6GB Laptop GPU 的
可用显存受显示器和其他进程影响，OOM 时先降低
`INFRARESEARCH_VLLM_MAX_MODEL_LEN` 或
`INFRARESEARCH_VLLM_GPU_MEMORY_UTILIZATION`。基础应用可以在 vLLM 不可用时继续
使用 extractive provider。
