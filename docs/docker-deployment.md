# Docker 部署指南

> 向量 GPU → CPU 自动回退仅适用于后端进程可见的 loopback Ollama 端点。Compose 默认通过 `http://ollama:11434` 访问独立容器，不自动更改其执行模式；该容器仍按部署的 GPU/CPU 配置运行。

[← 返回 README](../README.md)

> 🔒 **局域网访问安全（可选密码门禁）**：容器把后端暴露在 `8420`，同网段设备都能访问。需要为局域网 / 远程设备加登录密码时（本机与浏览器扩展仍免登录），设置环境变量 `OPENBILICLAW_API_AUTH_ENABLED=true` + `OPENBILICLAW_API_AUTH_PASSWORD=…`（或进容器跑 `openbiliclaw set-password`）。若手动套其他反向代理，记得配 `[api.auth].trusted_proxies` 或让代理自行鉴权；仓库自带的 Caddy HTTPS overlay 已把可信代理收紧到共享 loopback。详见 [`docs/modules/api-auth.md`](modules/api-auth.md)。

> 🛜 **应用内 Tailnet 首版不属于 Docker 镜像能力**：macOS / Windows 桌面安装包会内置
> `tsnet` helper，源码主机可用 Go 1.26.6 显式构建；当前 backend / prebuilt Docker 镜像不
> 携带 helper。不要只设置 `[tailnet].enabled=true` 并声称容器已加入 tailnet——本机容器 API
> 会继续运行，但远程入口会报告 helper 缺失。Docker 跨网络访问继续使用下文的 Caddy 公网
> HTTPS overlay 或自管 TLS。完整边界见[应用内 Tailnet](modules/tailnet.md)。

## 前置条件

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) V2（`docker compose` 命令）
- 一个 LLM API Key（OpenAI / Claude / Gemini / DeepSeek / OpenRouter / OrcaRouter / Requesty）—— **Embedding 用 compose 自带的 Ollama 不再需要单独申请**

### 自带 Ollama embedding sidecar（bge-m3 已烤进镜像,离线开箱即用）

`docker-compose.yml` 有一个 `ollama` 服务,对外暴露 `http://ollama:11434`,用 Docker 网络和后端互通。**bge-m3(~1.1GB)已在构建时烤进镜像 `openbiliclaw-ollama`**:容器启动时其 entrypoint 把烤好的模型播种进存储再 serve,**零网络拉取、离线可用**,对国内网络尤其友好。named volume `openbiliclaw_ollama` 持久化,重建容器不丢。

- 预构建路径(`docker-compose.prebuilt.yml`):直接拉 GHCR 上的 `openbiliclaw-ollama:<version>` 镜像。
- 源码构建路径(`docker-compose.yml`):`ollama` 服务用 `docker/ollama-bundled.Dockerfile` 本地构建(构建时联网拉一次 bge-m3 烤进镜像;之后运行离线)。
- 万一烤好的种子缺失/损坏,healthcheck 会**明确报 unhealthy**(不静默降级);设 `OPENBILICLAW_OLLAMA_ALLOW_PULL=1` 可显式允许运行时联网补拉。

后端容器首次启动时会自动把 `[llm.embedding] provider="ollama" model="bge-m3" base_url="http://ollama:11434/v1"` 写进生成的 `config.toml`,所以你**只需要给一个 chat 模型的 Key**,embedding 完全免费 + 离线可用。

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

## 多源接入前置：登录态来源使用宿主机浏览器

OpenBiliClaw 不代替用户登录——需要账号态的来源复用**你**当前浏览器的登录会话来读取你能看到的内容。Docker 部署后，这些来源仍需在装了扩展的同一个浏览器里登录；公开匿名来源会在各自条目中明确说明：

