# 本地 Ollama 自动加速与 CPU 回退验证

现场日志显示 Windows / Ollama 0.32.13 / RX 7700 XT / Vulkan 在 bge-m3
warmup 阶段以 `0xc0000409` 退出。服务端口仍响应，重新拉取模型不能恢复。
本变更针对这一执行失败增加有界 CPU 重试，不将错误码本身当作驱动根因证明。

## 自动化回归

- `pytest -q tests/test_ollama_embedding_fallback.py tests/test_ollama_diagnostics.py tests/test_llm_providers.py`：159 passed。
- 健康 API 原有 loopback 冷加载测试新增 CPU 回退分支：未回退的普通冷加载保留兼容策略；已回退后必须实际生成向量才能报告可用。
- 新增回归先验证旧实现失败：现场 runner 崩溃返回空结果、CPU 回退中的超时仍错误报告可用、HTTP 200 无效向量未尝试 CPU；随后修复并验证通过。
- 覆盖原生崩溃 / CUDA OOM / Vulkan device lost、诊断与正式调用共享模式、并发、取消、CPU 再次失败及恢复、endpoint/model 隔离、无效向量、远端及无关错误不切换。
- `pytest -q` 全量：9876 passed、65 skipped、1 failed（2020.30 秒）。失败为
  `tests/test_saved_sync_identity_pipeline.py::test_recommendation_api_preserves_canonical_identity`：
  推荐 worker 代理连接失败（`All connection attempts failed`），HTTP 502 而非期望的 200。
  该测试在本分支单独复跑通过，整个文件 14 项复跑通过；原始 main 代码的该单测也通过。
  尚未证明全量触发的顺序 / 环境原因，不将此结果记为全量通过；本次不改推荐代理链路。
- `ruff check src/ tests/`：通过。
- `mypy src/`：通过，305 source files。

## 首轮真实推理对照（含一次模拟 HTTP 500）

在本机 macOS 使用已安装的 Ollama 0.18.2 和 bge-m3，启动独立临时端口 daemon，
复用现有模型文件、禁用 prune；测试后停止该测试专属进程组，不重启用户的 Ollama。

1. 自动模式真实请求返回 1024 维向量。
2. 仅注入首次 HTTP 500（现场 `llama-server ... 0xc0000409` 签名），CPU 请求转发真实 Ollama。
3. `diagnose_ollama_embedding()` 经 CPU 重试返回 `ok`。
4. 新建 provider 继续使用 CPU，真实请求再次返回 1024 维向量。

实际终端输出（独立测试进程）：

```text
Real default inference: 1024 dimensions
Ollama embedding automatic runner failed; switching model=bge-m3 to CPU for this application session (num_gpu=0)
Injected runner crash -> real CPU inference -> diagnostic ok -> fresh provider CPU inference: PASS
```

这验证了真实 CPU 推理与应用切换链路，但没有复现 Windows Vulkan 原生崩溃，也不代表
Windows 0.32.13 / RX 7700 XT 已实机验收。用户机器应确认：首次失败后日志出现
`switching ... to CPU`、runner 使用 `-ngl 0`、后续向量请求成功且不反复重试 GPU。
若 CPU 同样失败，界面必须保留不可用与具体原因，不能以 `/api/version` 成功验收。

## 本机端到端复验：真实进程故障、真实 HTTP、真实向量

2026-10-04 对 `1126329a` 进行实测。环境为 Intel Mac / 16 GiB RAM / Ollama
0.18.2 / bge-m3。新建临时配置和数据目录，关闭采集与调度，启动独立 Ollama daemon
及真实 Uvicorn 应用（`openbiliclaw.api.app:create_app --factory`）。没有替换 provider、
HTTP transport、服务响应或向量，也没有注入假的 HTTP 500。

故障通过向**测试专属 Ollama 的直接 runner 子进程**发送 SIGKILL 产生。Ollama 实际返回
HTTP 500，错误为 `llama runner process has terminated: signal: killed`。已有系统 Ollama
及用户配置、数据库不参与测试。健康失败检查等待实际健康缓存 TTL 过期后进行。

