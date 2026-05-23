# 配置参考

> `config.toml` 所有配置段落详解。

## 快速开始

```bash
cp config.example.toml config.toml
# 编辑 config.toml，填入 LLM API Key；或对 OpenAI 实验性启用 Codex OAuth
```

## 配置段落

### `[general]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `language` | string | `"zh"` | Agent 输出语言（`zh` / `en`） |
| `data_dir` | string | `"data"` | 数据目录（记忆、Cookie、数据库） |

### `[api]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `host` | string | `"0.0.0.0"` | 后端 API 监听地址。默认绑定所有网卡，方便同局域网手机访问 `/m/`；如只允许本机访问可改为 `"127.0.0.1"` |
| `port` | int | `8420` | 后端 API 监听端口 |

`openbiliclaw start` 默认读取这里的 host / port。浏览器插件的手机二维码入口会在后端地址仍是 loopback 时读取 `/api/health.lan_ip`，用局域网 IP 生成 `/m/` 二维码；但后端仍需要绑定 `0.0.0.0`，手机才能连上。

### `[llm]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `default_provider` | string | `"openai"` | 默认 Provider：`openai` / `claude` / `gemini` / `deepseek` / `ollama` / `openrouter` / `openai_compatible` |
| `concurrency` | int | `3` | 全局 LLM 请求并发上限。所有 `LLMService` 调用共享这个优先级队列；可在插件设置页「模型」tab 调整，合法范围为 `1..16` |
| `fallback_enabled` | bool | `false` | 旧兼容开关；当前实际 fallback 只在 `fallback_provider` 非空时发生 |
| `fallback_provider` | string | `""` | 第二个备选 Provider。留空 = 不 fallback；非空时只按 `default_provider → fallback_provider` 尝试，不再自动遍历其它 provider |

### `[llm.openai]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | API Key（default_provider=openai 时必填，OpenAI 兼容服务也填这里） |
| `model` | string | `"gpt-5-nano"` | 模型名称（按 `base_url` 后端实际部署的模型填，例如 vLLM 上是 `meta-llama/Llama-3.1-70B-Instruct`） |
| `base_url` | string | `""` | 留空使用 OpenAI 官方 `https://api.openai.com/v1`；指向任何 OpenAI 兼容服务的 `/v1` 端点：Azure OpenAI / vLLM / LMStudio / OneAPI / Cloudflare AI Gateway / 自建 LLM 网关 |
| `auth_mode` | string | `""` | 认证模式：`""` / `"api_key"` 使用 `api_key`；`"codex_oauth"` 使用 `openbiliclaw login codex` 导入的 Codex CLI ChatGPT OAuth 凭据 |

> **「openai」是协议家族，不是厂商。** v0.3.5 起 `init` 向导会显式说明这一点。任何兼容 `POST /v1/chat/completions` 的服务都填到这一段，区别只在 `base_url`。
> 例如：
> - Azure OpenAI → `base_url = "https://your-resource.openai.azure.com/openai/deployments/your-deployment"`
> - 本地 vLLM → `base_url = "http://localhost:8000/v1"`，`api_key` 任填或留空
> - OneAPI 网关 → `base_url = "https://your-oneapi.example.com/v1"`

> `auth_mode = "codex_oauth"` 是实验性 / 非官方路径：OpenAI 官方 API 认证仍以 Platform API key 为稳定入口。启用前先运行 `openbiliclaw login codex`，OpenBiliClaw 会从官方 Codex CLI 登录态导入 token 到 `~/.openbiliclaw/codex_auth.json`。该模式下 `api_key` 会被忽略，并且 `base_url` 只能留空或指向 `https://api.openai.com`，避免把 ChatGPT OAuth token 发给第三方 OpenAI-compatible 代理。自定义代理仍请使用 `api_key` 模式或 `[llm.openai_compatible]`。

### `[llm.claude]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | Anthropic API Key（default_provider=claude 时必填） |
| `model` | string | `"claude-sonnet-4-6"` | 模型名称 |

### `[llm.gemini]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | Gemini API Key（default_provider=gemini 时，若未填写则回退读取 `GOOGLE_API_KEY` / `GEMINI_API_KEY`） |
| `model` | string | `"gemini-2.5-flash"` | Gemini 模型名称 |

> Gemini provider 按官方 quickstart 走 `google-genai` SDK 的 Gemini Developer API，不是 Vertex AI。

### `[llm.deepseek]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | DeepSeek API Key |
| `model` | string | `"deepseek-v4-flash"` | 模型名称（可选 `deepseek-v4-pro`；旧 `deepseek-chat` / `deepseek-reasoner` 将于 2026/07/24 弃用） |
| `base_url` | string | `"https://api.deepseek.com"` | API 地址 |
| `reasoning_effort` | string | `"max"` | DeepSeek v4 thinking 模式：`""` 关闭，`"high"` / `"max"` 开启 |

### `[llm.ollama]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `model` | string | `"qwen2.5:7b"` | 本地模型名称 |
| `base_url` | string | `"http://localhost:11434/v1"` | Ollama OpenAI-compatible `/v1` 服务地址 |

> Ollama 不需要 API Key，适合本地开发测试。

