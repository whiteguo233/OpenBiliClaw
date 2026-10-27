# issue #318 真实请求验收：Ollama embedding 构建（2026-10-22）

分支 `fix/issue-318-embedding-ollama`，提交 `bafcc0f2`。目标：在本机真实 Ollama + 真实 HTTP 请求下，
对比修复前后「embedding 服务能否构建、init 能否越过向量门槛」，并复现 issue #318 的打包版症状。

## 环境

- macOS，仓库 `.venv`（Python 3.12，httpx 0.28.1，openai 2.28.0）
- 本机 Ollama `127.0.0.1:11434`，`bge-m3:latest`（1024 维）与 `qwen2.5:0.5b` 均已就绪
- 隔离运行根目录：`/tmp/obc318`（修复版基线，端口 18765）、`/tmp/obc318b`（修复版 + 坏 SDK，18766）、
  `/tmp/obc318c`（main 版 + 坏 SDK，18767），互不共享 DB / config
- 配置：chat 走 `provider_type="ollama"` + `qwen2.5:0.5b`；`[llm.embedding] provider="ollama"`、
  `model="bge-m3"`、`base_url="http://127.0.0.1:11434/v1"`
- 故障注入：`/tmp/obc318shim/sitecustomize.py` 把 `openai.AsyncOpenAI` 包一层，**仅当调用栈里出现
  `build_embedding_service` / `_build_dedicated_embedding_provider`** 时抛
  `AttributeError("'AsyncHttpxClientWrapper' object has no attribute '_mounts'")`，其余路径放行。
  这样只有 embedding 构建会坏，chat 仍然健康，与 issue 报告的现象一致。

## 结果

### A. 修复版 + 真实 Ollama（18765）

| 请求 | 结果 |
| --- | --- |
| `GET /api/health` | `embedding_ready: true`、`llm_registered: true` |
| `GET /api/init-status` | `embedding_check: "ok"`、`embedding_detail: ""`、`ollama_phase: "ready"`，唯一阻塞为 `bilibili_not_logged_in` |
| `POST /api/config/probe-service`（kind=embedding） | `ok: true`、`provider: "ollama"`、`model: "bge-m3"`、`latency_ms: 2566` |

`ollama ps` 中 `bge-m3:latest` 已被真实加载（1.1 GB，100% CPU），确认探活打的是真实模型。

### B. 坏 SDK 对比（同一份配置、同一台 Ollama）

| 观测点 | 修复版（18766） | main（18767） |
| --- | --- | --- |
| `GET /api/health` → `embedding_ready` | `true` | `false` |
| `GET /api/init-status` → `embedding_check` | `ok` | `misconfigured` |
| `GET /api/init-status` → `embedding_detail` | `""` | `embedding 服务未能构建（provider=ollama）——请到设置页检查该 provider 的 API Key / base_url 后重新保存。` |
| `POST /api/config/probe-service` | `ok: true`，`latency_ms: 2349` | `ok: false`，`"Embedding service could not be built from the submitted config."`，`latency_ms: 14` |
| `POST /api/init`（github + 真实用户名，越过后必须在 embedding 门前停下） | **HTTP 202**，run 正常启动（随后手动 cancel） | **HTTP 409** `{"error":"embedding_not_ready", ...}` |
| 服务端日志 | `Embedding L2 encoding migration: {'migrated': 0, ...}`（说明服务真的建起来了） | 与 embedding 相关零日志——异常被 `except Exception: return None` 吞掉 |

修复版侧被取消的那次 init：`POST /api/init/cancel` 返回 `{"cancelling": true}`，随后 `running: false`、`reason: "cancelled"`。

## 结论

- 坏 SDK 下 main 完整复现 issue #318 的链路：`embedding_check: misconfigured` → `POST /api/init` 恒 409，且日志里没有任何原因。
- 同一故障注入下修复版不再受影响：embedding 服务正常构建、真实 bge-m3 探活通过、init 越过向量门槛返回 202。
- 修复版运行期间只有 `image-service` 子进程退出（与 embedding 无关），没有 embedding 相关 traceback。

## 复现步骤

```bash
# 1. 准备隔离根目录与 config.toml（chat=ollama qwen2.5:0.5b, embedding=ollama bge-m3）
mkdir -p /tmp/obc318 && $EDITOR /tmp/obc318/config.toml

# 2. 故障注入 shim（见上文说明）
mkdir -p /tmp/obc318shim && $EDITOR /tmp/obc318shim/sitecustomize.py

# 3. 起服务（修复版）
OPENBILICLAW_PROJECT_ROOT=/tmp/obc318b PYTHONPATH=/tmp/obc318shim:src \
  .venv/bin/python -m openbiliclaw.cli serve-api --host 127.0.0.1 --port 18766

# 4. 起服务（main 版，对照）
OPENBILICLAW_PROJECT_ROOT=/tmp/obc318c PYTHONPATH=/tmp/obc318shim:<main>/src \
  .venv/bin/python -m openbiliclaw.cli serve-api --host 127.0.0.1 --port 18767

# 5. 逐项请求 /api/health、/api/init-status、/api/config/probe-service、/api/init
```
