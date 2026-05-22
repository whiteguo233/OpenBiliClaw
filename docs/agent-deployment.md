# Agent 一键部署指南

[← 返回 README](../README.md)

这份文档写给**AI 编码智能体**（Claude Code / Codex CLI / OpenClaw / Cursor Agent 等），同时也适合人类维护者作为部署参考。

当用户在 README 里看到那段复制粘贴的 "Agent deployment prompt"，把它整段粘贴给任意一个编码智能体时，智能体要能够仅凭这段文字独立完成 OpenBiliClaw 后端的完整部署、配置补齐和健康自检。本文是那段 prompt 的完整操作契约。

---

## 你的任务（给 AI 智能体）

你收到了一个包含以下意图的指令：

> 在当前环境把 OpenBiliClaw 后端跑起来，如果用户机器上已经有另一份 OpenBiliClaw 目录，就把它里面的 API Key / Cookie / 登录态复用过来，不要反复问用户。如果确实缺关键凭据，就明确告诉用户需要补什么。

你应当按以下顺序工作，并**不要擅自跳过步骤**。

### 1. 核对前置条件

必须存在：

- `python3`（3.11+）或 `uv`
- `git`

可选（存在就优先用）：

- `docker` + `docker compose`（部署后端首选）
- 另一份已经配好的 OpenBiliClaw 目录（用来复用 API Key 和 Cookie）

用户通常会在**项目目录外**直接发起这个部署，例如：

```bash
mkdir -p ~/workspace/openbiliclaw-new
cd ~/workspace/openbiliclaw-new
```

### 2. 获取代码

- 如果当前目录已经是 OpenBiliClaw 仓库（存在 `pyproject.toml` 和 `config.example.toml`），直接用当前目录。
- 否则确认当前目录为空，然后 `git clone https://github.com/whiteguo233/OpenBiliClaw.git .`。
- 永远不要 `rm -rf` 一个非空目录。

### 3. 定位已有的 OpenBiliClaw 安装（关键）

这是最重要的一步。用户明确表示**希望复用旧项目里的凭据**，你的默认行为就是找到那份旧安装。

按以下顺序查找（找到第一份有效的就停止）：

1. 用户明确告诉你的路径。
2. 常见工作区路径：
   - `~/workspace/OpenBiliClaw`
   - `~/OpenBiliClaw`
   - `~/projects/OpenBiliClaw`
   - `~/code/OpenBiliClaw`
3. 在家目录下做一次 `find`：

```bash
find ~ -maxdepth 4 -type f -name "config.toml" -path "*OpenBiliClaw*" 2>/dev/null
```

一份"有效"的安装必须满足：

- 存在 `config.toml`（不是 `config.example.toml`）
- `config.toml` 里至少有一个非空的 `api_key`（`llm.openai.api_key` / `llm.gemini.api_key` / `llm.deepseek.api_key` / `llm.claude.api_key` / `llm.openrouter.api_key`）或者
- 存在 `data/bilibili_cookie.json`

如果找不到任何一份符合条件的安装，**向用户问一次**："我没找到已有的 OpenBiliClaw 安装，请告诉我路径，或者确认我现场帮你从 0 填配置。" 只问一次。

### 4. 跑部署脚本

用户的仓库里有 `scripts/agent_bootstrap.py`。这是你唯一需要直接调用的自动化入口；目标是推进到 `init_complete`，不是停在“后端已启动”。

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --mode auto \
  --reuse-from /ABSOLUTE/PATH/TO/EXISTING/OpenBiliClaw
