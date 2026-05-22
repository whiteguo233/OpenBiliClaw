# Docker 部署指南

[← 返回 README](../README.md)

## 前置条件

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) V2（`docker compose` 命令）
- 一个 LLM API Key（OpenAI / Claude / Gemini / DeepSeek / OpenRouter）—— **Embedding 用 compose 自带的 Ollama 不再需要单独申请**

### v0.3.11+ 自带 Ollama embedding sidecar

`docker-compose.yml` 现在多了一个 `ollama` 服务：自动拉 `bge-m3` 模型，对外暴露 `http://ollama:11434`，用 Docker 网络和后端互通。第一次 `docker compose up -d --build` 会多花 2–4 分钟下载模型（~568MB），之后用 named volume `openbiliclaw_ollama` 持久化，重建容器不重拉。

后端容器首次启动时会自动把 `[llm.embedding] provider="ollama" model="bge-m3" base_url="http://ollama:11434/v1"` 写进生成的 `config.toml`，所以你**只需要给一个 chat 模型的 Key**，embedding 完全免费 + 离线可用。

不需要这个 sidecar？删掉 `docker-compose.yml` 里 `ollama` 服务块和后端的 `OPENBILICLAW_SEED_OLLAMA_DEFAULTS` 环境变量即可。

### 平台支持（v0.3.4+）

镜像基于 `python:3.11-slim`（多架构 manifest），同一份 `docker-compose.yml` 可以在以下平台直接跑：

| 平台 | 架构 | 备注 |
|------|------|------|
| macOS Intel | linux/amd64 | Docker Desktop |
| macOS Apple Silicon (M1/M2/M3) | linux/arm64 | Docker Desktop，自动选 arm64 |
| Linux x86_64 | linux/amd64 | 直接 Docker Engine |
| Linux ARM (Raspberry Pi 4/5) | linux/arm64 | 直接 Docker Engine |
| Windows | linux/amd64 (默认) | Docker Desktop（默认 WSL2 backend）|

`docker compose build` 会自动按主机架构选择正确的 base image 层。如果你要为发布构建跨架构镜像，用 buildx：

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t openbiliclaw-backend:v0.3.4 .
```

## 多源登录前置：装了扩展的浏览器要登录每一个想用的源

OpenBiliClaw 不爬登录态——它复用**你**当前浏览器的登录会话来跨平台抓你能看到的内容。Docker 部署后，仍然需要在装了扩展的同一个浏览器里登录每个目标源：

- **B 站**：浏览器里登录 https://www.bilibili.com 即可。v0.3.12+ 扩展会自动把 Cookie 推到容器里的 `/api/bilibili/cookie`，免 F12
- **小红书**：必须在浏览器里登录 https://www.xiaohongshu.com。后端不直接抓小红书，所有发现/详情都通过扩展以你的登录态执行——大部分任务(search / creator 抓取)在隐藏 tab 里跑;但 v0.3.22+ 起 `init` 期间的 **bootstrap_profile 滚动任务会临时打开一个前台 tab**(后台 tab 在小红书上无法触发瀑布流懒加载),会抢一次焦点 10-30 秒,完成后自动关闭。**不登录 = 完全没有小红书内容**
- **抖音**：如果要启用 `init --yes-douyin`、`fetch-douyin` 或 `discover --source douyin`，必须在装了扩展的宿主机浏览器里登录 https://www.douyin.com。后端不直接抓抖音；初始化只接收扩展回传的发布 / 收藏 / 点赞 / 关注信号。search / hot / feed discovery 优先走登录浏览器插件签名桥；Cookie 可用环境变量覆盖或由扩展同步到容器 volume 的 `data/douyin_cookie.json`。不登录或触发风控时会返回 0 条并让 init 继续。
- **YouTube**：如果要启用 `init --yes-youtube` 或 `fetch-youtube`，必须在装了扩展的宿主机浏览器里登录 https://www.youtube.com。后端不直接抓 YouTube；初始化只接收扩展回传的观看历史 / 订阅 / 点赞信号。不登录、页面布局变化或任务仍在后台跑时会返回 0 条并让 init 继续。
- **CDP 说明**：小红书、抖音和 YouTube 当前都走 Chrome 插件任务链路，不需要额外启动 CDP 调试 Chrome。`[sources.browser].cdp_url` 只保留给通用 Web / 自定义网页源的浏览器抓取场景。

详见 [配置参考 / sources.browser 段](modules/config.md#sourcesbrowser)。

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw

# 2. Docker 主路径：启动 compose、确认配置、等待 Cookie、自动运行 init
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie

# 3. 健康状态：HEALTHCHECK 会让 docker compose ps 在容器真正可服务后才显示 healthy
docker compose ps
```

