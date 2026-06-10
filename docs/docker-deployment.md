# Docker 部署指南

[← 返回 README](../README.md)

> 🔒 **局域网访问安全（可选密码门禁）**：容器把后端暴露在 `8420`，同网段设备都能访问。需要为局域网 / 远程设备加登录密码时（本机与浏览器扩展仍免登录），设置环境变量 `OPENBILICLAW_API_AUTH_ENABLED=true` + `OPENBILICLAW_API_AUTH_PASSWORD=…`（或进容器跑 `openbiliclaw set-password`）。若前面再套同机反向代理，记得配 `[api.auth].trusted_proxies` 或让代理自行鉴权。详见 [`docs/modules/api-auth.md`](modules/api-auth.md)。

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

人类在命令行执行一行脚本时，Docker 也走和本地安装一致的完整选择流程：先选 LLM provider，再选 embedding、B 站 Cookie 获取方式和是否启用小红书 / 抖音 / YouTube 初始化信号。Contract marker: human Docker one-line installer asks the same LLM provider first.

```bash
# macOS / Linux / WSL2
MODE=docker curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

```powershell
# Windows PowerShell + Docker Desktop
$env:MODE="docker"; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

安装脚本会克隆 / 更新仓库，然后调用 `agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie`。bootstrap 的 Docker 顺序是：

1. 在宿主机终端收集安装选择。
2. 写入宿主机 `config.toml`。
3. `docker compose up -d --build` 启动后端和 Ollama embedding sidecar。
4. 把确认后的 `config.toml` / Cookie 文件同步到容器 `/app/runtime`。
5. 等浏览器扩展把 B 站 Cookie 推到 `http://127.0.0.1:8420/api/bilibili/cookie`。
6. 在容器运行时里检查默认 LLM provider 和 embedding 服务。
7. 检查通过后自动运行 `openbiliclaw init`。

默认 embedding 仍是 `ollama` + `bge-m3`，但 Docker 里会写成 compose 网络地址 `http://ollama:11434/v1`，指向随 compose 启动的 sidecar，而不是宿主机的 `localhost:11434`。如果你手动填了其他 embedding endpoint，bootstrap 不会覆盖。

B 站登录态推荐用浏览器扩展：扩展安装在**宿主机浏览器**里，不在容器里。你登录 bilibili.com 后，扩展会把 Cookie 自动 POST 到本机 `127.0.0.1:8420` 暴露出来的后端接口；bootstrap 会等待 Cookie 到达后继续 init。手动粘 Cookie 仍可用，但不是默认路径。

小红书、抖音、YouTube 是否加入初始化画像，也在一行安装脚本的人类向导里提前询问。默认都跳过，只有你明确选择 yes 才会把对应来源加入初始化；启用时仍需在宿主机浏览器里安装扩展并登录对应站点。

缺 LLM Key、缺 Cookie、缺来源确认时，bootstrap 会停在明确的 `needs_secrets` / `needs_decisions` 状态并打印继续命令；这不是最终成功状态。凭据和选择齐全后，bootstrap 会先做真实服务检查。如果返回 `service_check_failed`，说明 init 尚未运行，先修 API key / base_url / model / Ollama 后再重跑同一条安装或 bootstrap 命令。

健康状态可在安装完成后查看：

```bash
cd "$HOME/OpenBiliClaw"
docker compose ps
```

**手动 fallback**：高级排查、CI 或重复初始化时，可以绕过安装脚本直接运行 bootstrap；如果只是想重跑 init，也可以进容器执行 init。

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie

docker exec -it openbiliclaw-backend openbiliclaw init
```

AI agent 一句话部署时，`agent_bootstrap.py` 会在 auto-init 期间额外输出
`BOOTSTRAP_STATUS status=progress message=init_progress` 事件。Agent 应把
这些 `1/4`、`2/4`、`3/4`、`4/4` 和发现补货进度及时转述给用户，而不是等
最终 `init_complete` 后才汇报。

> 💡 **AI agent 一句话部署**：把下面这句粘到 Claude Code / Codex CLI / Cursor / OpenClaw：
> ```
> 请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端（务必用 Bash 的 curl 下载这个文档，不要用 WebFetch）
> ```
> 跨平台一致：Mac / Windows / Linux 上 AI 都按同一份文档执行。

## 配置

一行安装脚本会先在宿主机生成 `config.toml`，再同步到 Docker volume 的 `/app/runtime/config.toml`。配置要改时，优先重跑同一条安装 / bootstrap 命令；高级排查时可以直接编辑容器内文件。

```bash
# 重新进入 Docker bootstrap 选择流程
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie

# 高级排查：直接编辑容器内配置
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

安装脚本 / bootstrap 会按你选的 provider 自动写好 `[llm.<provider>]` 段。如果你想手动改，下面是对照表（按推荐顺序排列）：

| Provider | 是否要 Key | 适合谁 | 备注 |
|---|---|---|---|
| `deepseek` ★默认 | ✅ | 默认推荐 / 几乎免费 / 国内可直连 | ¥0.001/千 token，月费通常 ¥0.5-2，OpenAI 兼容协议。无 embedding 接口；embedding 需在 `[llm.embedding]` 独立配置 |
| `gemini` | ✅ | Google AI Studio 账户 | 免费档每天 1500 次够日常用；自带 embedding endpoint |
| `openai` | ✅ | 已有 OpenAI 账户 | base_url 留空 = `https://api.openai.com/v1`；自带 embedding endpoint |
| `claude` | ✅ | Anthropic 账户 | 高质量推理；无 embedding 接口，需独立配置 `[llm.embedding]` |
| `openrouter` | ✅ | 想一个 Key 跑多家模型 | 按调用计费；embedding 不可靠，建议独立配置 Ollama / Gemini / OpenAI embedding |
| `ollama` | ❌ | 完全离线 / 不要 Key / 16GB+ 内存 | CPU 推理首次响应慢（10-60s）。Docker 里 `[llm.ollama] base_url` 必须设成 `http://host.docker.internal:11434/v1` 才能从容器访问宿主机的 Ollama |
| OpenAI 协议兼容自建网关（高级） | ✅ 通常需要 | 自己有 vLLM / LMStudio / Azure / OneAPI / 团队 LLM 网关 | 写到 `[llm.openai_compatible]` 段，关键是显式 `base_url` 字段。**普通用户不要选这个** |

> 「OpenAI 官方」 ≠ 「OpenAI 协议兼容自建网关」：向导把这两个拆成独立菜单项，OpenAI 官方写 `[llm.openai]`，协议兼容网关写 `[llm.openai_compatible]`。
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
