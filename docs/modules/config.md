# 配置参考

> `[llm].concurrency` 缺省/非法值为 4；显式正数（含旧值 3）原样保留。后台容量为 `max(1, total-1)`；`candidate_eval_concurrency` 仍默认 3。

> `config.toml` 所有配置段落详解。

## 快速开始

```bash
cp config.example.toml config.toml
# 编辑 config.toml，填入 LLM API Key；或对 OpenAI 实验性启用 Codex OAuth
```

## 配置文件位置与恢复

源码 / AI 一键安装 / 桌面安装包默认都使用同一个运行目录：macOS / Linux 为 `~/OpenBiliClaw`，Windows 为 `%USERPROFILE%\OpenBiliClaw`。`config.toml` 保存主配置，`config.local.toml` 是可选本机覆盖文件，加载时后者覆盖前者。

桌面安装包启动时会先检查 `config.toml` 与 `config.local.toml` 是否可解析、是否能构建运行时 `Config` 对象。若发现 TOML 语法错误、文件编码错误，或结构形状导致配置对象无法构建，入口会把坏文件改名为 `config.toml.invalid` / `config.local.toml.invalid`（已有同名备份时追加 `.1`、`.2`），再从随包 `config.example.toml` 重新生成默认 `config.toml` 并打开 `/setup/` 重新初始化。这个恢复流程只处理配置文件，不会移动或删除 `data/`、数据库、Cookie 缓存或日志。

CLI / 源码运行仍按普通错误处理：配置文件损坏时直接暴露异常，方便开发和部署排查。

配置读取接口把所有 API Key、Cookie、令牌及代理 URL userinfo 视为**只写秘密**：`GET /api/config` 始终返回掩码；旧客户端携带的 `?reveal_keys=true` 仅作兼容参数接受，不能再导出原值。桌面 Web 与扩展只请求普通 `/api/config`，扩展写入 `chrome.storage` 的也只是掩码快照。设置输入留空或回传掩码仍表示“保持原值”，新值只通过 `PUT /api/config` 写入。

## 配置段落

插件、桌面 Web 和移动 Web 的「保存时自动同步到对应平台」都从 API 读取，默认关闭。插件与移动 Web 的配置 GET/PUT 使用 AbortController 有界 timeout；插件的同一 deadline 从后端地址解析开始，覆盖初次设备会话交换、401 强制换票、受保护请求与响应解析，认证 fetch 接收同一 AbortSignal。移动 Web 使用模态设置对话框：Escape 可关闭、Tab 焦点留在对话框内，关闭后回到原设置按钮；配置 GET 超时或失败时保存与开关保持禁用，用户必须通过「重试加载」成功取得当前值后才能写回，避免用默认 false 覆盖未知远端状态。

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

`openbiliclaw start` 和桌面安装包入口默认读取这里的 host / port；显式设置 `OPENBILICLAW_HOST` / `OPENBILICLAW_PORT` 时环境变量优先。浏览器插件的手机二维码入口会在后端地址仍是 loopback 时调用轻量端点 `GET /api/qr-info`（不触发 embedding readiness probe）并读取响应中的 `lan_ip` 字段，用局域网 IP 生成 `/m/` 二维码；但后端仍需要绑定 `0.0.0.0`，手机才能连上。

### `[api.auth]`

局域网 / 远程访问的**可选密码门禁**（`ApiAuthConfig`）。仅当 `enabled=true` 且请求非可信本机时生效；本机（loopback 且无转发头）默认免登录。远程浏览器扩展必须另行启用设备密钥认证。详见 [`docs/modules/api-auth.md`](api-auth.md)。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否为局域网 / 远程访问开启密码门禁。`true` 且 `password_hash` 为空时按配置错误处理（blocking） |
| `password_hash` | string | `""` | scrypt 密码哈希。**请勿手填明文**；用 `openbiliclaw set-password` / `init` / 环境变量设置 |
| `session_secret` | string | `""` | 登录态 HMAC 签名密钥。首次启用为空时自动生成并写回 config；请勿外泄 |
| `session_ttl_hours` | int | `0` | 登录态有效期（小时）。`0` = 永不过期（默认，「记住登录」）；`>0` = 限时登录 |
| `trust_loopback` | bool | `true` | 本机请求是否免登录（扩展 / CLI 依赖此项）。设 `false` 连本机也要登录。带代理转发头（`X-Forwarded-For` 等）的请求不算本机 |
| `trusted_proxies` | list[string] | `[]` | 受信任的同机 / 前置反向代理 IP；仅当直接对端命中此列表，才采信 `X-Forwarded-For`（从右向左）解析真实客户端 IP。**仅 TOML**（env 不支持列表）。同机反代必须配置，否则远程会被误判为本机 |
| `allowed_bearer_origins` | list[string] | `[]` | 允许「跨源 Bearer 登录」的 Origin 白名单。默认空 = 只允许同源 Cookie 登录，绝不向 JS 返回 token。**仅 TOML** |
| `extension_access_enabled` | bool | `false` | 远程扩展设备认证总开关；默认关闭。至少生成一个设备密钥后才能用 `ext-key enable` 开启 |
| `extension_access_keys` | list[string] | `[]` | `<12位 key ID>:<SHA-256 digest>` 记录，仅存高熵设备 secret 的摘要。使用 CLI 管理，不要写入明文密钥；不会由 `GET /api/config` 返回 |
| `extension_token_ttl_hours` | int | `24` | 扩展短会话有效期，范围 `1..168` 小时；长期设备密钥仅用于换取短会话 |

> **环境变量覆盖（显式读取）**：`OPENBILICLAW_API_AUTH_ENABLED` / `_PASSWORD`（明文，启动时即 hash）/ `_PASSWORD_HASH` / `_SESSION_SECRET` / `_SESSION_TTL_HOURS` / `_TRUST_LOOPBACK`。`trusted_proxies` 与 `allowed_bearer_origins` 是列表，**只支持 TOML**，没有 env 覆盖。
>
> 撤销纪元 `auth_epoch` 与密码指纹 `password_fingerprint` 是运行时高频可变状态，**不在 config.toml**，由后端写在 SQLite `data/openbiliclaw.db` 的 `auth_state` 表（改密 / 登出所有设备 / 轮换密钥时自增，使旧登录态立即失效）。`session_secret` / `password_hash` 也**永不经 `GET /api/config` 返回**（即便 `reveal_keys=true`）。

### `[saved_sync]`

```toml
[saved_sync]
auto_sync_enabled = false
```

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auto_sync_enabled` | bool | `false` | 是否在 OpenBiliClaw 本地收藏 / 稍后再看成功后创建对应平台账号写入任务。默认关闭；首次从插件、桌面 Web 或移动 Web 开启时必须确认外部账号修改警告。关闭不影响保存页手动同步。 |

插件 side panel 设置、桌面 Web 和移动 Web 都从 `GET /api/config` 回读该值，并以 `PUT /api/config` 的 `{saved_sync: {auto_sync_enabled}}` 严格保存。卡片保存始终先写本地；平台失败不回滚本地成功。列表页移除只删除 OpenBiliClaw membership，不反向删除平台收藏、书签、Saved、播放列表或稍后观看记录。

六平台授权 E2E 同样从 `auto_sync_enabled = false` 开始并在退出时恢复原值。手动 favorite / watch-later 不修改该开关；自动同步用例只有在用户对 exact platform、action、public content ID 和 expected target 明确同意后才临时开启。配置同意不能替代当次 `allow_state_changing=true` 精确授权。

推荐卡保存不会在前端按平台决定是否同步：只有后端读到 `auto_sync_enabled = true` 才创建 native task。关闭时响应中的 `pending` 不带 task ID，三个图形化保存页仍保留手动同步；带 task ID 的 `pending` / `syncing` 才表示已有任务并禁用重复提交。

旧 `config.toml` 缺少该段时，加载、`GET /api/config` 与 `openbiliclaw config-show` 都按
`false` 解析；保存其它配置字段不会意外把它改成 `true`。首次在任一图形界面从关闭切到
开启，必须先确认外部账号写入警告。列表页的手动单项 / 批量同步是独立的显式授权入口，
即使这里仍为 `false` 也可用。

### `[autostart]`

当前用户作用域的**开机 / 登录自启动**配置（`AutostartConfig`）。该功能只注册当前用户的桌面登录项，不写系统级服务、不要求管理员权限；Docker / 容器环境和未知平台会显示为不支持。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否期望系统登录后自动拉起 `openbiliclaw start`。可通过插件 / 桌面 Web 设置页或 `openbiliclaw autostart enable/disable` 修改 |
| `manage_ollama` | bool | `true` | `start` 时如果检测到当前配置需要本机 Ollama，且 endpoint 是默认 `127.0.0.1:11434`，会在 Ollama 未运行时尝试后台拉起 `ollama serve`。自定义端口或远端 endpoint 只探测不拉起 |

`save_config()` 默认会保留磁盘上已有的 `[autostart].enabled`，避免普通配置保存用陈旧快照覆盖用户刚从 API / CLI 改过的自启动开关。只有 `/api/autostart/apply` 和 `openbiliclaw autostart enable/disable` 会以 `autostart_authoritative=true` 权威写入该字段。

如果当前进程依赖 `OPENBILICLAW_*`、`GOOGLE_API_KEY` / `GEMINI_API_KEY`、或配置的抖音 Cookie 环境变量，自启动开启会被拒绝：登录会话通常拿不到交互式 shell 环境变量，应先把这些值写进 `config.toml`。

### `[llm]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `routing_version` | int | `2` | LLM 实例路由配置版本。新配置固定为 `2` |
| `default_chain` | list[string] | `["deepseek"]` | 全局有序实例链。每一项引用一个 `[llm.instances.<id>]`；请求按从左到右的顺序尝试 |
| `concurrency` | int | `4` | 单 runtime 的 LLM 总并发上限；后台容量派生为 `max(1, total-1)`（默认 3）。合法范围 `1..16` |
| `timeout` | int | `300` | 每个实例请求的超时秒数，合法范围 `10..600` |

`default_chain` 里的元素是**实例 ID**，不是 Provider 类型。一个实例是一套完整、可独立调用的端点配置，因此可以同时存在两个 `provider_type = "openai_compatible"` 的中转渠道、两个 OpenAI 账号，或同一网关上的不同模型：

```toml
[llm]
routing_version = 2
default_chain = ["relay-primary", "relay-backup", "deepseek"]
concurrency = 4
timeout = 300

[llm.instances.relay-primary]
name = "主中转"
provider_type = "openai_compatible"
enabled = true
api_key = "sk-..."
model = "gpt-5-nano"
base_url = "https://primary.example.com/v1"

[llm.instances.relay-backup]
name = "备用中转"
provider_type = "openai_compatible"
enabled = true
api_key = "sk-..."
model = "gpt-5-nano"
base_url = "https://backup.example.com/v1"

[llm.instances.deepseek]
name = "DeepSeek 官方"
provider_type = "deepseek"
enabled = true
api_key = "sk-..."
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
```

链只在当前实例出现 Provider 级失败、超时、限流或无有效内容时继续；限流冷却按**实例 ID**隔离，同类型的健康备用渠道不会被一起冷却。保存时会阻止空链、重复引用、不存在或停用的实例，以及缺少必要凭据的启用实例。`PUT /api/config` 遇到 blocking issue 返回 400，并保持磁盘和运行时原状。

### `[llm.instances.<instance_id>]`

实例 ID 必须以小写字母或数字开头，后续只允许小写字母、数字、`_`、`-`，最长 64 个字符；它必须唯一且保存后应保持稳定，调用统计、失败日志、路由和冷却都用它区分具体渠道。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `name` | string | 实例 ID | 设置页显示名称，可重复 |
| `provider_type` | string | `""` | 适配器类型：`openai` / `claude` / `gemini` / `deepseek` / `ollama` / `openrouter` / `openai_compatible` |
| `enabled` | bool | `true` | 是否允许注册和引用；停用实例不能留在任何链里 |
| `api_key` | string | `""` | 此实例自己的凭据；API 默认只回显掩码 |
| `model` | string | `""` | 此实例固定使用的聊天模型 |
| `base_url` | string | `""` | 此实例的服务地址；留空时按适配器默认值 |
| `auth_mode` | string | `""` | OpenAI 的 `api_key` / `codex_oauth` 认证模式 |
| `api_flavor` | string | `""` | OpenAI / OpenAI-compatible 的 `chat_completions` / `responses` 协议选择 |
| `http_referer` / `x_title` | string | `""` | OpenRouter 可选归属请求头 |
| `reasoning_effort` | string | `"medium"` | 支持该能力的适配器默认推理档位；空字符串表示不请求通用 effort（DeepSeek 例外：显式关闭 thinking） |
| `num_ctx` | int | `0` | 仅 Ollama 使用；`0` 采用服务端默认上下文 |

