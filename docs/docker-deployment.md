# Docker 部署指南

[← 返回 README](../README.md)

## 前置条件

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) V2（`docker compose` 命令）
- 一个 LLM API Key（OpenAI / Claude / Gemini / DeepSeek / OpenRouter）—— **Embedding 用 compose 自带的 Ollama 不再需要单独申请**

### v0.3.11+ 自带 Ollama embedding sidecar

`docker-compose.yml` 现在多了一个 `ollama` 服务：自动拉 `bge-m3` 模型，对外暴露 `http://ollama:11434`，用 Docker 网络和后端互通。第一次 `docker compose up -d --build` 会多花 2–4 分钟下载模型（~568MB），之后用 named volume `openbiliclaw_ollama` 持久化，重建容器不重拉。

后端容器首次启动时会自动把 `[llm.embedding] provider="ollama" model="bge-m3"` + `[llm.ollama] base_url="http://ollama:11434/v1"` 写进生成的 `config.toml`，所以你**只需要给一个 chat 模型的 Key**，embedding 完全免费 + 离线可用。

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
- **小红书反爬强建议**：开一个独立 profile 的 Chrome 用 `--remote-debugging-port=9222` 启动，里面手动登录小红书一次；后端 `[sources.browser] cdp_url = "http://host.docker.internal:9222"` 即可永久复用

详见 [配置参考 / sources.browser 段](modules/config.md#sourcesbrowser)。

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw

# 2. 启动容器（首次会构建镜像，后续仅增量）
docker compose up -d --build

# 3. 一键初始化：交互式向导 + B 站认证 + 画像生成 + 首轮发现
#    首次运行预计 2–5 分钟（拉历史 / LLM 调用 / 多策略发现）
docker exec -it openbiliclaw-backend openbiliclaw init

# 4. 健康状态：HEALTHCHECK 会让 docker compose ps 在容器真正可服务后才显示 healthy
docker compose ps
```

`init` 是 v0.3.20+ 的交互式向导，自动检测 `config.toml` 缺哪些配置并按需引导。每一步都有"不确定就回 1"的默认推荐：

1. **Phase 1 — 选 LLM 服务（7 项菜单）**：默认推荐 **DeepSeek**(默认模型 `deepseek-chat` / ¥0.001/千 token,几乎免费)。其它选项也都标了默认模型: OpenAI(`gpt-4o-mini`) / Gemini(`gemini-2.0-flash-exp`) / Claude(`claude-sonnet-4-5`) / OpenRouter(`openai/gpt-4o-mini`) / Ollama(`llama3`,16GB+ 内存,CPU 推理首次响应慢)。**OpenAI 协议兼容自建网关**（Azure / vLLM / LMStudio / OneAPI / 团队 LLM 网关）保留在菜单第 7 项的"高级"位置,需自填 Base URL + 模型名——**不要和选项 2 (OpenAI 官方) 混淆**。
2. **Phase 2 — 给所选服务填配置**：每个选项只问该选项需要的字段。Ollama 不问 Key（自动装 + 拉模型）；云厂商只问 API Key；自建网关问 Base URL + API Key + 模型名。
3. **Phase 3 — Embedding（向量化，独立提问，3 选 1 + 高级）**：
   - **1) 本地 Ollama bge-m3**（默认推荐 / 免费 / 离线 / 不消耗主 LLM 配额）
   - **2) 云端 Gemini embedding**（质量略高 / 跨语言更稳 / 免费档每天 1500 次够用）
   - **3) 跟随主 LLM**（最省事；主 LLM 是 Claude / DeepSeek / OpenRouter 时自动回退到选项 1）
   - 高级：自定义 OpenAI 兼容服务 / 指定其他 provider（默认折叠）
4. **Phase 4 — Per-module 覆盖（高级，默认跳过）**：可单独给 soul / discovery / recommendation / evaluation 指定不同模型。

> 💡 **v0.3.20+ Embedding 自动 fallback**（无需用户操心）：当主 LLM 是 Claude / DeepSeek / OpenRouter（这三家后端没 embedding 接口），bootstrap 会自动写 `[llm.embedding] provider="ollama" model="bge-m3"` 并预拉模型；运行时 registry 也有 `ollama → gemini → openai` 的 fallback 链兜底，不会再静默返回 None 让推荐管线坏掉。

接着 B 站登录态有 **2 种方式**（v0.3.12+）：

- **A.** 装浏览器扩展（推荐，零配置）—— [下载](https://github.com/whiteguo233/OpenBiliClaw/releases) 装好登录 B 站后，扩展会几秒内把 Cookie 自动推到 `http://127.0.0.1:8420/api/bilibili/cookie`。这条路向导会先退出，等同步好再 `docker exec -it openbiliclaw-backend openbiliclaw init` 完成 init
- **B.** 手动贴 Cookie —— 向导内附 F12 → Network 取 cookie 的 5 步教程

最后才进入真正的 init 阶段：拉历史、生成画像、跑首轮发现。整个流程会打印进度，不要以为卡住了——LLM 单次响应可能就要 10–30s。

> 🌸 **小红书数据是否加入(v0.3.27+)**:init 在 Docker 里跑时也会弹一个交互式问题——把小红书的收藏 / 点赞混进画像吗?
> - 想加就回 Y(默认),会有准备清单提示你装扩展 + 登录小红书。注意 Docker 模式下扩展是装在你**宿主机**的浏览器里的,后端在容器内通过 8420 端口拉数据
> - 不想加就回 N,只用 B 站数据建画像
> - 脚本化场景直接传 flag:`docker exec -it openbiliclaw-backend openbiliclaw init --no-xhs` 跳过 / `--yes-xhs` 强制启用
> - 想永久跳过:在 docker-compose.yml 里加 `OPENBILICLAW_NO_XHS=1` 环境变量

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
| `deepseek` ★默认 | ✅ | 默认推荐 / 几乎免费 / 国内可直连 | ¥0.001/千 token，月费通常 ¥0.5-2，OpenAI 兼容协议。无 embedding 接口（v0.3.20+ 自动 fallback 到本地 Ollama bge-m3） |
| `gemini` | ✅ | Google AI Studio 账户 | 免费档每天 1500 次够日常用；自带 embedding endpoint |
| `openai` | ✅ | 已有 OpenAI 账户 | base_url 留空 = `https://api.openai.com/v1`；自带 embedding endpoint |
| `claude` | ✅ | Anthropic 账户 | 高质量推理；无 embedding 接口（v0.3.20+ 自动 fallback） |
| `openrouter` | ✅ | 想一个 Key 跑多家模型 | 按调用计费；embedding 不可靠（v0.3.20+ 自动 fallback） |
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

# 可选：启用本地 Ollama 作为 embedding 兜底（v0.3.3+ 真实生效）
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

### 本地 embedding 兜底（Ollama + bge-m3）

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
```

注意：容器需要能访问宿主机的 Ollama，确认 `[llm.ollama] base_url` 已经设到 `http://host.docker.internal:11434`，embedding 会自动复用同一连接。

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