### `[llm.openrouter]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | OpenRouter API Key（default_provider=openrouter 时必填） |
| `model` | string | `"openai/gpt-5-nano"` | OpenRouter 模型名称 |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API 地址 |
| `http_referer` | string | `""` | 可选的 `HTTP-Referer` 请求头 |
| `x_title` | string | `"OpenBiliClaw"` | 可选的 `X-Title` 请求头 |

> `http_referer` 和 `x_title` 都是可选项；留空时不会阻止请求发送。

### `[llm.openai_compatible]` (v0.3.32+)

通用 OpenAI 协议兼容 provider，用于接入 Groq / Together / Azure OpenAI / vLLM / 自建等任何兼容 `/v1/chat/completions` 的服务。**与 `[llm.openai]` 完全独立**：cost 统计、retry 计数、provider 名都各自一份，可以同时在一个 backend 里跑两套（例：chat 走真 OpenAI 跑 `gpt-5-nano`，draft 任务挂 Groq 跑 Llama 加速）。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | 上游服务的 API Key（default_provider=openai_compatible 时必填） |
| `model` | string | `""` | 上游服务的模型名（如 `llama-3.1-70b-versatile`、`Qwen/Qwen2.5-72B-Instruct-Turbo`、Azure 部署名等） |
| `base_url` | string | `""` | **必填**。上游服务的 OpenAI 协议端点（缺失时 `_collect_config_issues` 会报 `llm.openai_compatible.base_url`，registry 拒绝注册） |

常见示例：

| 服务 | base_url | model 示例 |
|------|----------|-----------|
| Groq | `https://api.groq.com/openai/v1` | `llama-3.1-70b-versatile` |
| Together | `https://api.together.xyz/v1` | `Qwen/Qwen2.5-72B-Instruct-Turbo` |
| Azure OpenAI | `https://<resource>.openai.azure.com/openai/deployments/<deployment>` | `(matches deployment name)` |
| vLLM 自建 | `http://localhost:8000/v1` | `(vLLM 加载的模型名)` |

`[llm.embedding].provider` 也接受 `openai_compatible`：多数 OpenAI-compat 后端（Together / vLLM / Azure）都暴露 `/v1/embeddings`，可以直接挂上来，与 chat 用同一组 base_url 也行（互相独立的 provider 实例）。

### `[llm.embedding]`

Embedding 服务用于多个语义任务：discovery 内容兴趣预过滤、recommendation 跨主题去重、PoolCurator 反馈相似度判定、interest probe 主题归类。

**v0.3.32+ 起，本段拥有独立的 `api_key` / `base_url`，与 `[llm].default_provider` 完全解耦。** 不再被迫为「embedding 用 OpenAI 但 chat 用 DeepSeek」这种场景在两处填同一组凭据。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `provider` | string | `""` | 留空 = 不启用 embedding；不会跟随 `[llm].default_provider`。可填 `"openai"` / `"gemini"` / `"ollama"` / `"openai_compatible"`。Claude / DeepSeek / OpenRouter 没有 embedding 接口 |
| `model` | string | `"gemini-embedding-001"` | embedding 模型名；按 provider 自动填合理默认：`gemini → gemini-embedding-001` / `openai → text-embedding-3-small` / `ollama → bge-m3` |
| `api_key` | string | `""` | v0.3.32+ embedding 专属 API Key。默认不会借用 `[llm.<provider>].api_key`；只有 `fallback_enabled=true` 时才允许旧配置借用 chat-side 凭据并打一条 WARNING。Ollama 不需要 |
| `base_url` | string | `""` | v0.3.32+ embedding 专属 base URL。留空使用 provider 默认值（OpenAI → `api.openai.com/v1`、Ollama → `localhost:11434/v1`）。Gemini SDK 忽略此字段 |
| `similarity_threshold` | float | `0.82` | 余弦相似度阈值，超过即视为"同主题" |
| `fallback_enabled` | bool | `false` | 旧兼容开关；插件设置页选择 `fallback_provider` 时会同步写成 `true`，用于允许借用对应 chat provider 凭据 |
| `fallback_provider` | string | `""` | 第二个 embedding 备选 Provider。留空 = 不 fallback；可填 `openai` / `gemini` / `ollama` / `openai_compatible`，不会再自动走 `ollama → gemini → openai` 链 |

#### 启用本地 Ollama embedding（v0.3.0+，**v0.3.3 起真实生效**）

> ⚠️ **如果你装的是 v0.3.0~v0.3.2**：`setup-embedding` 当时虽然写了 `[llm.embedding] provider="ollama"`，但 LLM 注册表静默回退到 default provider，embedding 实际仍走 Gemini。
> **升级到 v0.3.3+ 重启 backend** 即可生效，不需要改配置；当前版本可再跑一次 `openbiliclaw setup-embedding`，向导会把 provider / model / base_url 写入独立的 `[llm.embedding]` 段。

不想再多一份 embedding API Key、或要支持离线，可以用 Ollama + bge-m3 跑本地 embedding：