- **B 站**：浏览器里登录 https://www.bilibili.com 即可。v0.3.12+ 扩展会自动把 Cookie 推到容器里的 `/api/bilibili/cookie`，免 F12
- **小红书**：必须在浏览器里登录 https://www.xiaohongshu.com。后端不直接抓小红书，所有发现/详情都通过扩展以你的登录态执行——大部分任务(search / creator 抓取)在隐藏 tab 里跑;但 v0.3.22+ 起 `init` 期间的 **bootstrap_profile 滚动任务会临时打开一个前台 tab**(后台 tab 在小红书上无法触发瀑布流懒加载),会抢一次焦点 10-30 秒,完成后自动关闭。**不登录 = 完全没有小红书内容**
- **抖音**：如果要启用 `init --yes-douyin`、`fetch-douyin` 或 `discover --source douyin`，必须在装了扩展的宿主机浏览器里登录 https://www.douyin.com。后端不直接抓抖音；初始化只接收扩展回传的发布 / 收藏 / 点赞 / 关注信号。search / hot / feed discovery 走登录浏览器插件 DOM-first 链路：后台 tab 先打开抖音首页，再模拟真实 DOM 操作触发加载，并被动收集页面响应 / 渲染结果；Cookie 可用环境变量覆盖或由扩展同步到容器 volume 的 `data/douyin_cookie.json`。不登录或触发风控时会返回 0 条并让 init 继续。
- **YouTube**：如果要启用 `init --yes-youtube` 或 `fetch-youtube`，必须在装了扩展的宿主机浏览器里登录 https://www.youtube.com。后端不直接抓 YouTube；初始化只接收扩展回传的观看历史 / 订阅 / 点赞信号。不登录、页面布局变化或任务仍在后台跑时会返回 0 条并让 init 继续。
- **X**：如果要启用 X 初始化或 discovery，必须在宿主机浏览器里登录 https://x.com；扩展同步 `auth_token` + `ct0` 到容器 volume，后端用默认安装的 `twitter-cli` 做只读服务端重放。
- **知乎**：如果要启用知乎初始化或 discovery，必须在装了扩展的宿主机浏览器里登录 https://www.zhihu.com；事件、初始化和 search / hot / feed / creator / related discovery 都走插件任务。
- **Reddit**：如果要启用 Reddit 初始化或 discovery，必须在装了扩展的宿主机浏览器里登录 https://www.reddit.com，插件读取 saved / upvoted / subscribed，并把 `reddit_session` 同步到容器 volume 内的 rdt-cli credential store。日常 discovery 默认使用容器内随 OpenBiliClaw 安装的 `rdt-cli`；插件不可用时可在容器里手动运行 `rdt login`，未登录或命令后端不可用时会自动 fallback 到宿主机浏览器插件任务。
- **Bangumi**：匿名 search / ranked / 按日期 discovery 直接使用官方只读 API，无需登录、Cookie、token 或扩展 host permission。若要让公开收藏参与初始化画像，请在 `/setup/` 或设置页显式填写公开用户名；未填用户名时 Bangumi 不能作为唯一画像初始化来源。
- **GitHub**：匿名 `search / ranked / latest` 直接使用官方 REST API，只接收公开 repository，无需浏览器登录、Cookie、扩展 host permission 或任务桥。若要让 public starred repositories 参与初始化，请在 `/setup/` 或设置页填写公开用户名；PAT 可选且只用于提额和 `/user` 身份核验。容器不会读取宿主机 `GITHUB_TOKEN` / `GH_TOKEN`，如需 PAT，必须在 backend 服务显式传入固定专用变量 `OPENBILICLAW_GITHUB_TOKEN` 或通过 GitHub 凭据表单保存。
- **微博**：后端以项目自有 `httpx` client 和仅存内存的匿名 visitor 会话读取公开 search / hot-as-query-seed / creator 内容；公开路径无需登录。若在 guided init 选择微博，宿主机浏览器需登录 `m.weibo.cn`，扩展在隔离同源任务页只读收藏、关注和 mentions，并仅回传布尔登录态、uid 与规范化事件；Cookie 不进入容器，个人 bootstrap 当前为 init-only。
- **CDP 说明**：小红书、抖音、YouTube、知乎和 Reddit 插件 fallback 都走 Chrome 插件任务链路，不需要额外启动 CDP 调试 Chrome。`[sources.browser].cdp_url` 只保留给通用 Web / 自定义网页源的浏览器抓取场景。

