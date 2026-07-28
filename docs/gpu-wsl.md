# Windows / WSL GPU 配置

先运行：

```bash
make preflight
```

WSL 内 `nvidia-smi` 应能看到 RTX 3060 Laptop 和驱动；不要在 WSL 内安装独立
Windows 显卡驱动。若不可见，更新 Windows NVIDIA 驱动与 `wsl --update`，重启
WSL，并确认 Docker Desktop 的 WSL 集成（仅当使用容器）。

建议把 vLLM 放在独立环境：

```bash
./scripts/start-vllm.sh
```

初始参数是 Qwen3-1.7B、8192 context、单并发、0.85 GPU 内存利用率和 Automatic
Prefix Caching。6GB Laptop GPU 的可用显存受显示器和其他进程影响；OOM 时先降低
`--max-model-len` 或 `--gpu-memory-utilization`。基础应用可以在 vLLM 不可用时
继续使用 extractive provider。