以下小节说明各 `provider_type` 的专属语义。

#### OpenAI（`provider_type = "openai"`）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | API Key；启用并进入任意调用链时必填（`codex_oauth` 除外） |
| `model` | string | `"gpt-5-nano"` | 模型名称（按 `base_url` 后端实际部署的模型填，例如 vLLM 上是 `meta-llama/Llama-3.1-70B-Instruct`） |
| `base_url` | string | `""` | 留空使用 OpenAI 官方 `https://api.openai.com/v1`；指向任何 OpenAI 兼容服务的 `/v1` 端点：Azure OpenAI / vLLM / LMStudio / OneAPI / Cloudflare AI Gateway / 自建 LLM 网关 |
| `auth_mode` | string | `""` | 认证模式：`""` / `"api_key"` 使用 `api_key`；`"codex_oauth"` 使用 `openbiliclaw login codex` 导入的 Codex CLI ChatGPT OAuth 凭据 |
| `api_flavor` | string | `""` | API 端点协议（issue #72）：`""` / `"chat_completions"` 走 `/v1/chat/completions`（默认）；`"responses"` 走 `/v1/responses`——部分第三方网关的 GPT 模型只开放这个端点。非法值会被 `_collect_config_issues` 以 blocking 级拦下 |
| `reasoning_effort` | string | `"medium"` | OpenAI 官方 GPT-5 / o-series 生效；Chat Completions 映射到 `reasoning_effort`，Responses 映射到 `reasoning.effort`。空字符串省略该字段，明确填写的 `none/minimal/low/medium/high/xhigh/max` 原样交给官方接口按具体模型校验；普通 GPT-4 不发送 |

> `openai` 实例默认指 OpenAI 官方，也能通过 `base_url` 指向兼容服务；需要同时管理多个兼容渠道时，优先使用多个 `openai_compatible` 实例，身份和用量归属更清晰。
> 例如：
> - Azure OpenAI → `base_url = "https://your-resource.openai.azure.com/openai/deployments/your-deployment"`
> - 本地 vLLM → `base_url = "http://localhost:8000/v1"`，`api_key` 任填或留空
> - OneAPI 网关 → `base_url = "https://your-oneapi.example.com/v1"`

> `auth_mode = "codex_oauth"` 是实验性 / 非官方路径：OpenAI 官方 API 认证仍以 Platform API key 为稳定入口。启用前先运行 `openbiliclaw login codex`，OpenBiliClaw 会从官方 Codex CLI 登录态导入 token 到 `~/.openbiliclaw/codex_auth.json`。该模式下 `api_key` 会被忽略，并且 `base_url` 只能留空或指向 `https://api.openai.com`，避免把 ChatGPT OAuth token 发给第三方代理。

#### Claude（`provider_type = "claude"`）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | Anthropic API Key |
| `model` | string | `"claude-sonnet-4-6"` | 模型名称 |
| `base_url` | string | `""` | 留空 = Anthropic 官方 `https://api.anthropic.com`；使用第三方中转 / 网关时填其地址（需实现 Anthropic 协议 `/v1/messages`，issue #72） |
| `reasoning_effort` | string | `"medium"` | Claude Sonnet 4.6+、Opus 4.5+ 等已确认型号映射到 `output_config.effort`；旧型号不发送。空渠道调用在支持型号上映射为最低安全档 `low` |

#### Gemini（`provider_type = "gemini"`）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | Gemini API Key；未填写时可读取 `GOOGLE_API_KEY` / `GEMINI_API_KEY` |
| `model` | string | `"gemini-2.5-flash"` | Gemini 模型名称 |
| `reasoning_effort` | string | `"medium"` | Gemini 3 映射 `thinkingLevel`；Gemini 2.5 以当前输出上限的 50% budget 近似中档。空渠道调用在 2.5 Flash 关闭 thinking，在不能关闭的 2.5 Pro / Gemini 3 降到最低合法档 |

> Gemini provider 按官方 quickstart 走 `google-genai` SDK 的 Gemini Developer API，不是 Vertex AI。

#### DeepSeek（`provider_type = "deepseek"`）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | DeepSeek API Key |
| `model` | string | `"deepseek-v4-flash"` | 模型名称（可选 `deepseek-v4-pro`；旧 `deepseek-chat` / `deepseek-reasoner` 将于 2026/07/24 弃用） |
| `base_url` | string | `"https://api.deepseek.com"` | API 地址；可填 DeepSeek-compatible 中转 / 私有网关，registry 会把该值实际传给 SDK，并按这个 endpoint 决定直连或使用 `[network]` 代理 |
| `reasoning_effort` | string | `"medium"` | 深度任务默认均衡档；DeepSeek 官方会把 portable `low/medium` 映射为 native `high`，`xhigh/max` 映射为 `max`。渠道型 discovery / recommendation / sources 调用仍按单次 `""` 真正关闭 thinking；手动设 `""` 可全局关闭 |

#### Ollama（`provider_type = "ollama"`）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `model` | string | `"qwen2.5:7b"` | 本地模型名称 |
| `base_url` | string | `"http://127.0.0.1:11434/v1"` | Ollama OpenAI-compatible `/v1` 服务地址。默认用 `127.0.0.1`（而非 `localhost`）与 Ollama 只监听 IPv4 的默认行为对齐，避免 `localhost` 被解析到 IPv6 (`::1`) 导致连接超时 |
| `num_ctx` | int | `0` | 上下文窗口 (tokens)。`0` = 用 Ollama 服务端默认值（通常 4096），走 `/v1` 兼容层。`>0`（推荐 `8192`）时聊天改走原生 `/api/chat` 端点并传 `options.num_ctx`——`/v1` 兼容层会静默丢弃 `num_ctx`，大批量 prompt 超 4096 即被截断、本地小模型输出无法解析的 JSON。仅 Ollama 生效 |

> Ollama 不需要 API Key，适合本地开发测试。

> **聊天模型必须明确填写：** `model` 为空时 Ollama 不会进入 chat registry，即使实例已启用也不会猜模型或回退到 `llama3`。只使用 Ollama `bge-m3` 做 embedding 时无需创建聊天实例；embedding 会走独立配置，不会触发聊天探针。
>
> **`num_ctx` 为何重要：** Ollama 的 OpenAI 兼容 `/v1` 端点不接受 `num_ctx`，模型按服务端默认上下文（多为 4096）加载。发现循环里 discovery 批量评估 / 推荐文案批量生成等 prompt 很容易超 4096，被静默截断后小模型（如 `qwen:7b`）就会吐出非法 JSON、或为整批视频生成同一句重复文案。设 `num_ctx = 8192` 后，OpenBiliClaw 改用原生 `/api/chat` 端点（已实测 `context_length` 真正变为 8192）即可规避。

#### OpenRouter（`provider_type = "openrouter"`）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | OpenRouter API Key |
| `model` | string | `"openai/gpt-5-nano"` | OpenRouter 模型名称 |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API 地址 |
| `http_referer` | string | `""` | 可选的 `HTTP-Referer` 请求头 |
| `x_title` | string | `"OpenBiliClaw"` | 可选的 `X-Title` 请求头 |
| `reasoning_effort` | string | `"medium"` | 通过 OpenRouter `reasoning.effort` 统一映射目标厂商；空渠道调用不发送（adapter 没有 per-model mandatory metadata，不能安全地对强制推理模型发 `none`） |

> `http_referer` 和 `x_title` 都是可选项；留空时不会阻止请求发送。

#### OpenAI-compatible（`provider_type = "openai_compatible"`）

通用 OpenAI 协议兼容适配器，用于接入 Groq / Together / Azure OpenAI / vLLM / 自建等任何兼容端点。每个 `[llm.instances.<id>]` 都是独立身份，可以同时配置任意数量的账号、网关与模型；cost、retry、限流冷却和探测结果不会互相混淆。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | 上游服务的 API Key |
| `model` | string | `""` | 上游服务的模型名（如 `llama-3.1-70b-versatile`、`Qwen/Qwen2.5-72B-Instruct-Turbo`、Azure 部署名等） |
| `base_url` | string | `""` | **必填**。上游服务的 OpenAI 协议端点；缺失时配置校验会指向具体实例并拒绝保存 |
| `api_flavor` | string | `""` | API 端点协议（issue #72）：`""` / `"chat_completions"` 走 `/v1/chat/completions`（默认）；`"responses"` 走 `/v1/responses`——部分第三方网关的 GPT 模型只开放这个端点 |
| `reasoning_effort` | string | `""`（新实例） | 空值不发送；明确填写非空值时分别透传到 Chat `reasoning_effort` 或 Responses `reasoning.effort`。兼容协议没有统一能力声明，目标网关是否接受由其自身校验 |

常见示例：

| 服务 | base_url | model 示例 |
|------|----------|-----------|
| Groq | `https://api.groq.com/openai/v1` | `llama-3.1-70b-versatile` |
| Together | `https://api.together.xyz/v1` | `Qwen/Qwen2.5-72B-Instruct-Turbo` |
| Azure OpenAI | `https://<resource>.openai.azure.com/openai/deployments/<deployment>` | `(matches deployment name)` |
| vLLM 自建 | `http://localhost:8000/v1` | `(vLLM 加载的模型名)` |

`[llm.embedding].provider` 也接受 `openai_compatible`：多数 OpenAI-compat 后端（Together / vLLM / Azure）都暴露 `/v1/embeddings`，可以直接挂上来，与 chat 用同一组 base_url 也行（互相独立的 provider 实例）。

### `[llm.embedding]`

Embedding 服务用于多个语义任务：discovery 内容兴趣预过滤、recommendation 跨主题去重、PoolCurator 反馈相似度判定、interest / avoidance probe 主题归类。

**本段拥有独立的 `api_key` / `base_url`，与聊天实例和 `[llm].default_chain` 完全解耦。** 不再被迫为「embedding 用 OpenAI 但 chat 用 DeepSeek」这种场景在两处填同一组凭据。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `provider` | string | `""` | 留空 = 不启用 embedding；不会跟随聊天调用链。可填 `"openai"` / `"gemini"` / `"ollama"` / `"openai_compatible"` / `"openrouter"` / **`"dashscope"`**（阿里百炼多模态向量）。Claude / DeepSeek 没有 embedding 接口；OpenRouter 必须显式配 `model` |
| `model` | string | `"gemini-embedding-001"` | embedding 模型名；按 provider 自动填合理默认：`gemini → gemini-embedding-001` / `openai → text-embedding-3-small` / `ollama → bge-m3` / **`dashscope → qwen3-vl-embedding`**。`openrouter` / `openai_compatible` 无安全默认，需要显式指定 |
| `api_key` | string | `""` | v0.3.32+ embedding 专属 API Key。默认不会借用 `[llm.<provider>].api_key`；只有 `fallback_enabled=true` 时才允许旧配置借用 chat-side 凭据并打一条 WARNING。Ollama 不需要 |
| `base_url` | string | `""` | v0.3.32+ embedding 专属 base URL。留空使用 provider 默认值（OpenAI → `api.openai.com/v1`、Ollama → `localhost:11434/v1`、Gemini → 官方 API）；Gemini 可填代理地址 |
| `output_dimensionality` | int | `1024` | embedding 目标向量维度。默认 1024，与本地 Ollama `bge-m3` 对齐；Gemini 会传 `output_dimensionality`，`provider = "openai"` 且模型为 `text-embedding-3-*` 时会传 `dimensions`。Ollama / OpenRouter / 泛 OpenAI-compatible 等未确认支持的后端不传参数，也不会把 cache 标成伪维度。设为 `0` 表示使用 provider 原生默认维度 |
| `similarity_threshold` | float | `0.82` | 余弦相似度阈值，超过即视为"同主题" |
| `fallback_enabled` | bool | `false` | 旧兼容开关；允许备选类型借用第一个同类型、已启用聊天实例的凭据 |
| `fallback_provider` | string | `""` | 第二个 embedding 备选 Provider。留空 = 不 fallback；可填 `openai` / `gemini` / `ollama` / `openai_compatible`，不会再自动走 `ollama → gemini → openai` 链 |
| `multimodal_enabled` | bool | `false` | 是否启用**封面图单独** embedding（image-only 向量，与文本同一模型空间），供 recommendation `precompute_delight_scores` 的封面视觉加成消费。默认关闭。开启后仍需当前 `model` 支持图像（如 `gemini-embedding-2`，或 `dashscope` + `qwen3-vl-embedding`）；本地 `ollama` + `bge-m3` 等纯文本模型会自动跳过，不报错。与 `[discovery].multimodal_evaluation_enabled`（vision LLM 评估）相互独立。**插件设置页与桌面 Web 设置的 Embedding 段均可直接勾选**（`dashscope` 也已加入 provider 下拉），无需手改 TOML |