```bash
# 1. 装 Ollama（一次性）
# Mac
brew install ollama && ollama serve &
# Windows: 从 https://ollama.com/download 下载安装包
# Linux
curl -fsSL https://ollama.com/install.sh | sh && ollama serve &

# 2. 跑向导自动拉模型 + 写配置
openbiliclaw setup-embedding
```

或手动改 `config.toml`：

```toml
[llm.embedding]
provider = "ollama"
model = "bge-m3"
```

CPU 即可跑（~100-200ms/次），跨 Mac / Win / Linux 一致。

### `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]`

可选的 **per-module** LLM 覆盖（v0.3.5 起 `init` 向导 Phase 4 会问；也可以手填或通过 `agent_bootstrap.py --module-override` 传入）。每段同结构，留空 = 跟随 `[llm].default_provider`：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `provider` | string | `""` | 留空跟随 `default_provider`；填 `openai` / `claude` / `gemini` / `deepseek` / `ollama` / `openrouter` / `openai_compatible` |
| `model` | string | `""` | 留空跟随 `[llm.<provider>].model`；填具体模型名覆盖 |

四个模块在管线里的位置：

| 段 | 用途 | 典型选型 |
|---|---|---|
| `[llm.soul]` | 灵魂画像生成（5 层 Event → Soul），稳定性优先 | 高质量模型，例如 Claude Sonnet / GPT-4o / Gemini 2.5 Pro |
| `[llm.discovery]` | 关键词生成、候选评估，调用频次最高 | 廉价模型，例如 DeepSeek Chat / GPT-4o-mini / Gemini Flash |
| `[llm.recommendation]` | 朋友式解释生成，影响最终用户体感 | 平衡型，例如 Claude Haiku / GPT-4o-mini |
| `[llm.evaluation]` | 池子打分、相关度评估，高频后台调用 | 廉价模型 |

运行时路由（v0.3.75+）：

- `LLMService` 不再用 caller 第一段朴素判断模块，而是内置 caller bucket。例：`soul.*` → soul，`discovery.search/explore/trending/related.*`、`yt_search.*`、`sources.xhs.*` → discovery，`recommendation.delight_score`、`recommendation.evaluate_batch`、`discovery.evaluate*`、`eval.*` → evaluation，其他 `recommendation.*` → recommendation。
- `provider` 非空时走 `LLMRegistry.complete_provider(provider, ...)` 精确调用该 provider，不走 fallback 链；该 provider 被 rate-limit 或返回错误时会直接报错，避免用户指定贵模型给画像却被静默改用默认便宜模型。
- `model` 非空时作为单次调用的 `model=` 参数传给 provider，不会修改 provider 实例的默认模型；`provider` 留空但 `model` 非空时，使用当前 default provider + 该 per-call model。
- `provider` 拼错或目标 provider 不是 chat-capable（例如 embedding-only Ollama）时，不会让保存配置失败；运行时会按模块 + provider 只 INFO 一次，然后降级到默认 provider 链。

例：发现/评估走 DeepSeek，画像走 Claude：

```toml
[llm.soul]
provider = "claude"
model    = "claude-sonnet-4-5-20250929"

[llm.discovery]
provider = "deepseek"
model    = "deepseek-v4-flash"

[llm.evaluation]
provider = "deepseek"
model    = "deepseek-v4-flash"
```

> 通过 `agent_bootstrap.py` 的命令行写入：
> ```bash
> python3 scripts/agent_bootstrap.py \
>   --module-override soul=claude:claude-sonnet-4-5-20250929 \
>   --module-override discovery=deepseek:deepseek-v4-flash \
>   --module-override evaluation=deepseek:deepseek-v4-flash
> ```

### `[bilibili]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auth_method` | string | `"cookie"` | 认证方式：`cookie` / `qrcode` / `none` |
| `cookie` | string | `""` | 浏览器 Cookie（推荐通过 `auth login` 命令设置） |

### `[bilibili.browser]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `executable` | string | `""` | agent-browser 路径（留空使用全局安装） |
| `headed` | bool | `false` | 是否显示浏览器窗口（调试用） |

> 运行时行为：
> 如果 `bilibili.cookie` 留空，CLI 命令和本地 API 服务会自动回退到 `auth login` 保存的 `data/bilibili_cookie.json`。
> 只有在你想显式覆盖本地登录态时，才需要把 cookie 直接写进 `config.toml`。

### `[sources.browser]`

通用 Web / 自定义网页源使用的浏览器配置。与 `bilibili.browser` 独立 —— 后者控制 B 站登录 / 扫码用的 agent-browser CLI。

> 当前小红书和抖音稳定链路都走 Chrome 插件任务，不依赖 `[sources.browser].cdp_url`。这里的 CDP 配置主要用于没有专用插件 / API adapter 的网页源。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `cdp_url` | string | `""` | 预启动 Chrome 的 CDP 端点，例如 `"http://localhost:9222"`。设置后优先走 Playwright `connect_over_cdp` 复用你手动登录的会话；留空则回退到 agent-browser（无登录态） |
| `headed` | bool | `false` | agent-browser 回退路径是否显示窗口 |