```

如果没有找到已有安装，就去掉 `--reuse-from`。

关键参数：

| 参数 | 含义 |
|------|------|
| `--project-dir` | 目标仓库目录。默认当前目录。 |
| `--mode` | `auto`（默认，有 Docker 走 Docker，否则 local）、`docker`、`local`。 |
| `--reuse-from PATH` | 从另一份 OpenBiliClaw 目录复用 API Key / Bilibili Cookie。 |
| `--provider NAME` | 强制切换默认 LLM provider（openai/claude/gemini/deepseek/ollama/openrouter）。OpenAI 协议兼容自建网关也填 `openai`，配合 `--llm-base-url`。 |
| `--llm-api-key KEY` | 给当前（或 `--provider` 指定的）provider 写入 API Key。 |
| `--llm-base-url URL` | （v0.3.5+）覆盖该 provider 的 `base_url`。**OpenAI 协议兼容服务必填**（Azure / vLLM / LMStudio / OneAPI / 自建网关）。 |
| `--llm-model NAME` | （v0.3.5+）覆盖该 provider 的 chat 模型名。 |
| `--embedding-provider NAME` | （v0.3.5+）embedding provider。空字符串 = 不启用 embedding；填 `ollama` 走本地 bge-m3；填其他 provider 则单独走该家。 |
| `--embedding-model NAME` | （v0.3.5+）embedding 模型名（典型: `bge-m3`、`text-embedding-3-small`）。 |
| `--embedding-base-url URL` | （v0.3.5+）自托管 embedding 网关的 base_url，会写到 `[llm.embedding].base_url`。 |
| `--embedding-api-key KEY` | （v0.3.5+）自托管 embedding 网关的 API Key，会写到 `[llm.embedding].api_key`。 |
| `--module-override MODULE=PROVIDER:MODEL` | （v0.3.5+，可重复）per-module LLM 覆盖。MODULE ∈ {soul, discovery, recommendation, evaluation}。例：`--module-override discovery=deepseek:deepseek-v4-flash`。 |
| `--bilibili-cookie VALUE` | 直接写入 Bilibili Cookie，同时落盘到 `data/bilibili_cookie.json`。 |
| `--interactive-confirm` | 人类直接运行 installer 时使用：bootstrap 会在终端里确认 embedding、B 站初始化收藏 / 关注上限、B 站 Cookie 来源和 XHS / Douyin / YouTube opt-in。AI agent 通常自己问完用户后传显式参数。 |
| `--wait-for-extension-cookie` | 缺 B 站 Cookie 且用户选择浏览器扩展同步时，后端健康后等待扩展把 Cookie 推到 `/api/bilibili/cookie`，同步后继续自动 init。 |
| `--bilibili-favorite-limit N` / `--bilibili-follow-limit N` | auto-init 传给 `openbiliclaw init` 的 B 站收藏 / 关注信号上限；默认各 300，`0` 表示跳过对应信号。 |
| `--yes-xhs` / `--no-xhs` | （v0.3.30+）auto-init 前的小红书数据决策。`--yes-xhs` 仅在用户明确同意把小红书收藏 / 点赞混进画像时传；其他情况传 `--no-xhs`。不传则 bootstrap 返回 `needs_decisions`，不会跑 init。 |
| `--yes-douyin` / `--no-douyin` | （v0.3.67+）auto-init 前的抖音数据决策。`--yes-douyin` 仅在用户明确同意把抖音发布 / 收藏 / 点赞 / 关注混进画像时传；其他情况传 `--no-douyin`。不传则 bootstrap 返回 `needs_decisions`，不会跑 init。 |
| `--yes-youtube` / `--no-youtube` | auto-init 前的 YouTube 数据决策。`--yes-youtube` 仅在用户明确同意把 YouTube 观看历史 / 订阅 / 点赞混进画像时传；其他情况传 `--no-youtube`。不传则 bootstrap 返回 `needs_decisions`，不会跑 init。 |
| `--skip-start` | 只准备配置和依赖，不启动服务。 |
| `--skip-init` | （v0.3.7 起默认 **不要加**）凭据齐全 + 后端健康后，bootstrap 会自动跑 `openbiliclaw init`。只有当用户显式说「先别跑 init」或你只是给已经初始化过的实例补凭据时才加这个 flag。 |
| `--skip-health-check` | 启动服务但不等 `/api/health`。 |
| `--host`, `--port` | local 模式下 API 监听地址，默认 `127.0.0.1:8420`。 |

AI agent 路径不要依赖 stdin 交互：你应该先问用户，再把补齐后的参数传入。人类直接运行 `install.sh` / `install.ps1` 时，脚本会加 `--interactive-confirm --wait-for-extension-cookie`，由 bootstrap 在过程中确认并自动 init。脚本向 stdout 输出两类行：

1. 以 `[bootstrap]` 开头的人类可读日志。
2. 以 `BOOTSTRAP_STATUS:` 开头的机器 JSON 行，你**必须**解析这些行来判断状态。

典型 JSON 事件：

```json
{"status": "ok", "message": "repo_ready", "details": {...}}
{"status": "ok", "message": "secrets_reused", "details": {"reused": [...], "source": "..."}}
{"status": "ok", "message": "config_summary", "details": {"provider": "gemini", "missing": [], "has_cookie_file": true}}
{"status": "ok", "message": "mode_selected", "details": {"mode": "local"}}
{"status": "ok", "message": "dependencies_installed", "details": {}}
{"status": "ok", "message": "local_started", "details": {"host": "127.0.0.1", "port": 8420}}
{"status": "complete", "message": "backend_healthy", "details": {"health_url": "...", "missing": []}}
{"status": "progress", "message": "init_progress", "details": {"phase": "1/4", "kind": "phase", "line": "1/4 拉取 B 站历史 / 收藏 / 关注", "elapsed_seconds": 0.3}}
{"status": "complete", "message": "init_complete", "details": {"init_command": "uv run openbiliclaw init --no-xhs --no-douyin --no-youtube", "health_url": "..."}}
```

> v0.3.7+ 多了最后这一行 `init_complete`：当凭据齐全 + 后端健康 + 没加 `--skip-init` 时，`agent_bootstrap.py` 会自动跑 `openbiliclaw init`（拉历史 / 生成画像 / 跑首轮发现），完事后再发这条事件。v0.3.69+ 还会在 init 期间额外发 `status=progress, message=init_progress`，AI agent 必须把这些阶段进度实时转述给用户。失败则发 `init_failed`，但不影响 bootstrap 退出码。

最后一行的 `status` 字段是整体结论：

- `complete` — 一切就绪，API 已起来，没有缺失凭据。
- `running_with_missing_secrets` — 服务起来了但还缺 API Key 或 Cookie，某些功能会降级。
- `needs_secrets` — 没启动（或 `--skip-start`）且还缺凭据。
- `error` — 失败，`message` 和 `details.step` 会告诉你哪一步炸了。

### 5. 处理 `missing`

`config_summary` 事件里的 `details.missing` 是一个字符串数组，最多包含两类：

- `llm.<provider>.api_key` — 默认 provider 没有 API Key。
- `bilibili.cookie` — Bilibili 还没登录。

**不要从 `http://127.0.0.1:8420/api/health` 硬编码 URL**。永远从最后一条 `BOOTSTRAP_STATUS` 的 `details.health_url` 字段读，这样当用户自定义了 `--host`/`--port` 时也能命中正确的地址。