#### DashScope / Qwen 多模态 embedding 示例

```toml
[llm.embedding]
provider = "dashscope"
model = "qwen3-vl-embedding"
api_key = "sk-..."          # 或环境变量 DASHSCOPE_API_KEY
base_url = ""               # 默认 https://dashscope.aliyuncs.com；国际站可填 https://dashscope-intl.aliyuncs.com
output_dimensionality = 1024  # qwen3-vl-embedding 支持 2560/2048/1536/1024/768/512/256
similarity_threshold = 0.82
multimodal_enabled = true   # 封面 image-only 向量；与文本同一空间
```

说明：DashScope 多模态向量走**原生** `.../multimodal-embedding/multimodal-embedding` 接口，**不是** `compatible-mode/v1/embeddings`。聊天若要用通义，新建 `provider_type = "openai_compatible"` 的聊天实例并指向 `compatible-mode/v1`；embedding 与 chat 凭据可共用同一把 `sk-` Key，但配置段彼此独立。

#### 配置页服务探测 API（v0.3.114+）

桌面 Web `/web` 与插件 side panel 都可测试单个聊天实例、整条默认链和 embedding。插件可直接新建、编辑、删除实例并调整全局 `default_chain`；模块自定义链在插件中只读展示，需进入 PC Web 编辑。探测走一个**无写入**接口，不会保存 `config.toml`，也不会触发运行时热重载。

```http
POST /api/config/probe-service
Content-Type: application/json

{
  "kind": "llm_chain",
  "config": {
    "llm": {
      "routing_version": 2,
      "default_chain": ["relay-primary", "relay-backup"],
      "instances": {
        "relay-primary": {
          "name": "主中转",
          "provider_type": "openai_compatible",
          "enabled": true,
          "api_key": "sk-...",
          "model": "gpt-5-nano",
          "base_url": "https://primary.example.com/v1"
        },
        "relay-backup": {
          "name": "备用中转",
          "provider_type": "openai_compatible",
          "enabled": true,
          "api_key": "sk-...",
          "model": "gpt-5-nano",
          "base_url": "https://backup.example.com/v1"
        }
      }
    }
  }
}
```

后端先读取当前 `load_config()`，再把请求里的 `config.llm` 按 `PUT /api/config` 的同一套规则合并到内存副本上：

| kind | 行为 | 成功条件 |
|------|------|----------|
| `llm_instance` | 通过请求体 `instance_id` 精确探测一个实例，不走 fallback | 实例已启用、可注册、chat-capable，并返回非空 `content` |
| `llm_chain` | 从 `default_chain[0]` 开始真实执行整条顺序链 | 任一实例成功；响应的 `instance_id` 表明最终由谁处理 |
| `llm` | 旧客户端兼容入口，精确探测全局首实例 | 首实例返回非空 `content` |
| `llm_fallback` | 旧客户端兼容入口，精确探测链中第二项 | 第二实例存在且返回非空 `content` |
| `embedding` | 构建临时 `EmbeddingService`，调用 `EmbeddingService.probe()` 绕过缓存真实取一次向量 | provider 已配置，并返回非空向量 |

响应统一为：

```json
{
  "ok": true,
  "kind": "llm_chain",
  "instance_id": "relay-backup",
  "provider": "openai_compatible",
  "model": "gpt-5-nano",
  "message": "LLM chain is available.",
  "error": "",
  "latency_ms": 428
}
```

探测失败也会返回 `200` + `ok=false`，让前端以行内状态显示错误原因；只有请求体 schema 错误等 API 层问题才按常规 4xx 处理。

#### 启用本地 Ollama embedding（v0.3.0+，**v0.3.3 起真实生效**）

> ⚠️ **如果你装的是 v0.3.0~v0.3.2**：`setup-embedding` 当时虽然写了 `[llm.embedding] provider="ollama"`，但 LLM 注册表静默回退到 default provider，embedding 实际仍走 Gemini。
> **升级到 v0.3.3+ 重启 backend** 即可生效，不需要改配置；当前版本可再跑一次 `openbiliclaw setup-embedding`，向导会把 provider / model / base_url 写入独立的 `[llm.embedding]` 段。

不想再多一份 embedding API Key、或要支持离线，可以用 Ollama + bge-m3 跑本地 embedding：

```bash
# 1. 装 Ollama（一次性）
# Mac
# 安装并启动官方 Ollama.app（会创建 ollama 命令行入口）
open https://ollama.com/download/mac
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

### `[llm.routes.soul]` / `discovery` / `recommendation` / `evaluation`

每个模块默认继承全局 `default_chain`；需要隔离成本、速度、地区或模型能力时，可以改为自己的有序实例链。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `inherit` | bool | `true` | `true` 使用全局链，忽略本段 `chain` |
| `chain` | list[string] | `[]` | `inherit = false` 时使用的实例 ID 顺序；至少一项 |

四个模块在管线里的位置：

| 段 | 用途 | 典型选型 |
|---|---|---|
| `[llm.routes.soul]` | 灵魂画像生成（5 层 Event → Soul），稳定性优先 | 高质量实例，例如 Claude Sonnet / GPT / Gemini Pro |
| `[llm.routes.discovery]` | 关键词生成与来源抽取，调用频次高 | 低成本、低延迟实例 |
| `[llm.routes.recommendation]` | 朋友式解释生成，影响最终用户体感 | 平衡型实例 |
| `[llm.routes.evaluation]` | 池子打分、相关度评估，高频后台调用 | 低成本实例 |

运行时路由（v0.3.75+）：

- `LLMService` 不再用 caller 第一段朴素判断模块，而是内置 caller bucket。例：`soul.*` → soul，`discovery.keyword*`、`discovery.search/explore/trending/related.*`、`yt_search.*`、`sources.xhs.*` → discovery，`recommendation.evaluate_batch`、`discovery.evaluate*`、`eval.*` → evaluation，其他 `recommendation.*` → recommendation。
- `inherit = true` 时调用 `LLMRegistry.complete()`，完整继承全局链。
- `inherit = false` 时调用 `LLMRegistry.complete_chain(route.chain, ...)`，只在该模块链内顺序降级；即使链在运行时全部不可用，也**不会越界回落到全局链**。
- 保存时会阻止不存在、停用、重复或空的自定义链引用。

例：发现/评估优先走低成本中转，画像优先走 Claude：

```toml
[llm.routes.soul]
inherit = false
chain = ["claude-quality", "relay-backup"]

[llm.routes.discovery]
inherit = false
chain = ["relay-cheap", "deepseek"]

[llm.routes.evaluation]
inherit = false
chain = ["relay-cheap", "deepseek"]
```

> 通过 `agent_bootstrap.py` 的命令行写入：
> ```bash
> python3 scripts/agent_bootstrap.py \
>   --module-override soul=claude:claude-sonnet-4-5-20250929 \
>   --module-override discovery=deepseek:deepseek-v4-flash \
>   --module-override evaluation=deepseek:deepseek-v4-flash
> ```

bootstrap 会复用类型和模型都匹配的已有实例；只有模型不同才创建一个完整的派生实例并把模块链指向它。

### 旧配置兼容与迁移

旧的 `default_provider` / `fallback_provider`、`[llm.<provider>]` 和 `[llm.<module>] provider/model` 仍可直接加载和运行。读取旧文件不会自动改盘；`GET /api/config` 会返回等价的实例/调用链投影，用户在新版桌面设置页保存后才写成 v2。迁移规则会保留凭据、Base URL、模型和模块模型覆盖；同类型主备不再因为 Provider 名相同而折叠。旧样例曾为所有远程 Provider 预填默认模型，因此迁移时只投影被路由引用、带真实凭据或可免密运行的端点，不会把只有模板默认值的未启用远程分段变成实例并触发伪缺密钥错误。

首次把一个已有旧格式文件保存成 v2 时，`save_config` 会先在同目录创建逐字节副本
`config.toml.pre-llm-routing.bak`，权限沿用原文件。该备份只创建一次，之后即使再次迁移也绝不覆盖；
新建 v2 文件、只读旧配置、旧格式保存和后续 v2 保存都不会额外生成备份。若备份已经存在，系统把它
视为用户的永久恢复点。

新二进制直接读取旧配置属于向前兼容；旧二进制则不认识 `instances`、`default_chain` 和
`routes`，**不能直接拿 v2 的 `config.toml` 启动**。需要回退程序版本时，先在新版本运行：

```bash
openbiliclaw config-export-legacy
# 默认输出 config.legacy.toml；不会覆盖当前 config.toml
```

导出采用旧格式能表达的确定性子集：保留全局第一项作为主 Provider，再保留后续第一个不同
Provider 类型作为唯一 fallback；每种 Provider 类型只保留一个端点；模块只保留链首的
Provider + model。多个同类型 Base URL / Token、全局第三项及之后、模块 fallback 无法写入旧
schema，命令会逐项告警，不会静默宣称无损。Embedding 的独立配置原样保留。确认告警后应先停止
daemon，保留当前 v2 文件和自动备份，再由操作者显式把导出副本换成旧版本使用的
`config.toml`。导出文件含明文凭据：POSIX 权限会收紧为 `0600`；Windows 上应放在仅当前账户
可访问的目录。完整参数与操作示例见 [CLI 文档](cli.md#openbiliclaw-config-export-legacy)。

旧版扩展仍可写固定 Provider 字段：后端只更新同类型的第一个匹配实例，不会删除、合并或重排其它同类型实例。新版扩展直接读写 v2 `instances` / `default_chain` / `routes`：可维护端点身份和全局链，保存时会完整回传模块路由，因而不会把 PC Web 配置的模块链压回旧格式；模块链本身仍由 PC Web 编辑。

### `[bilibili]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auth_method` | string | `"cookie"` | 认证方式：`cookie` / `qrcode` / `none` |
| `cookie` | string | `""` | 浏览器 Cookie（推荐通过 `auth login` 命令设置） |
| `proxy` | string | `""` | B站 请求专用代理（v0.3.153+）。留空 = 恒直连：客户端忽略环境变量与系统代理（代理出口 IP 常触发 B站 风控，导致已登录仍显示"未登录"）。仅当网络无法直连 B站 时才填，如 `"http://127.0.0.1:7890"` |

### `[bilibili.browser]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `executable` | string | `""` | agent-browser 路径（留空使用全局安装） |
| `headed` | bool | `false` | 是否显示浏览器窗口（调试用） |

> 运行时行为：
> 如果 `bilibili.cookie` 留空，CLI 命令和本地 API 服务会自动回退到 `auth login` 保存的 `data/bilibili_cookie.json`。
> 只有在你想显式覆盖本地登录态时，才需要把 cookie 直接写进 `config.toml`。

### `[network]` (v0.3.164+，v0.3.165 路由模式补强，v0.3.166 国内网关豁免)

海外网络路由。仅作用于**海外客户端**：OpenAI / Claude / Gemini / OpenRouter / openai_compatible 的 chat + embedding SDK、YouTube（yt-dlp、scrapetube、InnerTube / 页面 fallback）、X 的服务端 `twitter-cli`、Reddit 的 `rdt-cli` / OpenCLI 命令后端、Bangumi（`api.bgm.tv` 与封面 CDN `lain.bgm.tv` 均为海外 Cloudflare，实测 2026-07-18 国内网络直连超时、走代理正常）、GitHub 自动更新、Codex OAuth 令牌刷新。X / Reddit 回落到浏览器扩展任务时，请求由浏览器发出并沿用浏览器自己的网络设置。**注意**：`openai_compatible` / `openai` 若指向的是国内网关或本机地址，则按下方「国内网关豁免」强制直连，不受本节代理影响。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `mode` | string | `"system"`（v0.3.175 起；此前为 `"direct"`） | `system` 继承 `HTTP(S)_PROXY` / OS 代理（macOS 还含系统偏好设置里的代理）；`direct` 显式忽略环境 / 系统代理；`custom` 只使用下方 `proxy` |
| `proxy` | string | `""` | `custom` 模式的代理 URL。支持 `http://` / `https://` / `socks5://` / `socks5h://`，如 `"socks5://127.0.0.1:1080"` |