> **仅在通用 Web / 自定义网页源需要登录态时使用 CDP。** 普通 B 站 / 小红书 / 抖音使用路径不需要配置这里。
>
> 启动步骤：
> 1. 安装 Playwright：`pip install 'openbiliclaw[browser]'`
> 2. 启一个独立 profile 的 Chrome：
>    ```bash
>    open -na "Google Chrome" --args \
>      --remote-debugging-port=9222 \
>      --user-data-dir="$HOME/.openbiliclaw-chrome"
>    ```
> 3. 在这个 Chrome 里手动登录目标网页源，profile 会记住，后续复用
> 4. 在 `config.toml` 里填 `cdp_url = "http://localhost:9222"`
>
> `127.0.0.1` 与 `localhost` 并非总是等价：macOS 上 Chrome 常只绑定 IPv6 `::1:9222`，而 Python urllib 默认走 IPv4。用 `localhost` 最稳妥（`getaddrinfo` 会同时尝试两边）。

### `[sources.bilibili]`

Bilibili discovery 的平台级开关。B 站账号登录 / Cookie 获取仍由 `[bilibili.auth]` 和 `[bilibili.browser]` 控制；本段只决定后台候选池是否继续调度 B 站 `search` / `related_chain` / `trending` / `explore` 策略。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用 Bilibili discovery。设为 `false` 后，B 站候选池占比会从运行时有效配比中剔除，已保存的 `scheduler.pool_source_shares.bilibili` 数值仍保留，重新开启后继续使用 |

### `[sources.xiaohongshu]`

小红书专用配置。内容发现和元数据提取都由浏览器扩展在真实登录态下完成：被动收集、后台标签页搜索和创作者订阅都会通过扩展任务桥回写后端。主后端不主动爬取小红书，也不再依赖 `sidecar_url`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否启用小红书 discovery 和 init bootstrap；默认关闭，`init` 选 Yes、`--yes-xhs` 或插件设置页打开后才会写回 `true` |
| `daily_search_budget` | int | `30` | 每天后端允许入队的 Soul 驱动搜索任务数上限。由 `XhsTaskProducer`（`runtime/xhs_producer.py`）在持续刷新循环里使用，搭配内部 4h 最小间隔避免反复抢配额 |
| `daily_creator_budget` | int | `10` | 每天每位订阅创作者的抓取任务上限 |
| `task_interval_seconds` | int | `45` | 扩展分发器两次任务之间的最小间隔（秒） |

> **安全设计要点：** 后端从不直接调用小红书搜索 / Feed API。所有"主动发现"（关键词搜索、创作者主页浏览）都在用户自己的浏览器中以后台标签页形式执行，由扩展代理完成。被动发现则利用用户正常浏览时已经加载的卡片 URL，零额外请求。

### `[sources.douyin]`

抖音专用 discovery 配置。初始化画像仍由浏览器扩展执行；本段控制 `openbiliclaw discover --source douyin` / `discover-douyin` 的内容发现。Cookie 不写进 `config.toml`：`cookie_env` 指向的环境变量优先；未设置时，后端读取浏览器扩展通过 `/api/sources/dy/cookie` 同步到 `data/douyin_cookie.json` 的值。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否启用抖音 discovery。默认关闭，必须显式 opt-in |
| `mode` | string | `"direct"` | 当前仅支持 `direct`，保留字段用于后续 extension/direct 切换 |
| `cookie_env` | string | `"OPENBILICLAW_DOUYIN_COOKIE"` | douyin.com Cookie header 的环境变量覆盖名；为空时使用扩展同步文件 |
| `daily_search_budget` | int | `30` | 每日搜索插件任务预算，限制 `dy_tasks(type="search")` 入队次数 |
| `daily_hot_budget` | int | `5` | 每日热点插件任务预算，限制 `dy_tasks(type="hot")` 入队次数；runtime 抖音缺口较大时会把有效预算临时抬高到 `max(配置值, min(缺口, 60))`，手动 CLI 仍使用配置值 |
| `daily_feed_budget` | int | `30` | 每日首页推荐流插件任务预算，限制 `dy_tasks(type="feed")` 入队次数 |
| `request_interval_seconds` | int | `2` | direct 请求的建议最小间隔；当前插件签名链路主要由任务预算和 runtime producer 节流保护 |

当前 `search` 子来源优先使用浏览器插件的 logged-in page + acrawler 签名桥，并以 `dy-plugin-search` 进入 discovery；`hot` 子来源优先使用插件 hot-related 链路，并以 `dy-plugin-hot-related` 进入 discovery；`feed` 子来源使用同一插件签名桥请求 `/aweme/v1/web/tab/feed/`，并以 `dy-plugin-feed` 进入 discovery。插件任务空 / 失败时 search / hot 会分别回退 direct-cookie search / hot，feed 也保留 direct-cookie 诊断 fallback；因 daemon 重启或插件未及时消费而被清理的 `failed/stale_pending` 任务不消耗每日预算。runtime 大缺口补池会优先 search / hot，feed 只用于小缺口补零散名额。`msToken` 如果存在会随 Cookie 一起使用，但扩展同步不再硬依赖它。若 Cookie 过期、签名被拒绝或插件未在线，命令可能返回 0 条并提示检查登录态。

### `[sources.youtube]`