| 场景 | 实际结果 |
| --- | --- |
| 默认模式冷启动，POST `/api/config/probe-service` | 成功，3.791 秒；健康状态可用 |
| 终止新启动的 runner，再发真实连接测试 | 同一请求自动切 CPU 并成功，4.552 秒 |
| 回退后 4 路并发，共 8 个真实请求 | 全部成功，0.911–2.863 秒；没有重复触发自动模式切换 |
| 持续终止 CPU runner | 连接测试失败；GET `/api/health` 的 `embedding_ready=false` |
| 解除故障，再发请求 | 3.043 秒成功；健康缓存更新后恢复可用 |
| 关闭并重新启动真实应用进程 | 3.175 秒成功；重新从默认模式开始 |
| 真实 `EmbeddingService` + SQLite 缓存 | 3 条中文文本各返回 1024 维有限数值向量，数据库 3 条记录 |
| 内存及重新打开数据库后的缓存命中 | L1 / L2 各读取 3 条；均未增加 Ollama 向量请求 |
| Chromium 点击“测试 Embedding”，同时终止真实 runner | 页面显示可用，4632 ms；网络记录为真实 POST 返回 `ok=true` |
| 页面测试期间连续终止两个 CPU runner | 页面显示不可用，2906 ms；没有保留上一轮成功提示 |
| 解除故障后再次点击页面测试 | 页面恢复可用，3820 ms |
| 最终初始化检查 | `/api/init-status` 返回 `embedding_check=ok`、`embedding_ready=true`、`ollama_phase=ready` |

浏览器触发故障的实际后端日志顺序：

```text
03:59:39.069 POST /api/embeddings -> HTTP 500
03:59:39.072 automatic runner failed; switching model=bge-m3 to CPU ... (num_gpu=0)
03:59:42.255 POST /api/embeddings -> HTTP 200
03:59:42.256 CPU fallback verified (model=bge-m3)
03:59:42.261 POST /api/config/probe-service -> HTTP 200
```

证据：[结构化结果](assets/2026-10-04-ollama/results.json)、
[页面回退成功](assets/2026-10-04-ollama/ollama-real-runner-recovered.png)、
[CPU 持续失败](assets/2026-10-04-ollama/ollama-real-cpu-failure.png)、
[解除故障后恢复](assets/2026-10-04-ollama/ollama-real-cpu-restored.png)。
临时服务进程组与测试浏览器均已关闭，测试脚本正常退出。
本机原始执行脚本及日志已归档至主工作区的
`output/ollama-fallback-2026-10-04/raw-output/ollama-live-e2e/`（不纳入版本控制）；
截图与浏览器轨迹也在该归档目录中，清理测试 worktree 后仍可复查。

复验后回归命令（复用主工作区虚拟环境时必须指定当前 worktree 的 `PYTHONPATH`）：

```bash
PYTHONPATH=src ../OpenBiliClaw/.venv/bin/pytest -q \
  tests/test_ollama_embedding_fallback.py \
  tests/test_ollama_diagnostics.py tests/test_llm_providers.py
# 159 passed in 25.38s
```

**验收范围：本机真实向量链路与故障恢复通过。** 这台 Intel Mac 的 Ollama 自动选择了
CPU 后端，`/api/ps` 的 `size_vram=0`；所以这里实测的是“默认自动模式失败 → 显式 CPU
重试”，没有验证物理 GPU → CPU 迁移，也没有复现 Windows / RX 7700 XT / Vulkan 的
`0xc0000409`。用户当前没有可访问的 Windows 测试机，已确认先验证本机。平台账号采集、
完整画像初始化及推荐生成不在本轮向量服务验收范围内。

## 交付边界

- 本地 loopback 向量接口共用策略；桌面 / 移动 Web / 插件 / CLI 不各自维护模式。
- 按后端进程的 endpoint/model 记忆 CPU 到退出；重启应用重新尝试自动加速。
- 不改聊天请求、系统 GPU 环境或远端 Ollama；Compose 默认 `http://ollama:11434` 不在此自动回退范围内。
- Windows 包保留 Vulkan / CPU，继续裁剪 CUDA / ROCm；本次未生成或发布安装包。