> 与 `[bilibili].proxy` 的区别：`[network].proxy` 是「海外出口」，`[bilibili].proxy` 是「B站专用」，两者语义相反、互不影响。
>
> **国内直连隔离**：B站 / 抖音 / Ollama / 国内 CDN 图片缓存等所有 `trust_env=False` 客户端**永远不使用**此代理（继承代理曾触发 B站 风控，`df626f3f`）。该隔离由 `tests/test_network_proxy_isolation.py` 守卫测试钉死。
>
> **国内大模型网关豁免（v0.3.166）**：即使 `mode` 为 `system` / `custom`，指向国内网关的 LLM 请求也会被识别并**强制直连**——DeepSeek（`api.deepseek.com`）、商汤 SenseNova（`.cn`）、通义千问（`aliyuncs.com`）、智谱、文心千帆、混元、火山方舟、Kimi、MiniMax、阶跃、百川、硅基流动、无问芯穹、PPIO 等，以及 `localhost` / 内网自建端点（cpa、vLLM 等）。识别覆盖 `.cn` 顶级域、已知厂商的非 `.cn` 域名白名单、loopback / 私有 / link-local IP，由 `openbiliclaw.network.is_domestic_endpoint` 裁决。避免「为连墙外模型开了代理 → 国内模型请求被绕道境外 → 总是超时」。豁免按 endpoint 生效，genuine 墙外网关仍走上面的代理策略。
>
> **默认值为什么是 `system`（v0.3.175）**：本节列出的全是海外服务，`direct` 下国内网络必然超时；而这是开箱默认值，新用户配好令牌、启用来源，然后撞上一句没头没尾的网络错误。`system` 即 `trust_env=True`，读取环境变量 `HTTP(S)_PROXY`，macOS 上还会读系统偏好设置里的代理；**没有配代理时 `system` 与直连完全等价**，所以海外用户无损失。国内直连隔离与国内网关豁免（上面两条）不受影响，改的只是「海外那一侧」的缺省出口。
>
> **第三方 CLI 也遵循同一语义**：`system` 保留环境代理，并将操作系统代理物化成 CLI 能读取的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`；`custom` 将配置地址显式注入这些变量（X 同时用于 `TWITTER_PROXY`）；`direct` 从子进程环境移除代理变量。运行时切换模式会重建 twitter-cli 缓存的 curl 会话。浏览器扩展 fallback 不属于后端 CLI，仍跟随浏览器网络设置。
>
> **只有「从没写过」才吃新默认值**：`_build_network_config` 按 `mode` **键是否存在**判定，不看解析后的值。`config.toml` 里显式写了 `mode = "direct"` 的照旧直连，`OPENBILICLAW_NETWORK_MODE=direct` 同理（env override 注入的是同一张表，也算显式）。因此凡是通过设置页保存过配置的用户，磁盘上已有显式 `mode`，升级后行为一律不变；受益的是全新安装与从未配置过 `[network]` 的老配置。非法值（未知模式、`custom` 但 `proxy` 为空）仍然回退 `direct` 而不是新默认值——用户确实写了东西，不该因为写错就悄悄开始继承环境代理。
>
> 旧配置只有非空 `proxy` 而没有 `mode` 时自动迁移为 `custom`；空旧配置取默认值 `system`。**API 侧同一套判定**：`PUT /api/config` 与 `POST /api/config/probe-service` 收到只带 `proxy`、不带 `mode` 的 payload（旧版 UI、第三方客户端）时，与磁盘上缺 `mode` 键走同一条路——非空 `proxy` 仍是 `custom`，清空 `proxy` 则落到 `system` 而不是 `direct`；显式送了 `mode` 的一律照送的值处理。保存时校验模式、协议与主机，`custom` 缺地址或非法值经 `PUT /api/config` 返回 400、不落盘。桌面 Web 与扩展 popup 都提供模式选择、地址输入和按当前模式真实探测；CLI `config-show` 分别显示模式与地址；移动 Web 无设置页。

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

> **关于 `daily_*_budget`：** 这些字段是**每 UTC 日、按任务类型的入队次数上限**，不是启用 / 关闭该来源的开关（来源开关是各段的 `enabled`）。`0`（或留空）表示不设每日上限，补池只受平台缺口 / `discovery_limit` / producer 节流控制。填 `1` 只会把该任务类型限制到每天 1 次——配置加载时对落在 1–4 的可疑值会打印一次 WARN 提示。

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
| `daily_search_budget` | int | `0` | 每天后端允许入队的 Soul 驱动搜索任务数上限；`0` 表示不设每日上限，持续补池只受平台缺口、单轮 `discovery_limit` 和 producer 节流控制 |
| `daily_creator_budget` | int | `0` | 每天订阅创作者抓取任务上限；`0` 表示不设每日上限 |
| `task_interval_seconds` | int | `45` | 扩展分发器两次任务之间的最小间隔（秒） |

> **安全设计要点：** 后端从不直接调用小红书搜索 / Feed API。所有"主动发现"（关键词搜索、创作者主页浏览）都在用户自己的浏览器中以后台标签页形式执行，由扩展代理完成。被动发现则利用用户正常浏览时已经加载的卡片 URL，零额外请求。

### `[sources.douyin]`

抖音专用 discovery 配置。初始化画像仍由浏览器扩展执行；本段控制 `openbiliclaw discover --source douyin` / `discover-douyin` 的内容发现。Cookie 不写进 `config.toml`：`cookie_env` 指向的环境变量优先；未设置时，后端读取浏览器扩展通过 `/api/sources/dy/cookie` 同步到 `data/douyin_cookie.json` 的值。设置页（插件 / 桌面 Web）可手动粘贴新 Cookie，但 `GET /api/config` 的 API-only `sources.douyin.cookie` 始终只返回脱敏预览（`reveal_keys=true` 也是兼容 no-op）；`PUT /api/config` 把非空、非掩码的新值路由到 `data/douyin_cookie.json`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否启用抖音 discovery。默认关闭，必须显式 opt-in |
| `mode` | string | `"direct"` | 当前仅支持 `direct`，保留字段用于后续 extension/direct 切换 |
| `cookie_env` | string | `"OPENBILICLAW_DOUYIN_COOKIE"` | douyin.com Cookie header 的环境变量覆盖名；为空时使用扩展同步文件 |
| `daily_search_budget` | int | `0` | 每日搜索插件任务预算，限制 `dy_tasks(type="search")` 入队次数；`0` 表示不设每日上限 |
| `daily_hot_budget` | int | `0` | 每日热点插件任务预算，限制 `dy_tasks(type="hot")` 入队次数；`0` 表示不设每日上限，正数时 runtime 抖音缺口较大时会把有效预算临时抬高到 `max(配置值, min(缺口, 60))` |
| `daily_feed_budget` | int | `0` | 每日首页推荐流插件任务预算，限制 `dy_tasks(type="feed")` 入队次数；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `2` | direct 诊断请求的建议最小间隔；当前默认 discovery 走插件 DOM-first 链路，主要由任务预算和 runtime producer 节流保护 |

当前 `search` 子来源使用浏览器插件的登录会话，从抖音首页通过 DOM 搜索框输入 / 提交触发页面加载，并以 `dy-plugin-search` 进入 discovery；`hot` 子来源同样从首页点击热榜 / 热点入口和目标热词，并以 `dy-plugin-hot-related` 进入 discovery；`feed` 子来源在首页推荐流滚动触发加载，并以 `dy-plugin-feed` 进入 discovery。插件只被动监听页面自己发出的响应和已渲染 DOM，不主动跳 `/search/...`、`/hot/...` 快捷 URL，也不主动调用 search / related / feed API bridge。插件任务空 / 失败时默认返回 0 条；direct-cookie fallback 仅保留给显式 `allow_direct_fallback=True` 的诊断代码。因 daemon 重启或插件未及时消费而被清理的 `failed/stale_pending` 任务不消耗正数每日预算。runtime 大缺口补池会优先 search / hot，feed 只用于小缺口补零散名额。`msToken` 如果存在会随 Cookie 一起使用，但扩展同步不再硬依赖它。若 Cookie 过期、页面布局变化或插件未在线，命令可能返回 0 条并提示检查登录态。

### `[sources.youtube]`

YouTube discovery 配置。初始化画像由浏览器扩展读取观看历史 / 订阅 / 点赞，也可通过 `import-youtube` 导入 Google Takeout；steady-state discovery 由后端 `YoutubeDiscoveryProducer` 独立调度 `yt_search` / `yt_trending` / `yt_channel` 三个策略。这里的预算是可选每日执行上限；默认 `0` 表示不设每日上限，每轮执行规模由平台缺口和 `scheduler.discovery_limit` 决定，行为与 B 站补池保持一致。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 YouTube 参与候选池配比和后台 discovery；`init --yes-youtube` 会写回 `true`，`--no-youtube` 或 `OPENBILICLAW_NO_YOUTUBE=1` 会写回 `false` |
| `daily_search_budget` | int | `0` | `yt_search` 每天最多生成 / 执行的 YouTube 搜索 query 数；`0` 表示不设每日上限，本轮 query 数由平台缺口 / `discovery_limit` 决定 |
| `daily_trending_budget` | int | `0` | `yt_trending` 每天最多拉取的热门候选数；`0` 表示不设每日上限，本轮拉取规模由平台缺口 / `discovery_limit` 决定 |
| `daily_channel_budget` | int | `0` | `yt_channel` 每天最多选择的订阅频道数；`0` 表示不设每日上限，本轮频道数由平台缺口 / `discovery_limit` 决定 |
| `request_interval_seconds` | int | `2` | 预留的 YouTube 请求间隔配置；当前策略主要由单轮预算和 runtime 补池节奏控制 |
| `min_interval_minutes` | int | `60` | `YoutubeDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

### `[sources.twitter]`