`agent_bootstrap.py --mode docker` 是 Docker 部署的主入口：它会启动 compose，把宿主机确认后的 `config.toml` 同步到容器 `/app/runtime`，在 B 站 Cookie 通过扩展同步后继续自动运行 init。缺 LLM Key、缺 Cookie 或缺来源确认时，bootstrap 会停在明确的 `needs_secrets` / `needs_decisions` 状态并打印继续命令；这不是最终成功状态。

**手动 fallback**：高级排查或重复初始化时，仍可直接运行：

```bash
docker exec -it openbiliclaw-backend openbiliclaw init
```

`init` 是 v0.3.20+ 的交互式向导，自动检测 `config.toml` 缺哪些配置并按需引导。每一步都有"不确定就回 1"的默认推荐：

1. **Phase 1 — 选 LLM 服务（7 项菜单）**：**第一推荐 DeepSeek**(`deepseek-v4-flash` / ¥0.001/千 token,几乎免费,国内可直连);**第二推荐 #2 "中转站 / OpenAI 协议兼容服务"**——买了中转站 / OneAPI Key 的人走这个,也覆盖 Kimi / 通义 / 智谱 / Yi / MiniMax 官方 / Azure / vLLM,进子菜单后默认就是中转站 preset。其它选项: #3 OpenAI 官方(`gpt-5-nano`) / #4 Gemini(`gemini-2.5-flash`) / #5 Claude(`claude-sonnet-4-6`) / #6 OpenRouter(`openai/gpt-5-nano`) / #7 本地 Ollama(`qwen2.5:7b`)。Phase 2 会再次显示模型可选项让你确认。
2. **Phase 2 — 给所选服务填配置**：每个选项只问该选项需要的字段。Ollama 不问 Key（自动装 + 拉模型）；云厂商只问 API Key；选 #2(协议兼容)进子菜单后,Base URL + 默认模型自动填好,只用填 API Key 和确认模型。
3. **Phase 3 — Embedding（向量化，独立提问，3 选 1 + 高级）**：
   - **1) 本地 Ollama bge-m3**（默认推荐 / 免费 / 离线 / 不消耗主 LLM 配额）
   - **2) 云端 Gemini embedding**（质量略高 / 跨语言更稳 / 免费档每天 1500 次够用）
   - **3) 不启用 embedding**（可稍后在设置页或 `setup-embedding` 单独配置）
   - 高级：自定义 OpenAI 兼容服务 / 指定其他 provider（默认折叠）
4. **Phase 4 — Per-module 覆盖（高级，默认跳过）**：可单独给 soul / discovery / recommendation / evaluation 指定不同模型。

> 💡 **Embedding 选择**：交互式 `init` 会单独问；AI agent 一句话安装则必须在调用 `agent_bootstrap.py` 时显式传 `--embedding-provider ... --embedding-model ...`（默认推荐 `ollama` + `bge-m3`）。Embedding 不再“跟随主 LLM”，传 `--embedding-provider ""` 表示不启用 embedding。运行时 embedding fallback 默认关闭；需要自动切 provider 或借用 chat-side 凭据时，在设置页打开 embedding fallback。

接着 B 站登录态有 **2 种方式**（v0.3.12+）：