YouTube discovery 配置。初始化画像由浏览器扩展读取观看历史 / 订阅 / 点赞，也可通过 `import-youtube` 导入 Google Takeout；steady-state discovery 由后端 `YoutubeDiscoveryProducer` 独立调度 `yt_search` / `yt_trending` / `yt_channel` 三个策略。这里的预算是每日执行预算，按实际 strategy work 写入 SQLite ledger；不依赖 `content_cache` 统计，也不是扩展任务队列的每日入队数。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 YouTube 参与候选池配比和后台 discovery；`init --yes-youtube` 会写回 `true`，`--no-youtube` 或 `OPENBILICLAW_NO_YOUTUBE=1` 会写回 `false` |
| `daily_search_budget` | int | `6` | `yt_search` 每天最多生成 / 执行的 YouTube 搜索 query 数，对应 `YoutubeSearchStrategy.queries_per_run` 的日预算上限 |
| `daily_trending_budget` | int | `50` | `yt_trending` 每天最多拉取的热门候选数，对应 `YoutubeTrendingStrategy.fetch_limit` 的日预算上限 |
| `daily_channel_budget` | int | `10` | `yt_channel` 每天最多选择的订阅频道数，对应 `YoutubeChannelStrategy.max_channels` 的日预算上限 |
| `request_interval_seconds` | int | `2` | 预留的 YouTube 请求间隔配置；当前策略主要由单轮预算和 runtime 补池节奏控制 |
| `min_interval_minutes` | int | `60` | `YoutubeDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

### `[scheduler]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 后台 LLM / embedding 工作总开关；插件设置页显示为「停止后台 LLM 请求」。关闭后 runtime 的刷新、补池预计算、账户同步、猜测兴趣和主动推送等 daemon-owned 后台任务都会跳过；手动 CLI / API 请求仍按显式操作执行。若候选池为空，推荐页可能暂时没有内容 |
| `pause_on_extension_disconnect` | bool | `false` | 开启后，daemon-owned 后台 LLM / embedding 工作只在浏览器插件有 `/api/runtime-stream` 连接、或刚断开仍处于宽限窗口内时运行；离线期间不会自动补新内容 |
| `extension_disconnect_grace_seconds` | int | `90` | 插件最后一个 `runtime-stream` 连接断开后的宽限秒数；小于等于 0 或无法解析时回退到 `90` |
| `discovery_cron` | string | `"0 */8 * * *"` | 兼容旧配置的保留字段；当前 runtime 不消费这个 cron，发现补池由轮询、候选池缺口、行为阈值和下方策略间隔驱动 |
| `pool_target_count` | int | `300` | discovery pool 的硬上限，同时作为期望保有的可换候选数量；允许范围 `1..600`。pool < 目标时会持续补货；pool ≥ 目标时任何 refresh（含 `force_refresh`）都直接返回 `pool_at_cap` 不再 discover；pool > 目标时会先按 `relevance_score` / 时间 / `explore` 优先顺序把溢出部分降为 `suppressed` |
| `account_sync_interval_hours` | int | `6` | 账户侧长期信号同步间隔；运行时会低频拉取 history / favorites / following |
| `refresh_check_interval_seconds` | int | `60` | `ContinuousRefreshController` 主循环轮询间隔；小于 `15` 或无法解析时回退默认值 |
| `signal_event_threshold` | int | `6` | 累计多少条新行为事件后触发 `search + related_chain` 补池；小于 `1` 时回退默认值 |
| `trending_refresh_hours` | int | `3` | `trending` 策略的最小刷新间隔；小于 `1` 时回退默认值 |
| `explore_refresh_hours` | int | `12` | `explore` 策略的最小刷新间隔；小于 `1` 时回退默认值 |
| `discovery_limit` | int | `30` | 单轮 discovery wave 的候选上限；允许范围 `1..60` |
| `proactive_push_interval_seconds` | int | `120` | 主动推荐 / probe 推送循环间隔；小于 `30` 时回退默认值 |
| `speculator_idle_interval_minutes` | int | `30` | `ProfileUpdatePipeline` 空闲时检查猜测兴趣生命周期的间隔；小于 `5` 时回退默认值 |
| `speculation_interval_minutes` | int | `10` | 猜测兴趣推测的运行间隔（分钟） |
| `speculation_ttl_days` | int | `3` | 猜测兴趣的默认存活天数 |
| `speculation_cooldown_days` | int | `7` | 猜测兴趣被否定后的冷却天数 |
| `speculation_confirmation_threshold` | int | `3` | 需要多少次正向信号确认猜测兴趣 |
| `speculation_max_active` | int | `5` | 最多同时活跃的猜测兴趣数 |
| `speculation_max_primary_interests` | int | `15` | 主要兴趣域的最大数量 |
| `speculation_max_secondary_interests` | int | `60` | 次要兴趣域的最大数量 |
| `auto_update_enabled` | bool | `false` | 是否启用自动检查并应用新版本；默认关闭，避免本地开发或 release 漂移时自动重启 |
| `auto_update_check_interval_hours` | int | `6` | 自动更新检查间隔（小时） |