X (Twitter) discovery 配置。X 是第六个内容源，发现走**服务端 cookie 重放**（对标 `[sources.douyin]` 的 direct 模式），由后端 `XDiscoveryProducer` 调度 `search`（画像驱动关键词）/ `feed`（推荐流 For-You）/ `creator`（账号订阅）三个策略，把推文灌入统一候选池。行为采集（用户在 x.com 上自己的点赞 / 收藏 / 回复）走浏览器扩展 MAIN-world tap，与本段无关。Cookie 不写进 `config.toml`：`cookie_env` 指向的环境变量优先；未设置时，后端读取浏览器扩展通过 `/api/sources/x/cookie` 同步到 `data/x_cookie.json` 的 `auth_token` + `ct0`。设置页（插件 / 桌面 Web）可手动粘贴新 Cookie，但 `GET /api/config` 的 API-only `sources.twitter.cookie` 始终只返回脱敏预览（`reveal_keys=true` 也是兼容 no-op）；`PUT /api/config` 把非空、非掩码的新值路由到 `data/x_cookie.json`，含 `auth_token` + `ct0` 的有效粘贴会同时解除 re-login 健康封锁。X 客户端 `XClient` 封装默认安装自带的 `twitter-cli`，只在 `enabled=true` 且真正 fetch 时 lazy import，`enabled=false` 路径绝不 import；`openbiliclaw[x]` 仍保留为兼容旧脚本的安装别名。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 X 参与候选池配比和后台 discovery。默认关闭，必须显式 opt-in；`init --yes-x` / 插件设置页 X 源卡 / `--no-x` 会写回对应值。关闭后 `XDiscoveryProducer` 不下发任何任务，`pool_source_shares.twitter` 配额从有效配比中剔除，`twitter-cli` 也不会被 import |
| `mode` | string | `"cookie"` | 当前仅支持 `cookie`（服务端 cookie 重放）；保留字段 |
| `cookie_env` | string | `"OPENBILICLAW_X_COOKIE"` | x.com Cookie（含 `auth_token` + `ct0`）的环境变量覆盖名，优先级高于 `data/x_cookie.json`；为空时使用扩展同步文件 |
| `daily_search_budget` | int | `0` | `search` 策略每日抓取预算；`0` 表示不设每日上限，本轮规模由平台缺口 / `discovery_limit` 决定 |
| `daily_feed_budget` | int | `0` | `feed`（推荐流 For-You）每日拉取预算；`0` 表示不设每日上限。For-You 抓首页 home timeline 最易被注意，建议压低；producer 还会把 For-You 节流到很低的每日频次，并在连续失败后自动暂停 |
| `daily_creator_budget` | int | `0` | `creator`（账号订阅）每日抓取预算；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `3` | 两次 X 请求之间的最小间隔（抗检测）；TLS 指纹由 `twitter-cli`（`curl_cffi`）负责 |
| `min_interval_minutes` | int | `60` | `XDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

X 源健康状态（`ok` / `missing_cookie` / `expired_cookie` / `rate_limited` / `blocked`）由 `storage/x_health.py` 持久化，按 401 / 403 / 429 分别退避，连续 For-You 失败会自动暂停 For-You 拉取，状态经 `GET /api/sources/x/status` 暴露到插件 / 桌面 Web 设置页。账号订阅用 `x_creator_subscriptions` 表持久化，经 `GET/POST/DELETE /api/sources/x/creators` 管理。

### `[sources.zhihu]`

知乎 discovery 配置。知乎是浏览器插件登录态源：后端入队 `zhihu_tasks`，插件在已登录 `zhihu.com` 标签页中执行 `search` / `hot` / `feed` / `creator` / `related` 任务并把 `zhihu_*` 候选回写，后端再转换为 `source_platform="zhihu"` 的 `DiscoveredContent` 写入统一待评估候选池。`fetch-zhihu` 的事件 smoke 也复用同一张 `zhihu_tasks` 表，但任务类型是 `bootstrap_events`，命令本身只打印计数、不写 memory；guided init 里选择知乎时会显式收集同一类 `bootstrap_events` 结果，把浏览 / 收藏 / 点赞 / 动态收藏转换为首轮画像信号，并写回 `enabled=true`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让知乎参与候选池配比和后台 discovery。默认关闭，必须显式 opt-in；关闭后 `ZhihuDiscoveryProducer` 不入队任务，`pool_source_shares.zhihu` 配额从有效配比中剔除 |
| `source_modes` | list[str] | `["search", "hot", "feed", "creator", "related"]` | 后台和 `openbiliclaw discover --source zhihu` 允许调度的知乎 discovery 分支。插件 side panel 与桌面 Web 配置页都提供五个显式勾选项。`search` 使用统一关键词 planner；`hot` 拉热榜；`feed` 拉首页推荐；`creator` 优先用最近任务结果里的作者主页作种子，没有历史种子时使用本轮 search / hot / feed 返回的作者页；`related` 优先用最近知乎候选 URL，没有历史种子时使用本轮已返回内容 URL 作相关扩展种子 |
| `daily_search_budget` | int | `0` | 知乎搜索 discovery 每日任务预算；`0` 表示不设每日上限，本轮关键词数由统一关键词 planner / fallback 画像兴趣和平台缺口决定 |
| `daily_hot_budget` | int | `0` | 知乎热榜 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_feed_budget` | int | `0` | 知乎首页推荐 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_creator_budget` | int | `0` | 知乎作者 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_related_budget` | int | `0` | 知乎相关扩展 discovery 每日任务预算；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `3` | 后端等待任务时的轮询间隔 / 插件搜索节奏提示；真实平台请求仍发生在用户已登录浏览器内 |
| `min_interval_minutes` | int | `60` | `ZhihuDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

### `[sources.reddit]`

Reddit 来源配置。Reddit 日常 discovery 默认走随 OpenBiliClaw 安装的 `rdt-cli` 登录态命令后端；已连接浏览器插件会把 `reddit_session` 自动同步到 `~/.config/rdt-cli/credential.json`，插件不可用时才需要手动运行 `rdt login`。Cookie 不写进 `config.toml`：桌面 Web 设置页的 Reddit Cookie 覆盖输入框可手动粘贴（`PUT /api/config` 的 `sources.reddit.cookie` 为 API-only 字段，非 `config.toml` 键），非空新值路由到 rdt-cli credential store，与插件自动同步同一存储；粘贴内容缺少 `reddit_session` 时保存以 400 `missing_reddit_session` 显式拒绝，不静默丢弃。后端会拉取 `search` / `hot` / `subreddit` / `related` 候选后转换为 `source_platform="reddit"` 的 `DiscoveredContent` 并只写入统一待评估候选池；LLM 评估和入正式推荐池由后台 `DiscoveryCandidatePipeline` 统一处理。初始化阶段仍可入队 `reddit_tasks(type="bootstrap_events")`，插件在已登录 `reddit.com` 会话里读取 saved / upvoted / subscribed subreddit 并转换为 `favorite` / `like` / `follow` 画像信号。`extension` 可显式作为浏览器登录态 discovery 后端；默认 `rdt` / `opencli` 命令后端不可用或未登录时也会自动 fallback 到插件任务。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 Reddit 参与初始化 opt-in、候选池配比和后台 discovery。默认关闭，必须显式 opt-in；关闭后 `RedditDiscoveryProducer` 不入队任务，`pool_source_shares.reddit` 配额从有效配比中剔除 |
| `backend` | string | `"rdt"` | Reddit 取数后端。`rdt` 使用默认安装的 rdt-cli 登录态命令后端，并优先使用插件同步的 `reddit_session` credential；`rdt login` 仅作为手动 fallback；`extension` 使用 OpenBiliClaw 浏览器插件和当前浏览器登录态，且仍负责 bootstrap 初始化信号；`opencli` / `auto` 为兼容命令路径。命令后端状态不是 `ready` 时，CLI / producer 会自动 fallback 到插件任务 |
| `source_modes` | list[str] | `["search", "hot", "subreddit", "related"]` | 后台和 `openbiliclaw discover --source reddit` 允许调度的 Reddit discovery 分支。`search` 使用统一关键词 planner，关键词池为空时回退画像兴趣；`hot` 默认拉 `r/all`；`subreddit` 优先用最近 Reddit 候选里的 subreddit 作种子；`related` 优先用最近 Reddit 内容 URL 作相关扩展种子 |
| `daily_search_budget` | int | `300` | Reddit 搜索 discovery 每日条目预算 |
| `daily_hot_budget` | int | `300` | Reddit 热门 discovery 每日条目预算 |
| `daily_subreddit_budget` | int | `300` | Reddit subreddit discovery 每日条目预算 |
| `daily_related_budget` | int | `300` | Reddit related discovery 每日条目预算 |
| `request_interval_seconds` | int | `3` | 后端等待任务时的轮询间隔 / 插件任务节奏提示；真实平台请求发生在用户已登录浏览器内 |
| `min_interval_minutes` | int | `60` | `RedditDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

### `[sources.bangumi]`

Bangumi 使用官方 `https://api.bgm.tv/v0` 只读 API，默认匿名，不需要 Cookie 或浏览器登录态。启用后，`BangumiDiscoveryProducer` 按 `search / ranked / latest` 抓取 Subject，只写入统一待评估池；用户显式填写公开用户名后，guided init 还可以读取该账号的公开收藏作为画像信号。可选配置个人令牌（Personal Access Token）：设置后 init/discovery 经 `GET /v0/me` 自动识别账号并带 `Authorization: Bearer` 读取本人收藏（含私密）。首版不调用任何 Bangumi 站内写接口。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 Bangumi 参与候选池配比和后台 discovery；默认关闭，关闭时保留配置值但从有效 share 中剔除 |
| `username` | string | `""` | 可选公开 Bangumi 用户名，仅用于公开收藏初始化；匿名 discovery 不需要。保存时裁剪首尾空白，并拒绝路径分隔符、控制字符和超长值 |
| `access_token` | string | `""` | 可选个人令牌，在 https://next.bgm.tv/demo/access-token 自助生成（约 1 年有效）。设置后所有 Bangumi 请求带 Bearer，init/同步经 `/v0/me` 自动识别用户名并可读私密收藏；留空保持匿名公开用户名老路。保存时做结构校验（单行 ASCII、≤512 字符），guided init 提交的令牌先经 `/v0/me` 实测通过才落盘，坏令牌当场拒绝。令牌视同密码：只存 gitignored 的 `config.toml`，日志与设置 GET 响应不回传明文 |
| `subject_types` | list[str] | `["anime", "book", "game"]` | 允许的条目类型：`book / anime / music / game / real`；桌面 Web 与扩展设置页提供全部五类，默认勾选核心三类 |
| `source_modes` | list[str] | `["search", "ranked", "latest"]` | producer 分支；`latest` 的真实语义是官方 `sort=date`，可能包含未播条目 |
| `daily_search_budget` | int | `300` | 每 UTC 日最多抓取的搜索条目数；`0` 表示不设每日上限，本轮仍受平台缺口和 `discovery_limit` 控制 |
| `daily_ranked_budget` | int | `100` | 每 UTC 日最多抓取的排名条目数；`0` 表示不设每日上限 |
| `daily_latest_budget` | int | `100` | 每 UTC 日最多抓取的日期浏览条目数；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `1` | 同一 client 两次官方 API 请求间的本地最小间隔；`0` 仅适合显式诊断/测试 |
| `min_interval_minutes` | int | `60` | producer 两次执行之间的最小间隔；`--force` 可跳过本次检查，但不能绕过上游 `429` cooldown |
| `bootstrap_limit` | int | `300` | guided init / `fetch-bangumi` 默认公开收藏上限，保存范围 `1..1000` |

用户名不是登录凭据。guided init 的账号解析按三级优先取值：个人令牌 `/v0/me` > 显式/已配置公开用户名 > 浏览器扩展在已登录 bgm.tv 页面自动识别并上报的用户名（`discovery_runtime_state["bangumi_self_info"]`，见 extension 文档）；Bangumi-only guided init 三者至少满足一个，混合初始化全部缺失时只跳过 Bangumi 画像分支并提示“仍可用于 discovery”。init 请求显式发送空 username 时会覆盖并清除旧配置值；只有 username 字段缺失的旧客户端才回退已保存值。令牌存在时以 `/v0/me` 解析出的用户名为准（与显式用户名不一致会 WARNING 并覆盖）；同步期令牌被拒绝（401）时记 WARNING 并降级到匿名公开路径，不静默失败。完整边界见 [Bangumi 来源文档](bangumi.md)。

#### 配置页来源状态契约

插件 side panel 与桌面 Web `/web` 的平台源配置页统一读取 `GET /api/sources/status`。这个端点是**纯本地读取**：不会访问 Bilibili、小红书、抖音、YouTube、X、知乎、Reddit 或 Bangumi，也不会运行 `rdt` / `opencli` 命令。页面可见时每 30 秒刷新一次，但请求只到 OpenBiliClaw 本地后端；真实平台请求仅由用户显式初始化、发现、诊断任务或已启用的后台 producer 发起。

状态语义如下：

| 状态 | 配置页文案 | 含义 |
|------|------------|------|
| `ok` | 接入可用 | 之前的真实任务 / 健康检查已验证（当前仅 X 健康状态机使用）；读取状态页本身不会再验证 |
| `ready` | 凭据已就绪 | 本地凭据结构完整，或浏览器刚同步为已登录；不等于本次刷新访问平台成功 |
| `unverified` | 状态待验证 | 已配置凭据但尚未由实际任务验证，或浏览器登录态从未同步 |
| `missing` / `login_required` | 需要登录 | 本地无凭据，或浏览器最近明确同步为未登录 |
| `partial` | 部分可用 | 本地凭据不完整 |
| `stale` | 需要刷新 | 最近同步的浏览器登录态或 credential 已过期 |
| `error` | 检查失败 | 本地 credential 文件不可读或格式无效 |
| `no_auth` | 无需登录 | 公开来源 |

平台特例：抖音只要本地 Cookie 存在即显示 `unverified`，必须由实际抖音任务确认；小红书 / 知乎优先使用插件上报的 `logged_in + updated_at`，知乎仅在从未收到浏览器心跳时回落最近任务历史；Reddit `backend="rdt"` 只读取本地 credential 文件，非 rdt 命令后端在状态页显示 `unverified`。Bangumi 不探测登录，状态由本地开关与最近 producer run ledger 计算为 `disabled / unverified / ready / partial / rate_limited / error`。`xsec_token` 只是小红书内容 URL 的访问令牌，配置页即使能展示它也不会据此判断账号已登录。

### `[scheduler]`

