# v0.3.0 发布说明

发布日期：2026-09-20

## 发布结论

v0.3.0 完成评测口径修正、两种真实 Qwen 模型的全量实验、真实 OCR、Prefix Cache
开/关控制实验与 1/4/8/16 并发验收。Docker Compose 配置校验通过；当前 WSL 未暴露
Docker daemon，因此容器实际启动是唯一宿主环境待验项。

## 真实模型实验

共同配置：RTX 3060 Laptop 6GB、vLLM 0.23.0、FP16、8192 context、Qdrant Local、
FastEmbed `multilingual-e5-small`、BM25/weighted RRF、50 题、三模式、temperature=0。

| 模型 / 模式 | 原始正确性 [95% CI] | 原始引用召回 | 修复率 | 拒答准确率 |
|---|---:|---:|---:|---:|
| Qwen3-0.6B / Naive | 0.717 [0.590, 0.833] | 0.347 | 0.72 | 0.82 |
| Qwen3-0.6B / Fixed | 0.717 [0.587, 0.833] | 0.347 | 0.72 | 0.84 |
| Qwen3-0.6B / Agentic | 0.783 [0.667, 0.880] | 0.447 | 0.60 | 0.90 |
| Qwen3-1.7B / Naive | 0.803 [0.717, 0.883] | 0.677 | 0.40 | 0.88 |
| Qwen3-1.7B / Fixed | 0.793 [0.700, 0.883] | 0.670 | 0.32 | 0.88 |
| Qwen3-1.7B / Agentic | 0.837 [0.763, 0.903] | 0.680 | 0.34 | 0.94 |

主正确性来自引用修复前答案；修复后答案单独记录。1.7B 在三种模式上分别比 0.6B
高 0.087、0.077、0.053；三个跨模型配对 95% CI 均跨零，因此只将其解释为本次
点估计更高。旧实验的反向点估计被同口径复测纠正。

## 运行时与外部验收

- Prefix Cache On：热态 TTFT P50 49.7 ms，cache hits 增量 2,688。
- Prefix Cache Off：热态 TTFT P50 80.9 ms；开启后降低 38.5%。
- On/Off 两组在 1/4/8/16 并发下最终任务成功率均为 100%；报告保留瞬时传输重试。
- 真实 image-only PDF 经 PyMuPDF rasterize + Tesseract 5.3.4 成功恢复英文文本、page=1
  与 bbox `[34.2, 48.24, 484.2, 118.08]`。
- Docker Compose v5.5.1 `config --quiet` 通过；`up --build` 因当前 WSL 缺少
  `/var/run/docker.sock` 未执行成功。

## 结果索引

- `evals/results/roadmap-50q-qwen3-0.6b-full-v2/`
- `evals/results/roadmap-50q-qwen3-1.7b-full-v2/`
- `evals/results/model-comparison-qwen3.json`
- `evals/results/runtime-prefix-on.json`
- `evals/results/runtime-prefix-off.json`
- `evals/results/runtime-prefix-comparison.json`
- `evals/results/ocr-real.json`

## 已知限制

- 生成实验为 temperature=0 的单次运行；bootstrap CI 是题目采样区间，不是多种子方差。
- Citation support 使用确定性词项支持分，不是 NLI judge。
- 真实 OCR 只验收了英文语言包。
- 容器运行仍需启用 Docker Desktop 的 WSL integration 后复验。