如果 `missing` 不为空：

1. 向用户清晰地一次性说明还需要什么，例如：

   > 我已经把 OpenBiliClaw 后端跑起来了（`<details.health_url>` 正常），但还缺 Bilibili Cookie。请你打开 https://www.bilibili.com 登录后，在开发者工具里复制完整的 Cookie 字符串，贴回来给我。

2. 拿到凭据后，**不要**手动改 `config.toml`。重新调用同一个脚本，把第 4 步用过的所有 flag（`--port`、`--host`、`--reuse-from` 等）原封不动地带上，再追加新的凭据参数：

   ```bash
   python3 scripts/agent_bootstrap.py --project-dir . --bilibili-cookie "$USER_PROVIDED_COOKIE" --skip-start [原有 --port / --host / --reuse-from ...]
   ```

   这样会把凭据写进 `config.toml` 和 `data/bilibili_cookie.json`，但不会重启服务，也不会意外回落到默认 8420 端口和另一个实例抢占地址。

3. 再次解析输出里的 `config_summary.missing`，确认为 `[]`。

### 6. 健康自检

脚本会自己轮询 `details.health_url`（默认 `/api/health`）。如果 `--skip-health-check` 被加上，或你想手动确认：

```bash
curl -sS "$HEALTH_URL"   # $HEALTH_URL 来自最后一条 BOOTSTRAP_STATUS 的 details.health_url
# → {"status":"ok","service":"openbiliclaw-api"}
```

如果使用 Docker 模式，健康检查之后建议再跑一次：

```bash
docker exec -it openbiliclaw-backend openbiliclaw config-show
```

### 7. 首次初始化（v0.3.7 起默认自动跑）

**v0.3.7 之前**：`init` 是「部署后可选」步骤，需要你手动触发。
**v0.3.7+ 改了**：当凭据齐全（`config_summary.missing == []`）+ 后端健康（`backend_healthy`）后，`agent_bootstrap.py` 会自动调用 `openbiliclaw init`，把推荐链路真正接通。

auto-init 会把 B 站初始化收藏 / 关注上限传给 `openbiliclaw init`；默认各 300，AI agent 可在用户明确要求时传 `--bilibili-favorite-limit N` / `--bilibili-follow-limit N` 调整。

**v0.3.30+ 又加了一道隐私 / 质量决策门槛，v0.3.67+ 增加抖音同意项，当前也要求 YouTube 同意项**：auto-init 只会在
embedding 选择已显式传入（例如 `--embedding-provider ollama
--embedding-model bge-m3`，或 `--embedding-provider ""` 表示用户选择暂不启用
embedding）且小红书 / 抖音 / YouTube 决策都已显式传入（`--yes-xhs` / `--no-xhs`、`--yes-douyin` / `--no-douyin`、`--yes-youtube` / `--no-youtube`）时执行。如果缺少其中任一项，最后一条事件会是：

```json
{
  "status": "needs_decisions",
  "message": "init_decisions_required",
  "details": {
    "missing": [],
    "init_decisions": {"missing": ["embedding", "xhs", "douyin", "youtube"]}
  }
}
```

收到这个状态时不要手动跑 `openbiliclaw init`；先问用户，再用补齐的
`--embedding-*`、`--yes-xhs` / `--no-xhs`、`--yes-douyin` / `--no-douyin` 和 `--yes-youtube` / `--no-youtube` 重新跑 bootstrap。