TOML 与显式环境变量覆盖在构造 `SchedulerConfig` 前统一归一为真实布尔值。`enabled`、`pause_on_extension_disconnect`、`profile_consolidation_enabled`、`profile_consolidation_archive_enabled`、`auto_update_enabled`、`auto_update_allow_prerelease` 接受常见 `true/false/1/0`；空字符串或无法识别的值回到各字段默认值，绝不会把字符串 `""` 留到 API 响应后再触发类型校验错误。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 后台 LLM / embedding 工作总开关；插件设置页显示为「停止后台 LLM 请求」。关闭后 runtime 的刷新、补池预计算、账户同步、猜测兴趣和主动推送等 daemon-owned 后台任务都会跳过；手动 CLI / API 请求仍按显式操作执行。若候选池为空，推荐页可能暂时没有内容 |
| `pause_on_extension_disconnect` | bool | `false` | 开启后，daemon-owned 后台 LLM / embedding 工作只在浏览器插件有 `/api/runtime-stream` 连接、或刚断开仍处于宽限窗口内时运行；离线期间不会自动补新内容 |
| `extension_disconnect_grace_seconds` | int | `90` | 插件最后一个 `runtime-stream` 连接断开后的宽限秒数；小于等于 0 或无法解析时回退到 `90` |
| `discovery_cron` | string | `"0 */8 * * *"` | 兼容旧配置的保留字段；当前 runtime 不消费这个 cron，发现补池由轮询、候选池缺口、行为阈值和下方策略间隔驱动。插件与桌面 Web 设置页均不再暴露该字段，只能通过手改 `config.toml` 保留 |
| `pool_target_count` | int | `300` | 前端真实可换候选目标；允许范围 `1..600`。`count_pool_candidates()`（含预生成 / 分类 / 可打开 / 最近看过过滤 / topic window）达到目标时 refresh（含 `force_refresh`）返回 `pool_at_cap` 不再 discover；后台定时 refresh 采用约 90% 的低水位，略低于目标时不立即跑 discovery，等库存真正低于水位再补货。raw 素材库存由独立 raw ceiling `max(pool_target_count * 2, pool_target_count + 120)` 控制，不再被压成与可换目标相同 |
| `account_sync_interval_hours` | int | `6` | 账户侧长期信号同步间隔；运行时会低频拉取 history / favorites / following |
| `refresh_check_interval_seconds` | int | `60` | `ContinuousRefreshController` 主循环轮询间隔；小于 `15` 或无法解析时回退默认值 |
| `signal_event_threshold` | int | `6` | 累计多少条新行为事件后触发 `search + related_chain` 补池；小于 `1` 时回退默认值 |
| `trending_refresh_hours` | int | `3` | `trending` 策略的最小刷新间隔；小于 `1` 时回退默认值 |
| `explore_refresh_hours` | int | `12` | `explore` 策略的最小刷新间隔；小于 `1` 时回退默认值。统一关键词 planner 复用同一时钟：当该间隔已到或距到期不足一个 `refresh_check_interval_seconds`，且 B 站仍有补货空间时，会把探索 query 生成合并进当轮关键词调用 |
| `discovery_limit` | int | `30` | 单轮 discovery wave 的候选上限；允许范围 `1..60` |
| `delight_queue_limit` | int | `20` | 惊喜推荐队列默认加载数量；允许范围 `1..100`。桌面 Web、移动 Web 和浏览器插件默认调用 `/api/delight/pending-batch` 时共享该值，显式 query `limit` 可临时覆盖 |
| `proactive_push_interval_seconds` | int | `120` | 主动推荐 / probe 推送循环间隔；小于 `30` 时回退默认值 |
| `speculator_idle_interval_minutes` | int | `30` | `ProfileUpdatePipeline` 空闲时检查猜测兴趣生命周期的间隔；小于 `5` 时回退默认值 |
| `profile_consolidation_enabled` | bool | `true` | 是否启用 12 小时画像整理（LLM 合并重复的喜欢 / 讨厌主题，见 soul 模块 `ProfileConsolidator`） |
| `profile_consolidation_interval_hours` | int | `12` | 画像整理的最小间隔（小时）；输入未变化（digest 相同）且 active likes 未超过库存上限时该轮零 LLM 调用 |
| `profile_consolidation_like_target_upper` | int | `512` | active likes 目标上限；超过该值时整理会临时使用 full boundary，并在合并后尝试归档低权重长尾 |
| `profile_consolidation_like_target_soft` | int | `450` | active likes 整理水位；归档开启时会尽量把 active likes 降到该值（实际使用 `min(soft, upper)`） |
| `profile_consolidation_archive_enabled` | bool | `true` | 合并后仍超过上限时，是否把低权重、非用户保护的兴趣移入 `archived_interests` |
| `speculation_interval_minutes` | int | `10` | 猜测兴趣推测的运行间隔（分钟） |
| `speculation_ttl_days` | int | `3` | 猜测兴趣的默认存活天数 |
| `speculation_cooldown_days` | int | `7` | 猜测兴趣被否定后的冷却天数 |
| `speculation_confirmation_threshold` | int | `3` | 需要多少次正向信号确认猜测兴趣 |
| `speculation_max_active` | int | `5` | 最多同时活跃的猜测兴趣数 |
| `speculation_max_primary_interests` | int | `15` | 主要兴趣域的最大数量 |
| `speculation_max_secondary_interests` | int | `60` | 次要兴趣域的最大数量 |
| `avoidance_speculation_interval_minutes` | int | `10` | 不喜欢领域探针生成间隔（分钟），与正向兴趣探针独立 |
| `avoidance_speculation_ttl_days` | int | `3` | 不喜欢领域探针默认存活天数 |
| `avoidance_speculation_cooldown_days` | int | `7` | 不喜欢领域探针被否认或过期后的冷却天数 |
| `avoidance_speculation_confirmation_threshold` | int | `3` | 自动确认不喜欢领域所需显式负向信号数；用户直接确认不受此阈值限制 |
| `avoidance_speculation_max_active` | int | `5` | 最多同时活跃的不喜欢领域探针数，不占 `speculation_max_active` |
| `auto_update_enabled` | bool | `false` | 是否启用后端自动检查并应用新版本；默认关闭，只影响后端源码，不更新浏览器插件 |
| `auto_update_check_interval_hours` | int | `6` | 后端自动更新检查间隔（小时），最小 `1`；TOML 中的 `0` / 负数 / 非整数字符串加载时回退安全默认值 `6`，`PUT /api/config` 与 `save_config()` 对非整数或 `<1` 的值直接拒绝且不落盘；手动检查不受该间隔限制 |
| `auto_update_allow_prerelease` | bool | `false` | 是否允许 `backend-vX.Y.Z-rc/beta/dev` 预发布 tag 被后端自动更新选择；默认忽略 |
| `auto_update_allowed_remotes` | list[str] | OpenBiliClaw GitHub HTTPS / SSH | 允许自动更新快进的 `origin` allowlist；守卫校验 `ls-remote --get-url` 改写后的地址和 `remote get-url --all` 的全部值，任一不可信即拒绝且绝不自动改写 git 配置。规范化支持可选 `.git`、大小写不敏感与 GitHub 官方 `ssh.github.com[:443]` 等价；镜像包装、带凭据或未匹配地址继续以 `untrusted_remote` 拒绝 |

> 运行时护栏：
> 即使 `pool_target_count` 设得较高，单次 refresh 里的 discover wave 也由 `discovery_limit` 控制（默认 `30`，最大 `60`），避免一次性把全部缺口都打满。
> 后台 refresh 还会使用约 90% 的可换池低水位；池子只是轻微低于 `pool_target_count` 时不跑 discovery。B 站完整四策略补货在小缺口阶段优先只给 `search + related_chain` 预算，`trending/explore` 延后到更深缺口。
> `pause_on_extension_disconnect` 只约束后端 daemon 自己发起的后台 LLM / embedding 工作；用户手动点击刷新、CLI 显式命令、配置保存和普通读取接口不因为插件离线而被拦截。`runtime-stream` 连接断开由后端 receive-side detector 记录，浏览器 idle disconnect 后不会让 presence 状态卡住。

### `[scheduler.pool_source_shares]`

候选池按平台族做保底配比，默认保存的 share 是 `bilibili:xiaohongshu:douyin:youtube:twitter:zhihu:reddit:bangumi = 5:1:1:1:1:1:1:1`。旧配置文件若已有本段但缺少后续新增的平台 key，加载时会自动补齐默认 share（例如 `bangumi = 1`）。关闭的平台会保留配置值但在运行时从有效配比中剔除，剩余平台重新归一化吃满 `pool_target_count`；默认安装里小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi 都关闭，所以默认有效配比只有 Bilibili。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `bilibili` | int | `5` | B 站平台族占比；`search` / `related_chain` / `trending` / `explore` 四个策略统一计入该族 |
| `xiaohongshu` | int | `1` | 小红书平台族占比；`xhs-extension-*` 原始来源统一计入该族 |
| `douyin` | int | `1` | 抖音平台族占比；`dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 等统一计入该族 |
| `youtube` | int | `1` | YouTube 平台族占比；`yt_search` / `yt_trending` / `yt_channel` 统一计入该族 |
| `twitter` | int | `1` | X (Twitter) 平台族占比；`search` / `feed`（For-You）/ `creator`（账号订阅）三个策略统一计入该族 |
| `zhihu` | int | `1` | 知乎平台族占比；插件 `zhihu-search` / `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 候选统一计入该族 |
| `reddit` | int | `1` | Reddit 平台族占比；插件 / 命令后端 `reddit-search` / `reddit-hot` / `reddit-subreddit` / `reddit-related` 候选统一计入该族 |
| `bangumi` | int | `1` | Bangumi 平台族占比；`bangumi-search` / `bangumi-ranked` / `bangumi-latest` 统一计入该族 |

运行时会拆分两套 quota：前端可换来源目标用于补货和 `reactivate_under_quota_pool_sources()` 的缺口判断；raw ceiling 来源目标用于 `trim_pool_source_overflow()` / `trim_pool_to_target_count()` 的硬成本边界。小平台低于可换目标时，会优先保护 / 复活它们的候选，但不会超过 raw headroom；任一平台族 raw material 高于 raw ceiling 配额时，才会先压回配额内。B 站低于后台低水位且 `[sources.bilibili].enabled=true` 时，才由 B 站 discovery 补货；小缺口优先 `search + related_chain`，更深缺口再跑 `trending/explore`。抖音低于目标且 `[sources.douyin].enabled=true` 时，后台 `DouyinDiscoveryProducer` 会通过 `DouyinDiscoveryService(cache=True)` 触发 search / hot / feed 补池；YouTube 低于目标且 `[sources.youtube].enabled=true` 时，后台 `YoutubeDiscoveryProducer` 会在独立 loop 中触发 `yt_search` / `yt_trending` / `yt_channel`，主 refresh replenishment plan 不再 inline 调度 YouTube；X 低于目标且 `[sources.twitter].enabled=true` 时，后台 `XDiscoveryProducer` 会在独立 loop 中按预算和源健康触发 `search` / `feed` / `creator` 三个策略补池；知乎低于目标且 `[sources.zhihu].enabled=true` 时，后台 `ZhihuDiscoveryProducer` 会通过浏览器插件按 `source_modes` 触发 search / hot / feed / creator / related 补池；Reddit 低于目标且 `[sources.reddit].enabled=true` 时，后台 `RedditDiscoveryProducer` 默认通过 `rdt-cli` 按 `source_modes` 触发 search / hot / subreddit / related 补 raw candidates；命令后端不可用或显式切到插件后端时，入队 OpenBiliClaw 插件任务。Bangumi 低于目标且 `[sources.bangumi].enabled=true` 时，后台 `BangumiDiscoveryProducer` 直连官方匿名 API，按分支预算写 raw candidates，并遵循持久化限流冷却。

`openbiliclaw init` 会根据用户是否接入小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi 写回对应 `enabled`。其中知乎在 `fetch-zhihu` 命令下仍只是事件爬取 smoke；在 guided init 勾选知乎或传 `--yes-zhihu` 时，`bootstrap_events` 会作为首版画像信号参与 `analyze_events()` / `build_initial_profile()`。Reddit 同样支持 guided init：勾选 Reddit 或传 `--yes-reddit` 时，插件读取 saved / upvoted / subscribed subreddit，每个 scope 默认最多 300 条，并把事件纳入首版画像；`fetch-reddit --mode bootstrap` 可单独验证这条事件拉取链路。Bangumi 选择后只在提供公开 username 时读取公开收藏；没有 username 仍可作为 discovery 源。Bilibili 默认启用，也可在插件设置页或 `config.toml` 里手动关闭。交互式初始化在采集完各平台事件后，会按事件量给出一组推荐比例，用户可确认使用或手动输入。插件设置页也可开关八个平台、编辑八个平台占比，并通过 `/api/config/source-share-suggestion` 按已有事件重新生成建议值；GET 使用已保存配置，POST 可接收设置页当前尚未保存的 `enabled_sources` / `configured_shares`。