> 运行时护栏：
> 即使 `pool_target_count` 设得较高，单次 refresh 里的 discover wave 也由 `discovery_limit` 控制（默认 `30`，最大 `60`），避免一次性把全部缺口都打满。
> `pause_on_extension_disconnect` 只约束后端 daemon 自己发起的后台 LLM / embedding 工作；用户手动点击刷新、CLI 显式命令、配置保存和普通读取接口不因为插件离线而被拦截。`runtime-stream` 连接断开由后端 receive-side detector 记录，浏览器 idle disconnect 后不会让 presence 状态卡住。

### `[scheduler.pool_source_shares]`

候选池按平台族做保底配比，默认保存的 share 仍是 `bilibili:xiaohongshu:douyin:youtube = 8:1:1:1`。关闭的平台会保留配置值但在运行时从有效配比中剔除，剩余平台重新归一化吃满 `pool_target_count`；默认安装里小红书 / 抖音 / YouTube 都关闭，所以默认有效配比只有 Bilibili。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `bilibili` | int | `8` | B 站平台族占比；`search` / `related_chain` / `trending` / `explore` 四个策略统一计入该族 |
| `xiaohongshu` | int | `1` | 小红书平台族占比；`xhs-extension-*` 原始来源统一计入该族 |
| `douyin` | int | `1` | 抖音平台族占比；`dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 等统一计入该族 |
| `youtube` | int | `1` | YouTube 平台族占比；`yt_search` / `yt_trending` / `yt_channel` 统一计入该族 |

运行时会把同一份目标传给 `reactivate_under_quota_pool_sources()`、`trim_pool_source_overflow()` 和 `trim_pool_to_target_count()`：小平台低于目标时，会优先保护 / 复活它们的候选；任一平台族高于目标时，会先压回配额内，避免它占用其他平台的保留容量；B 站低于目标且 `[sources.bilibili].enabled=true` 时，仍由四个 B 站 discovery 策略并行补货；抖音低于目标且 `[sources.douyin].enabled=true` 时，后台 `DouyinDiscoveryProducer` 会通过 `DouyinDiscoveryService(cache=True)` 触发 search / hot / feed 补池；YouTube 低于目标且 `[sources.youtube].enabled=true` 时，后台 `YoutubeDiscoveryProducer` 会在独立 loop 中触发 `yt_search` / `yt_trending` / `yt_channel`，主 refresh replenishment plan 不再 inline 调度 YouTube。

`openbiliclaw init` 会根据用户是否接入小红书 / 抖音 / YouTube 写回对应 `enabled`；Bilibili 默认启用，也可在插件设置页或 `config.toml` 里手动关闭。交互式初始化在采集完各平台事件后，会按事件量给出一组推荐比例，用户可确认使用或手动输入。插件设置页也可开关四个平台、编辑四个平台占比，并通过 `/api/config/source-share-suggestion` 按已有事件重新生成建议值；GET 使用已保存配置，POST 可接收设置页当前尚未保存的 `enabled_sources` / `configured_shares`。

### `[storage]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `db_path` | string | `"data/openbiliclaw.db"` | SQLite 数据库路径 |

### `[soul.preference]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `satisfaction_filter_enabled` | bool | `true` | v0.3.x 事件满意度信号：默认开启。偏好分析会在构 prompt 前忽略 `quick_exit` 等被动 negative 事件，保留 positive / neutral / unknown 上下文；`feedback_type=dislike` 或 `reaction=thumbs_down` 的显式负反馈会继续进入分析器，只能作为 `disliked_topics` / 避让证据，不能提取为正向 `interests` |

### `[logging]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `level` | string | `"INFO"` | 控制台日志级别 |
| `file_level` | string | `"DEBUG"` | 文件日志级别 |
| `directory` | string | `"logs"` | 日志目录 |
| `filename` | string | `"openbiliclaw.log"` | 日志文件名 |
| `max_file_size_mb` | int | `100` | 单个日志文件上限（MB），超过即轮转；`0` 禁用轮转 |
| `backup_count` | int | `1` | 保留的历史日志份数；设为 `1` 时总占用封顶 `max_file_size_mb * 2` MB |
| `aggregate_budget_mb` | int | `500` | `logs/` 目录里非托管日志文件的总预算；启动或手动清理时会从最老文件开始删除到预算内，`0` 关闭 |
| `unmanaged_truncate_mb` | int | `200` | 单个非托管日志文件超过该大小时启动时截断到 0，`0` 关闭 |
| `unmanaged_max_age_days` | int | `30` | 非托管日志文件超过该天数时启动时删除，`0` 关闭 |

启动时如果现有日志文件已经超过 `max_file_size_mb`，会被重命名为 `<filename>.1`（覆盖旧的 `.1`）并重新开始写入——这样意外堆积的大日志不会在下次启动时继续增长。运行时到达上限则由 `RotatingFileHandler` 正常轮转：`app.log` → `app.log.1` → `app.log.2` → …，超出 `backup_count` 的旧份自动丢弃。

文件日志使用标准 formatter 写入异常 traceback；`RotatingFileHandler`、plain `FileHandler` 和 `/api/config` 热重载异常路径都有回归测试覆盖，避免 Windows / 非轮转配置下只留下错误摘要而丢失 stack trace。