**为什么改成默认跑**：用户期望「一句话装机」之后打开扩展能直接看到推荐。如果 `init` 没跑，画像没生成、历史没拉、内容池是空的，用户等于没装好。

**首次运行 ≈ 2–5 分钟**（拉历史 / LLM 调用 / 多策略发现；如果启用小红书 / 抖音 / YouTube，会先由扩展拉取对应初始化信号）。bootstrap 会把 init 的 stdout 流式打到你的终端，也会把 `1/4`、`2/4`、`3/4`、`4/4`、`补货阶段`、`当前池子`、`阶段完成`、`初始化摘要` 等高信号行转换为 `BOOTSTRAP_STATUS init_progress`。不要等 `init_complete` 才回应用户。

要**显式跳过** init（比如只给已初始化的实例补一个 cookie），加 `--skip-init`：

```bash
python3 scripts/agent_bootstrap.py \
  --project-dir . \
  --bilibili-cookie "$NEW_COOKIE" \
  --skip-init
```

要**手动重跑** init（任何时候，不通过 bootstrap）：

```bash
# local + uv
uv run openbiliclaw init --no-xhs --no-douyin --no-youtube
# local + venv
.venv/bin/openbiliclaw init --no-xhs --no-douyin --no-youtube
# Docker
docker exec -it openbiliclaw-backend openbiliclaw init --no-xhs --no-douyin --no-youtube
```

如果用户明确同意跨源初始化，把上面的 `--no-xhs --no-douyin --no-youtube` 换成对应的 `--yes-xhs` / `--yes-douyin` / `--yes-youtube`；脚本化场景必须显式二选一，避免非交互终端悄悄触发账号数据拉取。

事件流里会出现 `init_complete` 或 `init_failed`：

```json
{"status": "complete", "message": "init_complete", "details": {"init_command": "uv run openbiliclaw init --no-xhs --no-douyin --no-youtube"}}
{"status": "warning", "message": "init_failed", "details": {"error": "..."}}
```

`init_failed` 不会让 bootstrap 整体退出失败——后端服务仍在跑，只是用户需要手动重试 `openbiliclaw init`。

### 8. 报告给用户

最终用简短的一段话告诉用户：

1. 用的是 `docker` 还是 `local` 模式。
2. 从哪儿复用了哪些凭据（如果有）。
3. 服务监听地址 + 健康检查 URL。
4. 如果还缺凭据：清晰的下一步指令。
5. 下一条推荐命令（如 `openbiliclaw recommend`）。

---

## 失败排查（供智能体自查）

| 故障 | 典型 `details.step` / 症状 | 处理 |
|------|--------------------------|------|
| `git` 不存在 | `clone` 步骤报错 | 提示用户先装 git，不要继续 |
| `config.example.toml` 缺失 | `config` 步骤报错 | 说明当前目录不是 OpenBiliClaw 仓库，要求确认路径 |
| `--reuse-from` 指向的目录无效 | `reuse` 步骤报错 | 回到第 3 步，重新搜索或问用户 |
| 依赖安装失败 | `install` 步骤报错 | 检查 Python 版本（需要 3.11+），尝试 `--install-cmd` 指定另一种命令 |
| Docker up 失败 | `docker_up` 步骤报错 | 降级到 `--mode local` 重新跑 |
| 健康检查超时 | `backend_healthy` 没出现，`health_check_failed` 出现 | 查看 `logs/agent-bootstrap.log`（local 模式）或 `docker compose logs` |

---

## 给人类维护者的备注

- 脚本故意不要求 stdin 输入。这是为了让各种 AI 编码智能体（很多都不支持交互式 TTY）都能照样跑完整个流程。
- 脚本写 config 的逻辑是**原位重写单行字符串**，不会改动你自己的注释或非标准字段。
- `data/bilibili_cookie.json` 是 OpenBiliClaw 运行时真正用的 cookie 源，`config.toml` 里的 `bilibili.cookie` 只是一个同步镜像。复用时两份都会被同步。
- 如果你想把这套流程接到 CI 或无人值守的部署里，可以把 `--skip-health-check` 加上，然后自行用任务队列处理健康轮询。

---

## 与其它部署指南的关系

- `docs/docker-deployment.md` — 手动 Docker 步骤（给人看的）。
- `docs/openclaw-quickstart.md` — OpenClaw 调用 OpenBiliClaw 的 CLI bridge 契约。
- 本文 `docs/agent-deployment.md` — AI 智能体**一键**部署契约。

三份文档互补：docker-deployment 说明 Docker 怎么跑；openclaw-quickstart 说明部署完成后 OpenClaw 怎么调；本文说明**从零到完成部署**这一跳该怎么走。