详见 [配置参考 / sources.browser 段](modules/config.md#sourcesbrowser)。

## 快速开始

三种方式按省事程度排序。**无论选哪种，启动后端后都建议打开图形化引导页 `http://127.0.0.1:8420/setup/` 完成 AI 配置与前置检查**——它和桌面安装包是同一套向导：配置 LLM / embedding、选择初始化来源（B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do / Bangumi / V2EX / 微博 / GitHub）、真实校验前置条件。Bangumi 与 GitHub 的公开 discovery 无需登录；两者只有填写公开用户名后才能提供对应收藏/starred 画像信号，GitHub PAT 仍为可选。微博公开 discovery 无需登录，但选择微博个人初始化必须让宿主机浏览器登录微博并保持扩展在线。

> ⚠️ **容器内「开始初始化」按钮不可用**：Docker 运行时后端会拒绝网页发起的图形化初始化（`unsupported_runtime`），向导页会直接给出替代命令。在 `/setup/` 完成配置和前置检查后，初始化本身在宿主机执行：
>
> ```bash
> docker exec -it openbiliclaw-backend openbiliclaw init
> ```
>
> 方式 C 的一行安装脚本会自动跑这一步，无需手动执行。

### 方式 A：预构建镜像（最快，无需克隆源码）

GHCR 上有随后端版本发布的多架构镜像（linux/amd64 + linux/arm64），下载一个 compose 文件即可启动：

```bash
mkdir -p ~/openbiliclaw && cd ~/openbiliclaw
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
docker compose -f docker-compose.prebuilt.yml up -d
```

然后打开 `http://127.0.0.1:8420/setup/` 完成 AI 配置与前置检查，再运行 `docker exec -it openbiliclaw-backend openbiliclaw init` 完成初始化。想固定版本，把 compose 文件里的 `latest` 换成具体版本号（如 `0.3.152`）。

升级到最新版本：

```bash
docker compose -f docker-compose.prebuilt.yml pull
docker compose -f docker-compose.prebuilt.yml up -d
```

> 后端能识别自己跑在容器里（install mode `docker`）：设置页「版本与更新」会定期检查新版镜像并提示上面这两条命令，「立即检查」可用；容器内无法就地自更新，误点应用会以 `docker_install_mode` 明确拒绝。

### 方式 B：源码构建（想改代码 / 本地定制）

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
docker compose up -d --build
```

同样打开 `http://127.0.0.1:8420/setup/` 完成 AI 配置与前置检查，再运行 `docker exec -it openbiliclaw-backend openbiliclaw init` 完成初始化。更新：`git pull && docker compose up -d --build`（Dockerfile 已做依赖分层，依赖没变时重建只需数秒）。

### 方式 C：一行安装脚本 / AI agent 部署（终端向导 + 自动 init）

想在终端里一路问答式完成配置 + 自动 init，用一行安装脚本：

```bash
# macOS / Linux / WSL2
MODE=docker curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

```powershell
# Windows PowerShell + Docker Desktop
$env:MODE="docker"; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

安装脚本会克隆 / 更新仓库，然后调用 `agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie`。bootstrap 的 Docker 顺序是：

1. 在宿主机终端收集安装选择（LLM provider、embedding、B 站 Cookie 获取方式，以及小红书 / 抖音 / YouTube 初始化 opt-in；X / 知乎 / Reddit 请在 `/setup/` 引导页或后端设置页里开启）。Contract marker: human Docker one-line installer asks the same LLM provider first.
2. 写入宿主机 `config.toml`。
3. `docker compose up -d --build` 启动后端和 Ollama embedding sidecar。
4. 把确认后的 `config.toml` / Cookie 文件同步到容器 `/app/runtime`。
5. 等浏览器扩展把 B 站 Cookie 推到 `http://127.0.0.1:8420/api/bilibili/cookie`。
6. 在容器运行时里按顺序检查全局 LLM 实例链，并单独检查 embedding 服务。
7. 检查通过后自动运行 `openbiliclaw init`。

缺 LLM Key、缺 Cookie、缺来源确认时，bootstrap 会停在明确的 `needs_secrets` / `needs_decisions` 状态并打印继续命令；这不是最终成功状态。凭据和选择齐全后，bootstrap 会先做真实服务检查。如果返回 `service_check_failed`，说明 init 尚未运行，先修 API key / base_url / model / Ollama 后再重跑同一条安装或 bootstrap 命令。

AI agent 一句话部署时，`agent_bootstrap.py` 会在 auto-init 期间额外输出 `BOOTSTRAP_STATUS status=progress message=init_progress` 事件。Agent 应把这些 `1/4`、`2/4`、`3/4`、`4/4` 和发现补货进度及时转述给用户，而不是等最终 `init_complete` 后才汇报。

> 💡 **AI agent 一句话部署**：把下面这句粘到 Claude Code / Codex CLI / Cursor / OpenClaw：
> ```
> 请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端（务必用 Bash 的 curl 下载这个文档，不要用 WebFetch）
> ```
> 跨平台一致：Mac / Windows / Linux 上 AI 都按同一份文档执行。

### 启动后的通用说明

- 默认 embedding 是 `ollama` + `bge-m3`，Docker 里写成 compose 网络地址 `http://ollama:11434/v1`，指向随 compose 启动的 sidecar。如果你手动填了其他 embedding endpoint，不会被覆盖。
- **后端不再等 sidecar 拉完模型才启动**：`bge-m3` 首次下载（~568MB）期间后端已经可用，`/setup/` 的前置检查会显示 embedding 尚未就绪，拉取完成后自动通过。模型下载失败时 sidecar 守护进程仍在，重启 compose 会自动重试。
- B 站登录态推荐用浏览器扩展：扩展装在**宿主机浏览器**里，不在容器里。你登录 bilibili.com 后，扩展会把 Cookie 自动 POST 到 `127.0.0.1:8420` 的后端接口。
- 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / V2EX / 微博 / GitHub 都默认关闭，只有你在 `/setup/` 或设置页明确开启才会进入适用链路；需要个人浏览器信号的来源仍需在宿主机浏览器里装扩展并登录对应站点；Bangumi 与 GitHub 使用官方匿名只读 API，GitHub 不依赖扩展；微博公开请求匿名、个人 bootstrap 走扩展同源任务。镜像通过 pip 安装项目，X 的 `twitter-cli` 和 Reddit 的 `rdt-cli` 已内置。
- `[tailnet]` 也保持默认关闭。即使从宿主机挂载一个自行编译的 helper，容器网络 / 状态目录 / 生命周期仍不属于首版受支持路径；本指南不提供这种半自定义部署的可用性承诺。

### 可选公网域名自动 HTTPS（最简方案）

有公网 DNS 名称时，叠加仓库的 `docker-compose.https.yml` 即可让 Caddy 自动申请和续期
浏览器信任的证书，同时代理 REST、WebSocket、桌面 `/web` 和手机 `/m/`。需要 Docker
Compose `2.24.4+`，DNS A/AAAA 已指向服务器，并在防火墙 / 云安全组放行 TCP `80/443`。

预构建部署额外下载一次 overlay：

```bash
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.https.yml
export OPENBILICLAW_DOMAIN=obc.example.com  # 不带协议、端口或路径
docker compose -f docker-compose.prebuilt.yml -f docker-compose.https.yml up -d
```

源码部署：

```bash
export OPENBILICLAW_DOMAIN=obc.example.com
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

overlay 将宿主机 `8420` 收紧到 `127.0.0.1`，只公开 `80/443`；Caddy 与后端共享网络命名
空间，后端仅信任来自 `127.0.0.1` 的转发头。Caddy 在后端报告密码门禁启用前只在 loopback
等待，不会监听公网端口。设置 Web 密码；需要远程插件时再生成并开启设备密钥：

```bash
docker exec -it openbiliclaw-backend openbiliclaw set-password
docker exec -it openbiliclaw-backend openbiliclaw ext-key generate
docker exec -it openbiliclaw-backend openbiliclaw ext-key enable
docker restart openbiliclaw-backend
docker restart openbiliclaw-caddy
```

只用 PC / 手机 Web 时可省略 `ext-key` 两行，但不能省略 Web 密码和两次重启；CLI 写入的持久
配置需要后端重启加载，Caddy 重启后会重新附着该后端的共享网络命名空间。

PC 打开 `https://obc.example.com/web`，手机打开 `https://obc.example.com/m/`；插件选择
HTTPS、主机 `obc.example.com`、端口 `443`。完整前置条件、证书状态和排错见
[HTTPS 部署指南](https-deployment.md)。不要和下面的 `tls` profile 同时启用。

### 可选 LAN / self-managed HTTPS profile

源码 `docker-compose.yml` 提供默认不启动的 `tls` profile。**首次生成证书前**必须传入
远程客户端实际使用的 IP/hostname SAN：

```bash
export OPENBILICLAW_TLS_SAN_NAMES="192.168.1.20,openbiliclaw.lan"
export OPENBILICLAW_TLS_PORT=8443
docker compose --profile tls up -d --build
```

然后把 Web/扩展后端地址改为 `https://192.168.1.20:8443`，并按
[HTTPS 部署指南](https-deployment.md) 下载、核对并信任本地 CA。`OPENBILICLAW_TLS_SAN_NAMES`
会映射为代理容器的逗号分隔 `SAN_NAMES`；不设置时自动证书只有 localhost/127.0.0.1，
**不能声称局域网 IP 可用**。`OPENBILICLAW_TLS_PORT` 同时改变宿主机映射与容器监听。

证书持久化在 `openbiliclaw_certs` volume。改变 SAN 后，旧证书若不覆盖新值，容器会明确
启动失败且不会覆盖证书；先查看 `docker compose logs openbiliclaw-tls-proxy`，再按指南备份
并显式重签。该 profile 适合可信 LAN / 自管网络，不提供公网生产网关能力；原 `8420:8420`
HTTP 映射也不会自动关闭。`docker-compose.prebuilt.yml` 当前不包含此 profile。

健康状态随时可查：

```bash
docker compose ps          # 源码目录里；预构建方式加 -f docker-compose.prebuilt.yml
curl http://127.0.0.1:8420/api/health
```

**手动 fallback**：高级排查、CI 或重复初始化时，可以绕过安装脚本直接运行 bootstrap；如果只是想重跑 init，也可以进容器执行 init。

```bash
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie

docker exec -it openbiliclaw-backend openbiliclaw init
```

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
| `OPENBILICLAW_GITHUB_TOKEN` | 未设置 | 可选 GitHub PAT，只用于 GitHub 官方 REST 提额与 `/user` 身份核验；需显式加入 backend service environment，不读取 `GITHUB_TOKEN` / `GH_TOKEN` |

`OPENBILICLAW_TAILNET_AUTH_KEY` 不是首版 Docker 部署入口：镜像没有 helper，设置它不会让
容器自动获得 Tailnet 能力，也不应为了绕过该限制把 Auth Key 写进 compose 文件。需要
`OpenBiliClaw-mobile` Android / iOS 原生 App 私网访问时优先使用桌面 / 源码主机形态；该能力
不包括 Web / Linux / macOS / Windows Flutter 构建。Docker 使用已记录的 HTTPS 方案。

### LLM 配置

安装脚本 / bootstrap 会创建或复用一个 `[llm.instances.<id>]` 端点实例，并把它提升到 `default_chain` 首位；已有实例和后续故障切换顺序不会被删除。每个实例都独立保存 `provider_type`、Base URL、token 与 model，同一种类型可以配置多个渠道。如果你想手动改，下面是对照表（按推荐顺序排列）：

```toml
[llm]
routing_version = 2
default_chain = ["deepseek-official", "relay-backup"]

[llm.instances.deepseek-official]
name = "DeepSeek 官方"
provider_type = "deepseek"
enabled = true
api_key = "sk-..."
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/v1"

[llm.instances.relay-backup]
name = "备用中转"
provider_type = "openai_compatible"
enabled = true
api_key = "relay-..."
model = "deepseek-v4-flash"
base_url = "https://relay.example.com/v1"
```

| Provider | 是否要 Key | 适合谁 | 备注 |
|---|---|---|---|
| `deepseek` ★默认 | ✅ | 默认推荐 / 几乎免费 / 国内可直连 | ¥0.001/千 token，月费通常 ¥0.5-2，OpenAI 兼容协议。无 embedding 接口；embedding 需在 `[llm.embedding]` 独立配置 |
| `gemini` | ✅ | Google AI Studio 账户 | 免费档每天 1500 次够日常用；自带 embedding endpoint |
| `openai` | ✅ | 已有 OpenAI 账户 | base_url 留空 = `https://api.openai.com/v1`；自带 embedding endpoint |
| `claude` | ✅ | Anthropic 账户 | 高质量推理；无 embedding 接口，需独立配置 `[llm.embedding]` |
| `openrouter` | ✅ | 想一个 Key 跑多家模型 | 按调用计费；embedding 不可靠，建议独立配置 Ollama / Gemini / OpenAI embedding |
| `orcarouter` | ✅ | 一个 Key 跑 150+ 模型 + 网关级零信任安全 | 按调用计费；无 embedding 接口，需独立配置 `[llm.embedding]` |
| `requesty` | ✅ | 想一个 Key 跑多家模型 | 按调用计费；无 embedding 接口，需独立配置 `[llm.embedding]` |
| `api_route` | ✅ | 一个 Key 使用多家模型 | 按调用计费；embedding 需独立配置 `[llm.embedding]` |
| `cheaperinference` | ✅ | 一个 Key 使用多家模型 | 按调用计费；embedding 需独立配置 `[llm.embedding]` |
| `ollama` | ❌ | 完全离线 / 不要 Key / 16GB+ 内存 | CPU 推理首次响应慢（10-60s）。Docker 里的 Ollama chat 实例必须把 `base_url` 设成 `http://host.docker.internal:11434/v1` 才能访问宿主机 |
| OpenAI 协议兼容自建网关（高级） | ✅ 通常需要 | 自己有 vLLM / LMStudio / Azure / OneAPI / 团队 LLM 网关 | 使用 `provider_type="openai_compatible"`，必须显式配置 `base_url`。**普通用户不要选这个** |

> 「OpenAI 官方」 ≠ 「OpenAI 协议兼容自建网关」：向导把这两个拆成独立菜单项，并创建不同 `provider_type` 的实例；它们可以同时保留在 registry 和调用链中。
>
> 当 `--provider openai` 显式给出但 `--llm-base-url` 未给（或选了官方），bootstrap 会清空它选中的 OpenAI 实例的旧 gateway URL，让 SDK 回到 `https://api.openai.com/v1`；其他实例和链顺序不受影响。旧配置文件仍按 `[llm.openai]` 兼容处理。

**分模块链（可选）**：`[llm.routes.soul/discovery/recommendation/evaluation]` 默认 `inherit=true`；也可设 `inherit=false` 并提供有序 `chain`。典型用法是发现 / 评估优先便宜渠道，Soul 优先高质量渠道；自定义链耗尽后不会越界回到全局链。详见 [docs/modules/config.md](modules/config.md)。

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

桌面 Web 的 `.obcbackup` 迁移 API 坚持**后端观察到的真实 loopback + 同源**边界。默认 Docker bridge / 端口转发下，宿主机浏览器在容器内通常表现为 bridge gateway，因此配置页导入 / 导出可能按设计返回 `403 local_only`；LAN、Caddy 或 TLS 远程入口也不能用密码 / Bearer 绕过。Docker 跨机器迁移请继续使用停止容器后的 volume 冷拷贝，避免运行中的 SQLite 与 WAL 被拆开：

```bash
# 先停止后端写入（Ollama 可继续运行）
docker compose stop openbiliclaw-backend

# 备份整个数据目录（数据库、画像、Cookie、图片缓存等）
docker cp openbiliclaw-backend:/app/runtime/data ./backup-data

# 备份配置；config.local.toml 不存在时该命令会失败，可忽略
docker cp openbiliclaw-backend:/app/runtime/config.toml ./config-backup.toml
docker cp openbiliclaw-backend:/app/runtime/config.local.toml ./config-local-backup.toml

# 备份完成后重新启动
docker compose start openbiliclaw-backend
```

把冷备复制到另一台机器时，应在目标后端停止后写入对应 named volumes，并保留目标机自己的端口、网络、TLS、证书与 API auth 配置；不要把源机器的代理、证书或外部 CLI 登录误当作可移植用户数据。自定义部署只有在请求确实被后端安全解析为 loopback 时才能使用 `.obcbackup` 的四条 API（导出 / 导入 / 状态 / 取消）；它仍是未加密敏感包，虽会排除源机整段 `[api.auth]`，仍可能包含模型 / 来源 Key 和平台 Cookie。范围、环境变量提示与重启应用语义见[配置参考](modules/config.md#配置页跨机器迁移)。

### 彻底重置

删除所有 volumes 并重建，将清除所有数据（配置、画像、历史记录）：

```bash
docker compose down -v
docker compose up -d --build
```

## 网络与代理

### Clash 代理

容器启动时自动探测宿主机 Clash 代理（默认 `host.docker.internal:7897`）。发现可用代理，或容器环境中已显式设置 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 时，启动器会在用户没有明确选择的前提下设置 `OPENBILICLAW_NETWORK_MODE=system`，让海外客户端继承这些变量；本机回环与 `host.docker.internal` 仍加入 `NO_PROXY`。如需强制忽略容器代理，可显式设置 `OPENBILICLAW_NETWORK_MODE=direct`。

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
[llm.instances.ollama-host]
name = "宿主机 Ollama"
provider_type = "ollama"
enabled = true
model = "llama3"
base_url = "http://host.docker.internal:11434/v1"
```

### 本地 embedding provider（Ollama + bge-m3）

不想再多一份 embedding API Key、或想让系统在断网时仍能跑相似度计算，可以让 Ollama 同时承担 embedding 服务：

```bash
# 1. 在宿主机拉取 bge-m3（首次 ~568MB，CPU 即可跑）
ollama pull bge-m3

# 2. 在容器里写入 embedding 配置（推荐用 setup-embedding 命令）
docker exec -it openbiliclaw-backend openbiliclaw setup-embedding
```

或直接编辑 `config.toml` 的 `[llm.embedding]` 段：

```toml
[llm.embedding]
provider = "ollama"
model = "bge-m3"
base_url = "http://host.docker.internal:11434/v1"
```

注意：容器需要能访问宿主机的 Ollama；embedding 读取自己的 `[llm.embedding].base_url`，默认不会自动复用 chat 实例地址。只有显式开启 embedding 兼容 fallback 时，才可能借用首个启用的同类型 chat 实例。

## 常见问题

**Q: 容器启动后如何确认服务正常？**

```bash
curl http://127.0.0.1:8420/api/health
```

**Q: 如何更新到最新版本？**

预构建镜像方式：

```bash
docker compose -f docker-compose.prebuilt.yml pull
docker compose -f docker-compose.prebuilt.yml up -d
```

源码构建方式（依赖分层缓存，依赖没变时重建只需数秒）：

```bash
git pull
docker compose up -d --build
```

**Q: 启动时报 `container name "/openbiliclaw-backend" is already in use`？**

两个 compose 文件（源码构建的 `docker-compose.yml` 和预构建的 `docker-compose.prebuilt.yml`）管理的是同一组固定容器名。从一种方式切到另一种前，先在旧目录里 `docker compose down`（数据在 named volume 里，不会丢）；或直接移除残留容器后重试：

```bash
docker rm -f openbiliclaw-backend openbiliclaw-ollama
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