### `[discovery]`

**统一关键词规划器 / Discover 背压 / 评估输入**（`DiscoveryConfig`）。把"每平台各自定时调 LLM 生成搜索词"换成**缺口拉动的双缓冲背压模型**：一个关键词存储（cache + 历史 + 产出）夹在「生成」与「抓取」之间，生成只在缓存见底且池子有真实缺口时触发（一次合并 LLM 调用覆盖所有缺货平台，带历史去重 + 池子分布避让）。B 站 explore 方向也复用这条关键词存储：到达 `[scheduler].explore_refresh_hours` 的 refresh plan 窗口且 B 站有补货空间时，planner 会把 `explore_domains` 合并进同一次关键词生成，而不是新增配置项或单独 caller。同一段也承载 discovery evaluator 的可选封面图输入开关。本段**与 `[llm.routes.discovery]` 是两个独立的表**——后者选择 discovery 模块使用的 Provider 实例链，本段是规划器 / 背压 / 评估输入调参。完整设计见 [`docs/plans/2026-06-14-discover-backpressure-refactor-design.md`](../plans/2026-06-14-discover-backpressure-refactor-design.md) §6 参数表。

> ✅ `unified_keyword_planner_enabled` **v0.3.124 起默认 `true`**：搜索词走统一规划器 + 关键词存储，本段其余字段随之生效。设为 `false` 可逐字回退到旧的逐平台搜索词生成路径（旧路径保留、回退无副作用）。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `unified_keyword_planner_enabled` | bool | `true` | 统一关键词规划器总开关（v0.3.124 起默认 `true`）。`true` = 走 planner + 关键词存储；`false` = 回退旧逐平台搜索词生成。其余字段仅在 `true` 时生效 |
| `kw_cache_high` | int | `30` | 每平台关键词缓存高水位；生成补到这个数。小于 `1` 或无法解析时回退默认值 |
| `kw_cache_low` | int | `10` | 每平台关键词缓存低水位；`pending < low` 且有真实缺口时触发生成。小于 `1` 时回退默认值 |
| `gen_batch` | int | `30` | 单平台单次合并 LLM 调用生成的关键词数。小于 `1` 时回退默认值 |
| `fetch_batch` | int | `5` | 单次原子领取（claim）的关键词数。小于 `1` 时回退默认值 |
| `history_window_size` | int | `150` | 去重窗口大小：最近最多这么多个关键词作为"别再出"喂给 planner。小于 `1` 时回退默认值 |
| `history_window_hours` | int | `48` | 去重窗口时长（小时），与 `history_window_size` 配合滚动过期。小于 `1` 时回退默认值 |
| `claim_lease_minutes` | int | `10` | 领取租约（分钟）：`claimed`/`executing` 超过这个时长未变会被回收成 `pending`，防 loop / 任务崩溃泄漏在途行。小于 `1` 时回退默认值 |
| `planner_poll_seconds` | int | `120` | 关键词规划器轮询间隔（秒）；空闲轮询近似零成本。小于 `1` 时回退默认值 |
| `plan_ttl_hours` | int | `12` | 兜底失效（小时）：即便画像 `profile_kw_digest` 未变，`pending` 关键词超过这个时长也会过期；同画像、同平台需求块、同池子避让提示的 merged keyword 生成结果也按这个 TTL 在进程内复用。小于 `1` 时回退默认值 |
| `admission_min_score` | float | `0.60` | 普通推荐池统一入池最低分。候选行 / raw payload 显式 `score_threshold` 可作为策略阈值覆盖；来源标签如 `admission_policy="observed"` 不能绕过该分数门。探索类策略可略低于该值，但平台 / 插件来源不能获得特权。必须在 `(0, 1]` 内，非法值回退默认值 |
| `candidate_eval_concurrency` | int | `3` | 候选 LLM 评估的期望 worker 数，合法范围 `1..3`；每个 worker 最多 30 条，因此总 raw 在途上限为 90。超出范围的手工 TOML / API 值按本段既有整型规则回退默认 `3`。有效值为 `min(本值, max(1, llm.concurrency-1))`，为聊天等交互保留一个全局 LLM 槽位；插件与桌面 Web 设置页可修改，CLI `config-show` 自动显示。移动 Web 没有配置面板，不适用。 |
| `inspiration_search_enabled` | bool | `false` | 是否启用 query inspiration 脑暴阶段。开启后 `KeywordPlanner` 会通过本机 mcporter 搜索 provider 链获取搜索预览，再让 `discovery.keyword_inspiration` LLM caller 做 Profile Curator / Detail Expander，最终把带 `aspect_id/inspiration_id/expansion_id` 元数据的关键词写入 `discovery_keywords` |
| `inspiration_search_backends` | list[str] | `["local_cache", "platform_sources", "exa", "you"]` | query inspiration 搜索后端顺序。`local_cache` 会先从本地 `content_cache` 抽取相关标题 / URL / 摘要作为 evidence，本地命中不消耗外部 grounding 预算；证据不足时才 fallback。`platform_sources` 会从用户已启用且当前可同步/可注入 bridge 的平台源里抽样做 inspiration-only grounding（B站 / YouTube / X / Reddit；抖音 direct client；小红书 / 知乎 bridge 可用时），只把标题 / URL / 摘要作为灵感证据，不写候选池；`exa` 调用 `mcporter call exa.web_search_exa`；`you` 调用 `mcporter call you.you-search`（You.com Free MCP profile）。某个后端报错 / 限流 / 返回空结果时会继续尝试后面的后端。远端 MCP server 需要先写入本机 `config/mcporter.json` |
| `inspiration_replace_merged_keywords` | bool | `false` | 实验性替换模式。仅在 `inspiration_search_enabled=true` 且 inspiration provider 可用时生效：due 平台跳过旧 `discovery.keyword_planner` merged call，只通过 search-backed inspiration flow 产词；当 B 站 explore 到期且有补货空间时，也会用同一轮共享 brainstorm / grounding stage 写入 `keyword_kind="explore"` 的探索词池。开 replace 前应先用 `keyword-inspiration-report` 跑 cohort 门禁，避免无质量数据直接替换 |
| `inspiration_breadth` | str | `"high"` | 探索广度档位（Phase 2 config 收敛，13→4）：`low` / `medium` / `high`。旧的 10 个 `inspiration_*` 细粒度旋钮已删除，其派生成内部常量的有效值由本档位决定（见下表）。**默认 `high`（更宽的素材/轴/关键词产量）**；`medium` 逐项等于旧的 `_DEFAULT_INSPIRATION_*` 默认值，需与收敛前行为逐项对齐时显式设 `medium`。注意 `high` 会把每轮真实 probe 搜索与 LLM 用量放大（daemon 常驻），成本敏感可设 `medium`/`low`。非法档位（非 `low`/`medium`/`high`）→ 配置错误（`ConfigError`），未设置回退 `high` |
| `multimodal_evaluation_enabled` | bool | `false` | 是否在 discovery batch evaluator 中加入候选封面图。默认关闭；开启后仅当当前 evaluation 路由支持图像输入且候选有 `cover_url` 时使用，否则自动退回纯文本评估 |
| `danmaku_enabled` | bool | `false` | 是否启用**弹幕文本**加成（P2）：B 站候选喂给推荐的语义只有 `title` + `description`，而 description 常是"求三连"之类的无信息文本、`body_text` 在 B 站路径恒为空；弹幕是 B 站独有信号，反映观众实际在讨论什么。抓取走 `comment.bilibili.com/{cid}.xml`（**无需鉴权**，`cid` 直接从已有的 `/x/web-interface/view` 响应读，零额外请求），清洗后嵌入为独立排序信号。**纯文本信号，无需多模态嵌入模型**（与 P1/P3 不同）；仅对 B 站视频有效。默认关闭时加成恒 0，排序逐字节一致 |
| `danmaku_fetch_limit` | int | `50` | 每轮预热处理的视频数上限。合法范围 `1..200` |
| `danmaku_max_chars` | int | `500` | 弹幕摘要字数上限。合法范围 `100..2000` |
| `keyframe_enabled` | bool | `false` | 是否启用**视频关键帧**加成（P3）：封面是 UP 主手选的营销图、常常标题党，不代表视频内容；B 站已为每个视频预生成关键帧雪碧图（进度条悬停预览），一次请求即可取到，**无需下载视频、无需 ffmpeg**。用 P1 建好的口味质心去匹配真实画面而非封面，帧向量取 max-pool。需同时开 `[llm.embedding].multimodal_enabled` + 多模态嵌入模型；仅对 B 站视频有效（实测 30/30 覆盖率，时长 45s–5106s）。默认关闭时加成恒 0，排序与旧版逐字节一致 |
| `keyframe_max_frames` | int | `4` | 每个视频采样的关键帧数。合法范围 `1..12`，超范围回退默认值。相邻关键帧高度冗余，4 帧已能覆盖正片（采样跨全部雪碧图均匀分布并跳过片头片尾） |
| `keyframe_fetch_limit` | int | `50` | 每轮预热处理的视频数上限。合法范围 `1..200` |
| `visual_profile_enabled` | bool | `false` | 是否启用**用户视觉画像**加成（P1）：把点赞/踩过的推荐封面聚成 k 个均值质心，候选封面↔质心同模态余弦映射为有界加成（正向）− 有界惩罚（负向，"标题党封面"降权），在 `serve()` 排序上与封面↔文本锚点加成并行叠加。需同时开 `[llm.embedding].multimodal_enabled` + 多模态嵌入模型；与 `multimodal_evaluation_enabled` 互相独立。默认关闭/无反馈数据时加成恒 0，排序与旧版逐字节一致 |
| `multimodal_batch_size` | int | `8` | 图文评估 batch 上限。合法范围 `1..12`，超范围回退默认值；纯文本评估仍使用调用方原 batch size |
| `multimodal_image_max_px` | int | `384` | 送入评估器前封面图压缩后的最大边。合法范围 `128..768`，超范围回退默认值 |
| `multimodal_image_quality` | int | `72` | JPEG 压缩质量。合法范围 `40..90`，超范围回退默认值 |
| `multimodal_image_timeout_seconds` | int | `6` | 单张封面抓取与压缩超时秒数。合法范围 `1..20`，超范围回退默认值 |

默认 `[llm].concurrency=4`、`[discovery].candidate_eval_concurrency=3`，因此有效候选 worker 为 3，并为对话等交互保留一个总槽。高吞吐本地 profile 还可配合 `[scheduler].pool_target_count=600`、`[scheduler].discovery_limit=60`；显式旧值 `[llm].concurrency=3` 会保留为 3，此时后台与有效候选 worker 为 2。该 profile 不改变任何平台 `request_interval_seconds` / `min_interval_minutes`、daily budget、来源 share、raw ceiling 公式或 `admission_min_score`。

> **没有 `fetch_floor` 字段**：抓取最小间隔复用各平台已有的 `min_interval`（小红书 1h / 抖音 30m / YouTube·X 60m / B 站按风控），不在本段重复定义。
>
> **封面图评估能力边界**：当前通过 OpenAI-compatible `image_url` 消息格式发送压缩后的 `data:image/jpeg;base64,...`。`LLMService.supports_image_input()` 只会在当前 evaluation provider / model 明确看起来支持图像时开启；否则开关保持配置值，但运行时按文本 + 标题 / 描述 / 正文 / 标签 / 互动指标评估。
>
> **环境变量覆盖**：本段字段名都是多词键（如 `kw_cache_high`），与 `[scheduler]` 多词字段一样，**不被** 通用 `OPENBILICLAW_SECTION_KEY` 覆盖机制支持——`OPENBILICLAW_DISCOVERY_GEN_BATCH` 会被按 `_` 拆成 `discovery.gen.batch` 而落不到字段上（静默保持默认，不报错）。需要覆盖请直接改 `config.toml`。
>
> 非法 / 缺失 / 超范围的数值字段都会回退到上表默认值（与 `[scheduler]` 数值字段同一套 `_normalize_scheduler_int` 规范化）；`discovery` 写成非表（标量）时整段回退默认。