- **A.** 装浏览器扩展（推荐，零配置）—— [下载](https://github.com/whiteguo233/OpenBiliClaw/releases) 装好登录 B 站后，扩展会几秒内把 Cookie 自动推到 `http://127.0.0.1:8420/api/bilibili/cookie`。bootstrap 会等待 Cookie 到达并继续自动运行 init
- **B.** 手动贴 Cookie —— 向导内附 F12 → Network 取 cookie 的 5 步教程

最后才进入真正的 init 阶段：拉历史、生成画像、跑首轮发现。init 会先确认 B 站初始化信号上限：历史固定最多 300 条，收藏 / 关注默认各最多 300 条 / 人，直接回车接受默认，也可输入数字调整；脚本化可传 `--bilibili-favorite-limit N` / `--bilibili-follow-limit N`，`0` 表示跳过对应信号。整个流程会打印进度，不要以为卡住了——LLM 单次响应可能就要 10–30s。

AI agent 一句话部署时，`agent_bootstrap.py` 会在 auto-init 期间额外输出
`BOOTSTRAP_STATUS status=progress message=init_progress` 事件。Agent 应把
这些 `1/4`、`2/4`、`3/4`、`4/4` 和发现补货进度及时转述给用户，而不是等
最终 `init_complete` 后才汇报。

> 🌸 **小红书数据是否加入(v0.3.27+)**:init 在 Docker 里跑时也会弹一个交互式问题——把小红书的收藏 / 点赞混进画像吗?
> - 想加就回 Y,会有准备清单提示你装扩展 + 登录小红书。注意 Docker 模式下扩展是装在你**宿主机**的浏览器里的,后端在容器内通过 8420 端口拉数据
> - 直接回车或回 N 会跳过,只用 B 站数据建画像
> - 脚本化场景直接传 flag:`docker exec -it openbiliclaw-backend openbiliclaw init --no-xhs` 跳过 / `--yes-xhs` 强制启用
> - AI agent 的 `agent_bootstrap.py` auto-init 不会默认启用小红书；必须传 `--yes-xhs` 或 `--no-xhs`。没传会返回 `needs_decisions`，让 agent 先问用户
> - 想永久跳过:在 docker-compose.yml 里加 `OPENBILICLAW_NO_XHS=1` 环境变量

> 🎵 **抖音数据是否加入(v0.3.67+)**:init 也会单独问是否把抖音发布 / 收藏 / 点赞 / 关注混进画像。
> - 想加就回 Y，会提示你装扩展 + 登录抖音。注意扩展仍在宿主机浏览器里执行，Docker 容器只通过 8420 端口收结果
> - 不想加就回 N；非交互式终端默认跳过抖音
> - 脚本化场景直接传 flag:`docker exec -it openbiliclaw-backend openbiliclaw init --no-douyin` 跳过 / `--yes-douyin` 强制启用
> - AI agent 的 `agent_bootstrap.py` auto-init 同样要求 `--yes-douyin` 或 `--no-douyin` 二选一
> - 想永久跳过:在 docker-compose.yml 里加 `OPENBILICLAW_NO_DOUYIN=1` 环境变量

> 🌐 **YouTube 数据是否加入**:init 也会单独问是否把 YouTube 观看历史 / 订阅 / 点赞混进画像。
> - 想加就回 Y，会提示你装扩展 + 登录 YouTube。注意扩展仍在宿主机浏览器里执行，Docker 容器只通过 8420 端口收结果
> - 不想加就回 N；非交互式终端默认跳过 YouTube
> - 脚本化场景直接传 flag:`docker exec -it openbiliclaw-backend openbiliclaw init --no-youtube` 跳过 / `--yes-youtube` 强制启用
> - AI agent 的 `agent_bootstrap.py` auto-init 同样要求 `--yes-youtube` 或 `--no-youtube` 二选一
> - 想永久跳过:在 docker-compose.yml 里加 `OPENBILICLAW_NO_YOUTUBE=1` 环境变量

> 💡 **AI agent 一句话部署**：把下面这句粘到 Claude Code / Codex CLI / Cursor / OpenClaw：
> ```
> 请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端（务必用 Bash 的 curl 下载这个文档，不要用 WebFetch）
> ```
> 跨平台一致：Mac / Windows / Linux 上 AI 都按同一份文档执行。

## 配置

容器首次启动时会基于 `config.example.toml` 自动生成配置模板到 Docker volume 中。你可以通过以下方式编辑：

```bash
# 方式一：通过 init 命令交互式配置（推荐）
docker exec -it openbiliclaw-backend openbiliclaw init

# 方式二：直接编辑容器内的配置文件
docker exec -it openbiliclaw-backend vi /app/runtime/config.toml
```

### 环境变量

可通过环境变量覆盖部分配置，在 `docker-compose.yml` 的 `environment` 中设置或启动时传入：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENBILICLAW_PROXY_HOST` | `host.docker.internal` | 代理主机地址 |
| `OPENBILICLAW_PROXY_PORT` | `7897` | 代理端口 |
| `OPENBILICLAW_PROXY_TIMEOUT` | `1.0` | 代理探测超时（秒） |

### LLM 配置

`init` 向导会按你选的 provider 自动写好 `[llm.<provider>]` 段。如果你想手动改，下面是 v0.3.5+ 的对照表（按推荐顺序排列）：

| Provider | 是否要 Key | 适合谁 | 备注 |
|---|---|---|---|
| `deepseek` ★默认 | ✅ | 默认推荐 / 几乎免费 / 国内可直连 | ¥0.001/千 token，月费通常 ¥0.5-2，OpenAI 兼容协议。无 embedding 接口；embedding 需在 `[llm.embedding]` 独立配置 |
| `gemini` | ✅ | Google AI Studio 账户 | 免费档每天 1500 次够日常用；自带 embedding endpoint |
| `openai` | ✅ | 已有 OpenAI 账户 | base_url 留空 = `https://api.openai.com/v1`；自带 embedding endpoint |
| `claude` | ✅ | Anthropic 账户 | 高质量推理；无 embedding 接口，需独立配置 `[llm.embedding]` |
| `openrouter` | ✅ | 想一个 Key 跑多家模型 | 按调用计费；embedding 不可靠，建议独立配置 Ollama / Gemini / OpenAI embedding |
| `ollama` | ❌ | 完全离线 / 不要 Key / 16GB+ 内存 | CPU 推理首次响应慢（10-60s）。Docker 里 `[llm.ollama] base_url` 必须设成 `http://host.docker.internal:11434/v1` 才能从容器访问宿主机的 Ollama |
| OpenAI 协议兼容自建网关（高级） | ✅ 通常需要 | 自己有 vLLM / LMStudio / Azure / OneAPI / 团队 LLM 网关 | 写到 `[llm.openai]` 同段，关键是显式 `base_url` 字段。**普通用户不要选这个** |

> 「OpenAI 官方」 ≠ 「OpenAI 协议兼容自建网关」：v0.3.6+ 向导把这两个拆成独立菜单项，v0.3.20+ 把"自建网关"挪到菜单末尾的"高级"位置（避免普通用户误选）。后端写到同一个 `[llm.openai]` 段，区分点是 `base_url` 字段。
>
> v0.3.20+：当 `--provider openai` 显式给出但 `--llm-base-url` 未给（或选了官方），bootstrap 会自动清空 `[llm.openai] base_url`，让 SDK 回到 `https://api.openai.com/v1`——之前从自建网关切回 OpenAI 官方时 base_url 残留导致继续打老网关的 bug 已修。

**Per-module 覆盖（可选）**：在 `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]` 段单独指定 `provider` + `model`。典型用法：发现 / 评估走便宜模型，灵魂画像走高质量模型。详见 [docs/modules/config.md](modules/config.md)。

## 日常命令

所有 CLI 命令通过 `docker exec` 在容器内执行：

```bash
# B 站认证登录
docker exec -it openbiliclaw-backend openbiliclaw auth login

# 可选：启用本地 Ollama 作为独立 embedding provider
docker exec -it openbiliclaw-backend openbiliclaw setup-embedding

# 手动触发内容发现
docker exec -it openbiliclaw-backend openbiliclaw discover

# 查看推荐
docker exec -it openbiliclaw-backend openbiliclaw recommend

# 查看用户画像
docker exec -it openbiliclaw-backend openbiliclaw profile
```

### 生命周期管理

```bash
# 启动（需要在项目目录）
docker compose up -d

# 停止
docker compose down

# 重新构建（代码更新后）
docker compose up -d --build

# 查看容器日志
docker compose logs -f openbiliclaw-backend
```

> **注意**：Docker 镜像在构建时打包代码，`git pull` 后必须加 `--build` 重新构建，否则容器内运行的仍是旧版代码。
> 如果发现画像内容缺失或功能不符合预期，首先尝试 `docker compose up -d --build` 重建镜像。

## 默认行为

- 后端对外监听 **`8420`** 端口
- 配置、数据、日志存放在 Docker named volumes 中：
  - `openbiliclaw_config` → `/app/runtime`（配置文件）
  - `openbiliclaw_data` → `/app/runtime/data`（SQLite 数据库等）
  - `openbiliclaw_logs` → `/app/runtime/logs`（日志文件）
- 健康检查地址：`http://127.0.0.1:8420/api/health`
- 容器设置为 `restart: unless-stopped`，异常退出后自动重启

## 数据与存储

Docker 部署默认与宿主机项目目录**完全隔离**，所有数据保存在 Docker named volumes 中。

### 查看日志

```bash
# 查看容器标准输出
docker compose logs -f

# 查看应用日志文件
docker exec -it openbiliclaw-backend cat /app/runtime/logs/openbiliclaw.log
```

### 备份数据

```bash
# 备份数据库
docker cp openbiliclaw-backend:/app/runtime/data ./backup-data

# 备份配置
docker cp openbiliclaw-backend:/app/runtime/config.toml ./config-backup.toml
```

### 彻底重置

删除所有 volumes 并重建，将清除所有数据（配置、画像、历史记录）：

```bash
docker compose down -v
docker compose up -d --build
```

## 网络与代理

### Clash 代理

容器启动时自动探测宿主机 Clash 代理（默认 `host.docker.internal:7897`）。

自定义代理端口：

```bash
export OPENBILICLAW_PROXY_PORT=7890
docker compose up -d --build
```

自定义代理主机：

```bash
export OPENBILICLAW_PROXY_HOST=192.168.1.100
docker compose up -d --build
```

### Ollama 本地模型

如使用宿主机上的 Ollama，需确保 Ollama 监听 `0.0.0.0`，并在配置中设置：

```toml
[llm.ollama]
model = "llama3"
base_url = "http://host.docker.internal:11434"
```

### 本地 embedding provider（Ollama + bge-m3）

不想再多一份 embedding API Key、或想让系统在断网时仍能跑相似度计算，可以让 Ollama 同时承担 embedding 服务：

```bash
# 1. 在宿主机拉取 bge-m3（首次 ~568MB，CPU 即可跑）
ollama pull bge-m3

# 2. 在容器里写入 embedding 配置（推荐用 setup-embedding 命令）
docker exec -it openbiliclaw-backend uv run openbiliclaw setup-embedding
```

或直接编辑 `config.toml` 的 `[llm.embedding]` 段：

```toml
[llm.embedding]
provider = "ollama"
model = "bge-m3"
base_url = "http://host.docker.internal:11434/v1"
```

注意：容器需要能访问宿主机的 Ollama；embedding 现在读取 `[llm.embedding].base_url`，不会自动复用 `[llm.ollama].base_url`。

## 常见问题

**Q: 容器启动后如何确认服务正常？**

```bash
curl http://127.0.0.1:8420/api/health
```

**Q: 如何更新到最新版本？**

```bash
git pull
docker compose up -d --build
```

**Q: 端口 8420 被占用怎么办？**

修改 `docker-compose.yml` 中的端口映射：

```yaml
ports:
  - "9090:8420"  # 宿主机 9090 → 容器 8420
```

**Q: 数据库出现问题怎么修复？**

如果数据库出现问题，可以在容器内运行 `docker exec openbiliclaw-backend openbiliclaw db-repair` 进行检查和修复。

**Q: 后端启动了、健康检查也通过了，但插件里没有推荐？**

最常见原因是没有执行过 `init`。容器启动只运行 API 服务器，用户画像需要通过 init 命令生成：

```bash
docker exec -it openbiliclaw-backend openbiliclaw init
```

也可以检查 health endpoint 确认画像状态：

```bash
curl -s http://127.0.0.1:8420/api/health | python -m json.tool
# 看 "profile_ready" 字段：false 或缺失都表示还需要跑 init
```

v0.3.80+ 后端会在首次同步到行为数据后自动尝试生成画像，但手动 init 能获得更完整的初始画像（包含历史标题、作者等上下文信息）。