`GET /api/config` 会额外返回只读字段 `logging.file_path`，即后端按项目根目录解析后的完整日志文件路径；`config.toml` 仍只保存 `directory` 和 `filename`。插件设置页展示和编辑「完整日志路径」时会在保存前拆回这两个字段，因此现有配置文件结构保持兼容。

## 插件设置页覆盖范围

浏览器插件的设置页通过后端 `/api/config` 读取和保存配置。当前 UI 已覆盖常用和高风险易漏项：

- 基础：`language`、`data_dir`、`storage.db_path`
- LLM：默认 provider、全局并发数、显式备选 provider、各 provider 的 key/model/base_url、DeepSeek `reasoning_effort`、OpenRouter headers、四个 per-module override
- B 站与多源：`bilibili.browser.*`、`sources.bilibili.enabled`、`sources.browser.*`、`sources.xiaohongshu.*`、`sources.douyin.*`、`sources.youtube.*`
- 调度：`scheduler.enabled`、`pause_on_extension_disconnect`、`extension_disconnect_grace_seconds`、`pool_target_count`、`account_sync_interval_hours`、refresh / signal / trending / explore / discovery limit / proactive push / speculator idle 等 runtime 频率参数、四个平台 `pool_source_shares`、猜测兴趣参数、自动更新参数；设置页可调用 `/api/config/source-share-suggestion` 按已有事件和当前表单开关填入建议比例
- 日志：控制台 / 文件级别、完整日志路径（保存时拆回 `directory` / `filename`）、轮转与非托管日志清理参数

保留但不单独暴露的字段主要是目前只有一个有效值的内部兼容项，例如 `[sources.douyin].mode = "direct"`；保存时插件会继续按当前支持值写回，不会删除其他高级字段。

## `/api/config` 保存与恢复语义

设置页和外部调用方都走同一条配置 API。`GET /api/config` 默认会 mask API Key；`PUT /api/config` 只更新请求体里出现的字段，并遵循以下安全规则：

- masked key（例如 `sk-****abcd`）不会写回 `config.toml`，避免把真实密钥覆盖成星号。
- 已有非空的 `model`、`base_url`、OpenRouter headers、DeepSeek `reasoning_effort` 和 embedding `model/base_url/api_key` 不会被空字符串覆盖；空值只在旧值本来为空时写入。
- 需要真正清空 API Key 时，调用方必须传 `reset_fields`。当前允许值为 `llm.openai.api_key`、`llm.claude.api_key`、`llm.gemini.api_key`、`llm.deepseek.api_key`、`llm.openrouter.api_key`、`llm.openai_compatible.api_key`、`llm.embedding.api_key`；未知字段返回 400。
- 写盘前会先用新配置构建 LLM registry；blocking issue 会返回 400 且不写入 `config.toml`。
- 写盘前会生成 `config.toml.bak`。正常模式下热重载失败会尝试恢复备份，并在响应里设置 `rollback_applied=true`；如果备份恢复也失败，接口返回 500 和人工恢复提示。

`PUT /api/config` 返回 `ConfigUpdateResponse`：

| 字段 | 说明 |
|------|------|
| `ok` | 请求是否完成。校验失败时为 `false`。 |
| `reloaded` | 是否已热重载运行时组件。 |
| `rollback_applied` | 热重载失败后是否已从 `config.toml.bak` 回滚。 |
| `restart_required` | 新配置是否已写入但需要重启 daemon 才能生效。降级模式保存会返回 `true`。 |
| `config` | 保存后或回滚后的配置快照，API Key 仍默认 masked。 |
| `message` | 给 UI 展示的人类可读状态。 |

当 daemon 因 LLM registry 配置错误进入降级模式时，`GET /api/config` 会返回 `degraded=true`、`degraded_reason="llm_registry_unavailable"` 和 blocking issues；`PUT /api/config` 会保存修复配置但不尝试热重载，返回 `restart_required=true`，要求用户重启 daemon。

## 环境变量