#### `inspiration_breadth` 档位派生表（Phase 2）

`inspiration_breadth` 一个键派生出下列 9 个内部常量（Task 4 删掉的 10 个旧旋钮里，`inspiration_max_expansions_per_seed` 因 Phase-1 死代码清扫后已无消费者，直接删除、不入派生表）。**发布默认档位为 `high`**；`medium` 列逐项等于旧 `_DEFAULT_INSPIRATION_*` 默认值（表驱动断言强制），需与收敛前行为逐项对齐时显式设 `medium`。注意 planner 内部另有 `selected_interests ≤ 4` 的调用预算 cap，`interest_sample_size` 派生 6 / 8 后有效值仍是 4。

| 内部常量（原 key） | low | medium（=旧默认） | high |
|---|---|---|---|
| `aspect_window_size` | 16 | **32** | 48 |
| `interest_sample_size` | 3 | **6** | 8 |
| `max_probe_searches_per_stage` | 6 | **12** | 20 |
| `platforms_per_probe` | 1 | **2** | 3 |
| `riskcontrolled_probe_budget` | 2 | **4** | 8 |
| `search_pages_per_probe` | 1 | **1** | 2 |
| `search_results_per_query` | 3 | **5** | 8 |
| `max_seeds_per_aspect` | 2 | **3** | 5 |
| `max_keywords_per_platform` | 8 | **12** | 16 |

> **已移除的 10 个键（无兼容 shim，写了也会被忽略）**：`inspiration_aspect_window_size`、`inspiration_interest_sample_size`、`inspiration_max_probe_searches_per_stage`、`inspiration_platforms_per_probe`、`inspiration_riskcontrolled_probe_budget`、`inspiration_search_pages_per_probe`、`inspiration_search_results_per_query`、`inspiration_max_seeds_per_aspect`、`inspiration_max_expansions_per_seed`、`inspiration_max_keywords_per_platform`。`load_config_with_diagnostics()` 会在构建 discovery 段之前扫描 raw `[discovery]`，命中任一移除键就往 `diagnostics.issues` 追加一条"`inspiration_xxx` 已移除，值被忽略，请改用 `inspiration_breadth`"提示（CLI 的"配置提示"面板自然渲染），**不 fail-fast**——值被忽略、其余配置照常加载。

#### `keyword_generation_mode`（搜索词生成模式，UI/API 派生便利层）

配置页（**桌面 Web `/web` 与插件 popup 设置区**）把 `inspiration_search_enabled` / `inspiration_replace_merged_keywords` 两个布尔收成**单一「搜索词生成模式」下拉**（经典 / 混合 / 灵感）。这**不是** `DiscoveryConfig` 新字段——`config.toml` 仍只存这两个布尔（单一真相源）；`keyword_generation_mode` 只是 API 层的派生便利：`DiscoveryConfigOut` 读出它、`PUT /api/config` 把它翻译回两布尔，两端 UI 只见一个下拉。

三档 ↔ 两布尔映射：

| 模式（下拉标签 / option value） | `inspiration_search_enabled` | `inspiration_replace_merged_keywords` | 语义 |
|---|---|---|---|
| 经典 / `legacy` | `false` | `false` | 只用合并关键词生成器 |
| 混合 / `hybrid` | `true` | `false` | 经典 + 叠加 search-backed 灵感轴链路（同时跑两套，**混合最贵**） |
| 灵感 / `inspiration` | `true` | `true` | 完全用灵感轴链路替代经典 |

- **读容忍**：`_derive_keyword_generation_mode(enabled, replace)` 在 `enabled=false` 时一律返回 `legacy`（无论 `replace` 取何值），避免过时的 `replace` 残留干扰显示。
- **写规范化（canonical）**：`PUT /api/config` 收到 `discovery.keyword_generation_mode` 时，每档都**显式写两个布尔**（legacy→`{false,false}`、hybrid→`{true,false}`、inspiration→`{true,true}`），不留 `replace` 残留旧值；`keyword_generation_mode` 本身**从不写入 `config.toml`**（config load 忽略未知 discovery 键，handler 也从不 setattr 它）。
- **非法值 → 422**：`ConfigUpdateIn.discovery` 是裸 dict，Pydantic 不校验嵌套 Literal，故 handler 手动校验，非 `legacy`/`hybrid`/`inspiration` 抛 `HTTPException(422)`。
- **mode 赢冲突**：同一 discovery 更新里若 mode 与显式 `inspiration_*` 布尔同时出现，**mode 赢**——mode 应用块在 discovery 段最后执行，且两个原始布尔本就不在该 handler 的显式白名单里。

### `[saved_sync]`

跨平台原生保存的同步配置契约（`SavedSyncConfig`）。默认值、TOML、配置 API、`config-show` 及桌面 / 移动 Web / 插件设置控件均已接入；平台中立保存 API 会在每次本地保存请求中读取当前热重载值。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auto_sync_enabled` | bool | `false` | 是否允许把本地收藏自动同步到外部平台。默认关闭，只有用户明确开启后才为后续同步服务提供启用信号；本字段本身不执行同步 |

`GET /api/config` 返回 `saved_sync.auto_sync_enabled`；`PUT /api/config` 接受同形状的部分更新并保存到 `[saved_sync]`。输入采用 presence-aware 严格布尔校验：省略 `saved_sync` 仍是合法的部分更新；显式传 `saved_sync: null`、`auto_sync_enabled: null`、字符串 `"true"` 或数字 `1` 都返回 422。CLI `openbiliclaw config-show` 会显示解析后的「收藏自动同步」状态。

`false` 时 `POST /api/saved/{list_kind}` 只完成本地保存并返回 `pending`；`true` 时同一路径创建由 runtime registry 跟踪的后台平台任务，但仍立即返回本地成功，不等待 B 站网络。显式 `POST /api/saved/{list_kind}/sync` 是本批账号写入授权，始终无视该开关。旧 `/api/watch-later`、`/api/favorites` 不消费该开关。

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
- LLM：展示实例、全局调用链与四个模块链摘要，允许调整全局并发 / 超时、测试默认链，并跳转桌面 Web 完整编辑；插件保存其他字段时不会回写或压扁实例路由
- B 站与多源：`bilibili.browser.*`、`sources.bilibili.enabled`、`sources.browser.*`、`sources.xiaohongshu.*`、`sources.douyin.*`、`sources.youtube.*`、`sources.twitter.*`、`sources.zhihu.*`、`sources.reddit.*`
- 调度：`scheduler.enabled`、`pause_on_extension_disconnect`、`extension_disconnect_grace_seconds`、`pool_target_count`、`account_sync_interval_hours`、refresh / signal / trending / explore / discovery limit / proactive push / speculator idle 等 runtime 频率参数、八个平台 `pool_source_shares`、猜测兴趣参数、不喜欢领域探针参数、自动更新参数；设置页可调用 `/api/config/source-share-suggestion` 按已有事件和当前表单开关填入建议比例
- 日志：控制台 / 文件级别、完整日志路径（保存时拆回 `directory` / `filename`）、轮转与非托管日志清理参数

`[saved_sync].auto_sync_enabled` 已在桌面 / 移动 Web 和插件设置控件中暴露，也可通过 `config.toml` 或严格校验的 `/api/config` 管理。保留但不单独暴露的字段还包括目前只有一个有效值的内部兼容项，例如 `[sources.douyin].mode = "direct"`；保存时插件会继续按当前支持值写回，不会删除其他高级字段。

## `/api/config` 保存与恢复语义

设置页和外部调用方都走同一条配置 API。`GET /api/config` 默认会 mask API Key；`PUT /api/config` 只更新请求体里出现的字段，并遵循以下安全规则：

- masked key（例如 `sk-****abcd`）不会写回 `config.toml`，避免把真实密钥覆盖成星号。
- 已有非空的 `model`、`base_url`、OpenRouter headers 和 embedding `model/base_url/api_key` 不会被空字符串覆盖；空值只在旧值本来为空时写入。
- DeepSeek `reasoning_effort` 是例外：空字符串是有效配置值，表示关闭 thinking，会被 `/api/config` 保存并热重载。
- 旧 schema 的 `openai_compatible.reasoning_effort` 即使被旧保存器物化为默认 `"medium"`，读取为 v2 投影时仍归一为空字符串，因为旧 adapter 实际从未发送它；原生 v2 实例中用户明确填写的值则完整保留。
- `saved_sync.auto_sync_enabled` 只接受 JSON 布尔值；省略整个段表示“不更新”，但段或字段显式传 `null`、字符串或数字等非布尔输入都由 Pydantic 返回 422，不做 truthy / null 转换。
- v2 实例编辑器只有在用户显式勾选“清除已保存密钥”时才会给该实例发送空 `api_key`；留空会保留现有密钥，masked echo 也不会被写盘。`reset_fields` 继续服务旧 Provider 分段和独立 embedding 配置；未知字段返回 400。
- 安装包 `/setup/` 第一页保存 LLM 配置时会传请求级字段 `suppress_background_llm_work=true`。该字段不写入 `config.toml`，只表示本次保存后热重载组件但暂停 refresh / account-sync 等 LLM 后台循环与 post-reload 探针 / 预热；用户在第二页点击「开始初始化」后，guided init 先严格生成完整画像和首轮可用推荐，init 终态后恢复后台循环并调度兴趣 / 避雷探针。普通设置页保存不传该字段，仍保持原有热重载和后台续跑行为。
- 写盘前会先用新配置构建 LLM registry；blocking issue 会返回 400 且不写入 `config.toml`。
- 写盘前会生成 `config.toml.bak`。正常模式下热重载失败会尝试恢复备份，并在响应里设置 `rollback_applied=true`；如果备份恢复也失败，接口返回 500 和人工恢复提示。

## 模型列表发现（不写配置）

`POST /api/config/discover-models` 接收 `instance_id` 和当前页面的 `config.llm` 草稿，在内存副本中精确构建该实例，并调用其 OpenAI-compatible `GET /models`。它不会调用 `save_config()`、不会创建迁移备份，也不会改变默认链；masked API Key 继续复用已保存密钥。PC Web、插件和 `/setup/` 都把结果填入可编辑的模型下拉框，失败时保留用户手填值。

OpenAI-compatible 协议没有列举 reasoning effort 能力的标准接口；接口返回的 Effort 候选是按 Provider / 模型生成的 `local_advisory`。用户可以继续手填，最终是否接受由具体服务在真实请求时决定。

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
| `GEMINI_API_KEY` | Gemini 官方兼容环境变量；启用的 Gemini 实例未显式配置密钥时可作为凭据来源 |
| `OPENBILICLAW_PROXY_HOST` | Docker 运行时可选宿主机代理地址，默认 `host.docker.internal` |
| `OPENBILICLAW_PROXY_PORT` | Docker 运行时可选宿主机代理端口，默认 `7897` |
| `OPENBILICLAW_PROXY_TIMEOUT` | Docker 运行时代理探测超时（秒），默认 `1.0` |
| `OPENBILICLAW_DOUYIN_COOKIE` | 抖音 direct-cookie discovery 的显式 Cookie 覆盖；未设置时读取扩展同步的 `data/douyin_cookie.json` |
| `OPENBILICLAW_API_AUTH_ENABLED` | 覆盖 `[api.auth].enabled`（局域网密码门禁总开关） |
| `OPENBILICLAW_API_AUTH_PASSWORD` | 明文访问密码；启动时即 scrypt hash，优先于 `_PASSWORD_HASH`（适合 Docker / 多 worker 注入同一密码） |
| `OPENBILICLAW_API_AUTH_PASSWORD_HASH` | 预生成的 scrypt 密码哈希；覆盖 `[api.auth].password_hash` |
| `OPENBILICLAW_API_AUTH_SESSION_SECRET` | 登录态 HMAC 签名密钥；覆盖 `[api.auth].session_secret`（多进程共用同一密钥） |
| `OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS` | 覆盖 `[api.auth].session_ttl_hours`（0=永不过期） |
| `OPENBILICLAW_API_AUTH_TRUST_LOOPBACK` | 覆盖 `[api.auth].trust_loopback`（本机是否免登录） |
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
routing_version = 2
default_chain = ["deepseek"]

[llm.instances.deepseek]
name = "DeepSeek"
provider_type = "deepseek"
enabled = true
api_key = "sk-..."
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/v1"

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