| 变量 | 说明 |
|------|------|
| `OPENBILICLAW_BILIBILI_COOKIE` | 集成测试用 B 站 Cookie |
| `GOOGLE_API_KEY` | Gemini 官方推荐 API Key 环境变量，优先级高于 `GEMINI_API_KEY` |
| `GEMINI_API_KEY` | Gemini 官方兼容环境变量，`default_provider=gemini` 时可替代 `llm.gemini.api_key` |
| `OPENBILICLAW_PROXY_HOST` | Docker 运行时可选宿主机代理地址，默认 `host.docker.internal` |
| `OPENBILICLAW_PROXY_PORT` | Docker 运行时可选宿主机代理端口，默认 `7897` |
| `OPENBILICLAW_PROXY_TIMEOUT` | Docker 运行时代理探测超时（秒），默认 `1.0` |
| `OPENBILICLAW_DOUYIN_COOKIE` | 抖音 direct-cookie discovery 的显式 Cookie 覆盖；未设置时读取扩展同步的 `data/douyin_cookie.json` |
| `OPENBILICLAW_NO_XHS` | 设为 `1` 时永久跳过 `init` 的小红书接入，即使脚本传了 `--yes-xhs` |
| `OPENBILICLAW_NO_DOUYIN` | 设为 `1` 时永久跳过 `init` 的抖音接入，即使脚本传了 `--yes-douyin` |
| `OPENBILICLAW_NO_YOUTUBE` | 设为 `1` 时永久跳过 `init` 的 YouTube 接入，即使脚本传了 `--yes-youtube` |
| `OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS` | `init --yes-xhs` 收集小红书扩展任务结果的最大等待秒数，默认 `180`；`fetch-xhs --wait-seconds` 可覆盖单次 smoke 命令 |
| `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS` | 小红书 `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用，`fetch-xhs --force` 可绕过单次复用 |
| `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS` | `init --yes-xhs` 的小红书每个 scope 最大滚动轮数，默认 `15` |
| `OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS` | `init --yes-xhs` 的小红书每个 scope 最多采集条目数，默认 `300` |
| `OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS` | `init --yes-douyin` 收集抖音扩展任务结果的最大等待秒数，默认 `180`；`fetch-douyin --wait-seconds` 可覆盖单次 smoke 命令 |
| `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS` | 抖音 `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用 |
| `OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS` | `init --yes-douyin` 的抖音每个 scope 最大滚动轮数，默认 `15` |
| `OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS` | `init --yes-douyin` 的抖音每个 scope 最多采集条目数，默认 `300` |
| `OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS` | `init --yes-youtube` 收集 YouTube 扩展任务结果的最大等待秒数，默认 `240`；`fetch-youtube --wait-seconds` 可覆盖单次 smoke 命令 |
| `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS` | YouTube `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用 |
| `OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS` | `init --yes-youtube` 的 YouTube 每个 scope 最大滚动轮数，默认 `10` |
| `OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS` | `init --yes-youtube` 的 YouTube 每个 scope 最多采集条目数，默认 `300` |

## Docker 部署说明

使用仓库根目录下的 `docker-compose.yml` 时，默认会挂载：

- `openbiliclaw_config -> /app/runtime`
- `openbiliclaw_data -> /app/runtime/data`
- `openbiliclaw_logs -> /app/runtime/logs`

这意味着：

- 容器启动前不需要宿主机准备 `config.toml`
- 首次启动时会自动在 volume 中生成 `/app/runtime/config.toml`
- `data/` 会持久化 SQLite、画像、Cookie 和运行态文件
- `logs/` 会持久化后端日志，便于排查服务器问题
- 容器内运行时会把 `/app/runtime` 视为项目根目录，因此 `config-show` 中看到的路径应为 `/app/runtime/config.toml` 和 `/app/runtime/data`
- 容器启动时会自动尝试探测 `host.docker.internal:$OPENBILICLAW_PROXY_PORT`；可达时自动注入代理，不可达时直接回退直连
- 容器内每次执行 `openbiliclaw ...` 时也会重复这层探测，因此 `docker exec` 场景不需要额外手动补 `HTTP_PROXY`

如果你修改了 `[general].data_dir` 或 `[logging].directory` 为自定义绝对路径，需要同步调整 Docker volume 的挂载目标路径。

### Docker 最小配置示例

```toml
[general]
language = "zh"
data_dir = "data"

[llm]
default_provider = "openai"

[llm.openai]
api_key = "sk-..."
model = "gpt-5-nano"

[bilibili]
auth_method = "cookie"
cookie = ""
```

建议：

- Docker 模式下的首选入口是 `python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie`；它会确认配置、同步到容器 `/app/runtime`，并自动运行 init
- `docker exec -it openbiliclaw-backend openbiliclaw init` 是高级手动 fallback，用于重复初始化或排查
- 如果缺少 provider API Key 或 B 站 Cookie，bootstrap / init 会直接在终端里引导并写回 Docker volume
- provider 和 API Key 会写入 `/app/runtime/config.toml`
- B 站 cookie 会写入 `/app/runtime/data/bilibili_cookie.json`
- 首轮 `init` 和后续 `discover` 可能持续几分钟，因为它们会真实访问 B 站和当前 LLM provider
- 当前 discover 已启用保守受控并发；默认会并发处理少量 B 站请求和 LLM 评分，但不提供额外用户配置项
- `init` 的首轮补货会按 `search + related_chain -> trending -> explore` 分阶段推进，并尽量把 fresh 候选池补到至少 `100` 条
- 如不方便交互，可使用 `docker exec openbiliclaw-backend openbiliclaw auth login --cookie "..."`

补充：

- `docker compose up -d`、`build`、`down` 这类生命周期命令仍建议在项目目录执行
- 如果不在项目目录，可以显式传 `-f /path/to/docker-compose.yml`
- 如果你使用 Clash Verge 一类本机代理，并且对 Docker 暴露了 HTTP 代理端口，容器无需手动写 `HTTP_PROXY`
- 非交互终端不会进入引导；服务器脚本、CI 或批量部署仍需预置 `config.toml` 和 Cookie
- 如需手动编辑容器内配置，可使用 `docker cp` 导出 `/app/runtime/config.toml`，修改后再复制回去
- 如需彻底清空 Docker 内状态，可执行 `docker compose down -v`
