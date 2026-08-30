# 配置参考

> `[llm].concurrency` 缺省/非法值为 3；显式正数原样保留。后台容量为 `max(1, total-1)`（默认 2）；`candidate_eval_concurrency` 仍默认 3。

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

## 配置页跨机器迁移

桌面 Web 的「设置 → 通用 → 数据迁移」可以在旧机器导出 `.obcbackup`，再到新机器选择该文件导入。这里的“全部信息”指**全部可移植用户状态**：磁盘上的 `config.toml` 与 `config.local.toml` 会先合并（不读取环境变量覆盖），移除 `[api.auth]` 后扁平化为包内单份 `config/config.toml`，其中仍包括文件中保存的模型 Key 与来源凭据；其余还包括当前进程已锁定的 active data dir 中的主 SQLite 和其它数据库、画像 / 记忆、平台 Cookie 文件、图片缓存，以及白名单内的桌面主题与滚动偏好。若刚在线保存了尚待重启的新 `data_dir`，配置快照仍来自磁盘两层，但数据成员不会提前改从新目录读取。它不是系统镜像，也不会复制日志、已有备份、embedding 派生缓存、评测 / 临时缓存、证书、自启动文件、OpenBiliClaw Web / 扩展访问会话、外部 CLI 凭据或环境变量值；平台 Cookie 则是明确包含的敏感登录态。源机器的 API 登录开关、密码 / password hash、session secret、受信代理、Bearer Origin 和扩展设备 key 都属于整段 `[api.auth]`，不会写入包。

`.obcbackup` 是**未加密的敏感 ZIP**。只有在可信设备之间传递，并像保护 API Key / Cookie 一样保护和及时删除它。manifest 的 `source_omitted_environment_variables` 会列出源机器导出时有值、会影响运行结果的环境变量名称（`OPENBILICLAW_*`、Gemini 标准 Key、系统代理 / CA），但不会写入这些变量的值；如果源机器的有效配置依赖它们，目标机需自行重新提供。导入响应 / staged 状态中的 `target_active_environment_variables` 则是导入当时目标进程有值、重启后仍可能覆盖文件配置的环境变量快照；重启前如果环境改变，应以实际启动环境为准。两者都不是“已迁移的值”。

导入不会在当前 API 进程里热替换配置或前端偏好。后端会先完整校验并暂存，设置页显示“需要重启”；下一次受支持的后端启动取得独占迁移锁并成功应用后，才写入规范化后的新 `config.toml`、替换后端数据并让设置页应用白名单桌面偏好。每个浏览器按 applied status 的 `migration_id` 在本地记录一次性交接回执，同一迁移的偏好只应用一次，所以用户之后修改主题、色相或滚动设置不会被持久存在的旧 status 再覆盖；每次打开「通用」仍会强制向后端对账，而不是只依赖页面启动时的缓存。来源机的 local overlay 已扁平化，不会以 `config.local.toml` 身份恢复；目标机原 `config.local.toml` 会按存在情况改名为 `config.local.toml.pre-import-<id>.bak`，其机器专属 / auth 取值已先合并进新 `config.toml` 的保留基线。

以下机器专属字段始终采用**目标机器**的当前值，不从迁移包覆盖：

- `general.data_dir` 与 `[storage]`（包括 `db_path`）；
- `[api].host` / `[api].port`；
- `[logging].directory` / `[logging].filename`；
- `[network]`、`[tls_proxy]`、`[autostart]`；
- `sources.browser_cdp_url`；
- `bilibili.proxy` 与 `bilibili.browser_executable`（目标机网络策略和本机浏览器路径）。

`[api.auth]` 也整段以**目标机器应用时的最新磁盘值**为基线：迁移包不提供或覆盖其中任何来源字段，重启应用会重新读取目标机两层配置，不使用暂存时的陈旧 auth 快照。应用时只执行安全收口——生成新的文件 `api.auth.session_secret`，把 `api.auth.extension_access_enabled` 设为 `false` 并清空 `extension_access_keys`；prepared DB 还把 `auth_epoch` 严格提升为 `max(来源 prepared DB epoch, 目标 active DB epoch) + 1`、删除来源 password fingerprint，启动后再按目标凭据 reconcile。因此目标机既有的门禁开关、密码凭据、会话 TTL、loopback / proxy / Origin 策略继续保留，但来源 / 目标旧 Web 会话都会失效（即使 session secret 由目标环境固定），扩展远程设备也需重新生成 key 并配对。目标数据目录里的 `certs/` 与 `autostart/` 文件会保留。详细包格式、校验和回滚流程见[存储层](storage.md#可移植数据迁移)，HTTP 契约见[后端 API](api.md#本机数据迁移)。

## 配置段落

插件、桌面 Web 和移动 Web 的「保存时自动同步到对应平台」都从 API 读取，默认关闭。插件与移动 Web 的配置 GET/PUT 使用 AbortController 有界 timeout；插件的同一 deadline 从后端地址解析开始，覆盖初次设备会话交换、401 强制换票、受保护请求与响应解析，认证 fetch 接收同一 AbortSignal。移动 Web 使用模态设置对话框：Escape 可关闭、Tab 焦点留在对话框内，关闭后回到原设置按钮；配置 GET 超时或失败时保存与开关保持禁用，用户必须通过「重试加载」成功取得当前值后才能写回，避免用默认 false 覆盖未知远端状态。

### `[general]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `language` | string | `"zh"` | Agent 输出语言（`zh` / `en`） |
| `data_dir` | string | `"data"` | 数据目录（记忆、Cookie、数据库）；通过配置 API 改动时只持久化，完整重启后才切换 |

### `[api]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `host` | string | `"0.0.0.0"` | 后端 API 监听地址。默认绑定所有网卡，方便同局域网手机访问 `/m/`；如只允许本机访问可改为 `"127.0.0.1"` |
| `port` | int | `8420` | 后端 API 监听端口 |

`openbiliclaw start` 和桌面安装包入口默认读取这里的 host / port；显式设置 `OPENBILICLAW_HOST` / `OPENBILICLAW_PORT` 时环境变量优先。默认 `host = "0.0.0.0"` 会创建独立的 IPv4 `0.0.0.0` 与 IPv6 `[::]` listener（系统无 IPv6 时保留 IPv4），避免不同操作系统对 IPv4-mapped IPv6 的行为差异。浏览器插件的手机二维码入口会在后端地址仍是 loopback 时调用轻量端点 `GET /api/qr-info`（不触发 embedding readiness probe）并读取响应中的 `lan_ip` 字段，用局域网 IP 生成 `/m/` 二维码；IPv4 优先，没有可用 IPv4 时回退 ULA / global IPv6，并用方括号生成合法 URL。桌面 Web 的手机二维码会优先使用设置中手动填写的后端地址/端口（若已填写），未填写时才按页面来源或自动探测的局域网 IP 生成。

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

### `[tls_proxy]`

默认关闭的局域网 / 自管 HTTPS 入口（`TlsProxyConfig`）。只由
`openbiliclaw serve-api` 消费；普通 `start` 与未启用 TLS 的行为不变。完整安全与证书流程见
[`TLS Proxy 模块`](tls-proxy.md) 和 [`HTTPS 部署`](../https-deployment.md)。
公网域名的 Docker Caddy overlay 不读取本表；它只消费部署变量 `OPENBILICLAW_DOMAIN`。

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `false` | 是否在 `serve-api` 启动可选 TLS listener |
| `port` | int | `8443` | TLS 监听端口；严格限制 `1..65535`，不能与 API 端口相同 |
| `cert_dir` | string | `""` | 空值解析为 `{data_dir}/certs`；相对路径按 runtime project root 解析；控制字符非法 |
| `san_names` | list[string] | `[]` | 客户端实际使用的 DNS/IP；规范化大小写、IDNA/IP 并去重；非法 hostname、URL、带端口值会拒绝加载/保存 |

`save_config()` 会完整渲染 `[tls_proxy]`，因此 enable/disable、端口、目录与 SAN 会经过
load → save → load round-trip；保存其他已知配置表时也不会再丢掉 TLS 表。环境变量覆盖的字段
始终从基础 `config.toml` 的磁盘值回写（基础文件没有该字段则省略），不会把临时有效值烘焙
进去；默认路径加载时，`config.local.toml` 遮蔽的字段采用同一 provenance 规则。显式
`load_config(path)` / `save_config(config, path)` 不合并也不咨询 project-root local 文件。

显式支持的环境变量只有：

| 环境变量 | 字段 |
|---|---|
| `OPENBILICLAW_TLS_PROXY_ENABLED` | `enabled` |
| `OPENBILICLAW_TLS_PROXY_PORT` | `port` |
| `OPENBILICLAW_TLS_PROXY_CERT_DIR` | `cert_dir` |
| `OPENBILICLAW_TLS_SAN_NAMES` | `san_names`（逗号分隔） |

这些多词变量绕过通用下划线拆分器，由 `_build_tls_proxy()` 显式读取。不要推断其它
`OPENBILICLAW_TLS_*` 名称可用。若 `enabled` 被环境变量或 `config.local.toml` 覆盖，
`tls-proxy enable/disable` 会拒绝报告虚假的持久化成功，并指出应修改的上层来源。

#### Config 模块公开 TLS API

| API | 说明 |
|---|---|
| `TlsProxyConfig` | 根 `Config.tls_proxy` 的 typed 配置对象 |
| `normalize_tls_proxy_port()` | 严格验证 TLS TCP port |
| `normalize_tls_cert_dir()` | 规范化目录并拒绝控制字符 |
| `normalize_tls_san_names()` | 验证、规范化并去重 DNS/IP SAN |
| `resolve_tls_cert_dir(config)` | 按 project root / data_dir 解析运行时证书目录 |

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
| `concurrency` | int | `3` | 单 runtime 的 LLM 总并发上限；后台容量派生为 `max(1, total-1)`（默认 2）。合法范围 `1..16` |
| `timeout` | int | `1200` | 每个实例请求的超时秒数，默认 20 分钟，合法范围 `10..1200` |

`default_chain` 里的元素是**实例 ID**，不是 Provider 类型。一个实例是一套完整、可独立调用的端点配置，因此可以同时存在两个 `provider_type = "openai_compatible"` 的中转渠道、两个 OpenAI 账号，或同一网关上的不同模型：

```toml
[llm]
routing_version = 2
default_chain = ["relay-primary", "relay-backup", "deepseek"]
concurrency = 3
timeout = 1200

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

> DeepSeek 官方 API 对内容安全审核较严，`rebuild-profile` / `init` 偏好分析遇到
> HTTP 400 `"Content Exists Risk"` 时，系统会自动拆分并跳过命中事件；更稳的做法是给
> `default_chain` 加一个第三方中转（`openai_compatible`）作为 fallback。

### `[llm.instances.<instance_id>]`

实例 ID 必须以小写字母或数字开头，后续只允许小写字母、数字、`_`、`-`，最长 64 个字符；它必须唯一且保存后应保持稳定，调用统计、失败日志、路由和冷却都用它区分具体渠道。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `name` | string | 实例 ID | 设置页显示名称，可重复 |
| `provider_type` | string | `""` | 适配器类型：`openai` / `claude` / `gemini` / `deepseek` / `ollama` / `openrouter` / `orcarouter` / `openai_compatible` |
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

> `auth_mode = "codex_oauth"` 是实验性 / 非官方路径：OpenAI 官方 API 认证仍以 Platform API key 为稳定入口。启用前先运行 `openbiliclaw login codex`，OpenBiliClaw 会从官方 Codex CLI 登录态导入 token 到 `~/.openbiliclaw/codex_auth.json`，并立即做一次真实 LLM 能力探测。该模式下 `api_key` 会被忽略，`api_flavor` 不再需要设置（传输层固定为官方 Codex 通道）；请求发往 `https://chatgpt.com/backend-api/codex/responses`——官方 Codex CLI 同款通道，而不是 `api.openai.com/v1`。`base_url` 只能留空或指向官方 Codex 域名，避免把 ChatGPT OAuth token 发给第三方代理。`model` 必须是 Codex 后端模型（如 `gpt-5.4` / `gpt-5.5` / `gpt-5.6-*` / `gpt-5.3-codex-spark`）；Platform API 模型（如 `gpt-5-nano`）会被该通道以 HTTP 400 拒绝。

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

#### OrcaRouter（`provider_type = "orcarouter"`）

[OrcaRouter](https://www.orcarouter.ai) 是 OpenAI 协议兼容的模型路由网关，一个 Key 即可按需路由 150+ 模型，并在网关层提供默认拒绝的零信任安全。它继承 OpenAI 系 adapter 的超时 / 重试 / 错误归一化 / JSON mode / per-call model 语义。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | OrcaRouter API Key |
| `model` | string | `"openai/gpt-4o"` | OrcaRouter 路由的模型名（`<vendor>/<model>` 或平台别名） |
| `base_url` | string | `"https://api.orcarouter.ai/v1"` | OrcaRouter API 地址 |
| `reasoning_effort` | string | `"medium"` | 保留以对齐统一配置面；网关把推理参数原样转发给上游路由，非推理模型会以 HTTP 400 拒绝，因此适配器**不发送** `reasoning_effort` / `reasoning`，推理模型使用自身默认档位 |

> OrcaRouter 没有 embedding 接口；需要向量化时在 `[llm.embedding]` 独立配置 Ollama / Gemini / OpenAI 等。

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
| `cache_max_bytes` | int | `0` | L2 持久化缓存（`data/embedding_cache.db`）磁盘预算，单位字节；`0` = 不设上限（默认）。向量本身已按紧凑 float32 二进制存储（4096 维约 16 KiB/行），此上限进一步约束长跑 discovery/warmup 的磁盘增长：占用超过 `cache_max_bytes × cache_high_watermark` 时开始淘汰（先删失效 namespace / 旧 legacy 行，再按最近访问时间淘汰 active namespace 最旧行），直到降到 `cache_max_bytes × cache_low_watermark`。缓存可重建，淘汰只影响冷数据。建议值 `536870912`（512 MiB） |
| `cache_high_watermark` | float | `0.9` | 容量淘汰触发水位（占用 / 预算 的比例，0..1），需 `>= cache_low_watermark` |
| `cache_low_watermark` | float | `0.7` | 容量淘汰停止水位（占用 / 预算 的比例，0..1），需 `<= cache_high_watermark` |

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

桌面 Web `/web` 与插件 side panel 都可测试单个聊天实例、整条默认链和 embedding。插件可直接新建、编辑、删除实例并调整全局 `default_chain`；模块自定义链在插件中只读展示，需进入 PC Web 编辑。探测走一个**无写入**接口，不会保存 `config.toml`，也不会触发运行时热重载；guided init 运行期间仍可调用，不受 `409 init_running` 写端门控影响。真正保存草稿的 `PUT /api/config` 在 init 期间仍被禁止。

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

### `[sources.<name>]` 发布日期偏好

所有来源（`bilibili` / `xiaohongshu` / `douyin` / `youtube` / `twitter` / `zhihu` /
`reddit` / `bangumi` / `linuxdo` / `v2ex` / `weibo`）都支持以下四个字段，默认
`"all"` = 不按发布日期过滤：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `recommendation_date_preset` | string | `"all"` | 发布日期范围：`all`、`last_7_days`、`last_30_days`、`last_6_months`、`last_1_year` 或 `custom` |
| `recommendation_date_start` | string | `""` | `custom` 的包含式起始自然日，格式为 `YYYY-MM-DD`；留空表示无下界 |
| `recommendation_date_end` | string | `""` | `custom` 的包含式结束自然日，格式为 `YYYY-MM-DD`；留空表示无上界 |
| `recommendation_date_weight` | float | `0.5` | 范围外候选的分数乘数为 `1 - weight`；`1` = 严格排除 |

发布日期范围按用户本地自然日换算为包含式 UTC 边界。发现阶段（LLM 评估之前）会直接过滤掉
范围外候选，不消耗评估预算；候选池打分与推荐服务阶段保留 `weight` 语义。缺失或无法解析
发布时间不能用发现时间代替；范围启用时按范围外处理。

配置文件、`GET /api/config` 和 `PUT /api/config` 使用同一组字段。保存阶段会拒绝非法 preset、日期
或权重，不会先写入再在运行时悄悄修正；合法保存沿用现有备份、原子写入和 RuntimeContext 热更新事务。
桌面 Web「设置 → 平台源 → Bilibili」提供这些字段的编辑控件；本次切片不在插件 popup 或移动 Web
重复实现编辑表单，它们继续读取后端生效配置并使用共享推荐行为。

### `[bilibili.browser]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `executable` | string | `""` | agent-browser 路径（留空使用全局安装） |
| `headed` | bool | `false` | 是否显示浏览器窗口（调试用） |

> 运行时行为：
> 如果 `bilibili.cookie` 留空，CLI 命令和本地 API 服务会自动回退到 `auth login` 保存的 `data/bilibili_cookie.json`。
> 只有在你想显式覆盖本地登录态时，才需要把 cookie 直接写进 `config.toml`。

### `[network]` (v0.3.164+，v0.3.165 路由模式补强，v0.3.166 国内网关豁免)

海外网络路由。仅作用于**海外客户端**：OpenAI / Claude / Gemini / OpenRouter / openai_compatible 的 chat + embedding SDK、YouTube（yt-dlp、scrapetube、InnerTube / 页面 fallback）、X 的服务端 `twitter-cli`、Reddit 的 `rdt-cli` / OpenCLI 命令后端、Bangumi（`api.bgm.tv` 与封面 CDN `lain.bgm.tv` 均为海外 Cloudflare，实测 2026-07-18 国内网络直连超时、走代理正常）、封面图片代理的境外 CDN（`i.ytimg.com` / `ggpht.com`；v0.3.209 起跟随本节策略，此前硬编码 `trust_env=true` 在 `custom` 模式下拿不到代理、国内直连超时致 YouTube 封面全裂）、GitHub 自动更新、Codex OAuth 令牌刷新、discovery 灵感搜索的海外后端（Exa `api.exa.ai` / You.com `api.ydc-index.io` / Serply `api.serply.io` 的直连 HTTP 客户端与 mcporter 子进程；v0.3.209 起跟随本节策略，此前硬编码 `trust_env=false` 在 custom 模式下拿不到代理，实测 api.exa.ai 国内直连超时；Bing RSS 属国内可达服务保持恒直连）。X / Reddit 回落到浏览器扩展任务时，请求由浏览器发出并沿用浏览器自己的网络设置；微博的项目自有 `httpx` client 固定 `trust_env=false` 国内直连，也不读取本段。**注意**：`openai_compatible` / `openai` 若指向的是国内网关或本机地址，则按下方「国内网关豁免」强制直连，不受本节代理影响。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `mode` | string | `"system"`（v0.3.175 起；此前为 `"direct"`） | `system` 继承 `HTTP(S)_PROXY` / OS 代理（macOS 还含系统偏好设置里的代理）；`direct` 显式忽略环境 / 系统代理；`custom` 只使用下方 `proxy` |
| `proxy` | string | `""` | `custom` 模式的代理 URL。支持 `http://` / `https://` / `socks5://` / `socks5h://`，如 `"socks5://127.0.0.1:1080"` |

> 与 `[bilibili].proxy` 的区别：`[network].proxy` 是「海外出口」，`[bilibili].proxy` 是「B站专用」，两者语义相反、互不影响。
>
> **国内直连隔离**：B站 / 抖音 / 微博 / Ollama / 国内 CDN 图片缓存等所有 `trust_env=False` 客户端**永远不使用**此代理（继承代理曾触发 B站 风控，`df626f3f`）。该隔离由 `tests/test_network_proxy_isolation.py` 与微博 client 测试守卫。
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

> **关于 `daily_*_budget`：** 多数来源的这些字段是**每 UTC 日、按任务类型的入队次数上限**；微博例外，三个 budget 只计最终经全局去重和 candidate pipeline 实际保留的候选条数。它们都不是启用 / 关闭来源的开关（来源开关是各段的 `enabled`）。显式填 `0` 表示不设每日上限，补池只受平台缺口 / `discovery_limit` / producer 节流控制；字段缺省时使用各来源表格所列默认值，其中小红书搜索为 `20`。对按任务计数的来源，填 `1` 只会把该任务类型限制到每天 1 次——配置加载时对落在 1–4 的可疑值会打印一次 WARN 提示。

### `[sources.bilibili]`

Bilibili discovery 的平台级开关。B 站账号登录 / Cookie 获取仍由 `[bilibili.auth]` 和 `[bilibili.browser]` 控制；本段只决定后台候选池是否继续调度 B 站 `search` / `related_chain` / `trending` / `explore` 策略。

> **`min_interval_minutes` 的作用范围（2026-07-26 实测澄清）**：这道闸只拦 **producer loop** 这一条路径——
> `ContinuousRefreshController` 每 `[scheduler].refresh_check_interval_seconds`（默认 60 秒）唤醒一次
> `_loop_<source>_producer`，`_tick_<source>_producer` 先算该来源缺口，再由 producer 的 `_is_due()` 判定是否到点。
> **非 B 站的 8 个来源，这是稳态补货的唯一路径**，所以配置真实生效。
>
> **节流地板的判定口径（v0.3.186 起统一）**：抖音 / YouTube / X / 知乎 / Reddit 原先把「上次何时跑过」记在进程内属性里，后端一重启就清零、地板当轮失效——在真实数据上量到过：Reddit 25 天 55 轮里有 5 轮间隔是 8 / 10 / 11 / 35 / 40 分钟，而当时配置的是 60 分钟。同一处还有个反向毛病：跑完但零产出的轮次也会写时间戳，把本该立刻重试的情况锁死一个完整周期。现在八个非 B 站 producer 都使用持久 cadence：抖音 / YouTube / X / 知乎 / Reddit / Linux.do 共用 `source_producer_runs`，**只记录真正产出候选的轮次**；XHS 与 Bangumi 继续使用各自的持久 runtime state / run ledger。这样重启不失效，空跑不烧周期。未接数据库构造的共享-cadence producer（单测 / CLI 一次性调用）自动回落到原来的进程内时间戳。
> **节流地板的判定口径（v0.3.186 起统一）**：抖音 / YouTube / X / 知乎 / Reddit 原先把「上次何时跑过」记在进程内属性里，后端一重启就清零、地板当轮失效——在真实数据上量到过：Reddit 25 天 55 轮里有 5 轮间隔是 8 / 10 / 11 / 35 / 40 分钟，而当时配置的是 60 分钟。同一处还有个反向毛病：跑完但零产出的轮次也会写时间戳，把本该立刻重试的情况锁死一个完整周期。现在九个来源统一以共享账本 `source_producer_runs` 为准，**只记录真正产出候选的轮次**——重启不失效，空跑不烧周期。未接数据库构造的 producer（单测 / CLI 一次性调用）自动回落到原来的进程内时间戳。
>
> B 站不同：它有两条路径，而闸门只管其中较少走的那条。
>
> | B 站的触发路径 | 走哪套门控 | 过 `min_interval_minutes` 吗 |
> |---|---|---|
> | 主发现（`search` / `related_chain` / `trending` / `explore`） | `_build_refresh_plan` → `_run_refresh_plan`，由 `[scheduler]` 的 `signal_event_threshold` / `trending_refresh_minutes` / `explore_refresh_minutes` / `discovery_limit` 决定 | **否** |
> | 手动「立即补货」 | `force_refresh` → `_build_source_replenishment_plan` → 同上 | **否** |
> | 初始化回填 `run_init_backfill` | 直接调 `discovery_engine.discover()` | **否** |
> | API 搜索被风控冷却时接管的扩展搜索兜底 | `BilibiliExtensionSearchProducer.produce_if_due()` | **是** |
>
> 换句话说，日常看到的 B 站补货绝大多数不受本字段影响；要调 B 站主发现的节奏请改 `[scheduler]`。
>
> **`trending_refresh_minutes` / `explore_refresh_minutes` 通常只在池子不缺货时才生效（2026-07-27 实测，2026-08-15 补充）**：`_build_refresh_plan` 先看池子是否低于目标——低于时优先返回 `_build_source_replenishment_plan()` 的结果，而那条路径把 B 站四个策略 `search / related_chain / trending / explore` **整组下发、完全不查这两个间隔**。只有池子**不低于**目标时才走到按间隔挑选策略的巡航分支。**例外**：当 B 站已达自身份额、`_build_source_replenishment_plan()` 为空，且 discovery candidate 管线没有 `pending_eval/evaluating` 在途工作时，`_build_refresh_plan` 会回落到按 `trending_refresh_minutes` / `explore_refresh_minutes` 的 B 站周期计划，让健康超份额来源在其它来源不可用或节流时继续补全局库存。真机采样：B 站有缺口时 `last_trending_refresh_at` / `last_explore_refresh_at` 每 ~1.1 分钟（即每个 refresh tick）同步推进一次，而不是配置的 3 分钟。也就是说这两个字段管的是「池子够用时的巡航节奏」，不是「缺货时的补货节奏」——后者由缺口大小、`discovery_limit` 和 B 站客户端自身的风控冷却决定。
> 另有显式绕过：`openbiliclaw discover-xhs --force` 把间隔置 0，Bangumi / 微博的统一
> `openbiliclaw discover --source <source> --force` 会把 `force=True` 交给 producer；它们都只跳过
> cadence，不会绕过平台 cooldown、日预算或 pool gate，常驻流程不会强制执行。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用 Bilibili discovery。设为 `false` 后，B 站候选池占比会从运行时有效配比中剔除，已保存的 `scheduler.pool_source_shares.bilibili` 数值仍保留，重新开启后继续使用 |
| `min_interval_minutes` | int | `3` | `BilibiliExtensionSearchProducer` 两次入队之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行。**只作用于「B 站 API 搜索被风控冷却」时接管的扩展搜索兜底路径**，主发现路径（`search` / `related_chain` / `trending` / `explore`）的节奏由 `[scheduler]` 的 `signal_event_threshold` / `trending_refresh_minutes` / `explore_refresh_minutes` / `discovery_limit` 决定 |

### `[sources.xiaohongshu]`

小红书专用配置。内容发现和元数据提取都由浏览器扩展在真实登录态下完成：被动收集、短暂前台渲染的搜索任务和创作者订阅都会通过扩展任务桥回写后端。主后端不主动爬取小红书，也不再依赖 `sidecar_url`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否启用小红书 discovery 和 init bootstrap；默认关闭，`init` 选 Yes、`--yes-xhs` 或插件设置页打开后才会写回 `true`。关闭后 producer 停止产词，`/api/sources/xhs/next-task` 也不会领取此前已排队的自动 search / creator / bootstrap 任务，因此扩展不会继续打开自动发现页面；任务保留为 pending，重新开启后恢复 |
| `incremental_enabled` | bool | `false` | 是否允许小红书参与扩展在线周期回拉（`bootstrap_profile`）。默认关闭；需同时开启 `[scheduler].source_incremental_enabled` 才会生效。开启后插件可能短暂打开前台标签页抓取账号数据 |
| `daily_search_budget` | int | `20` | 每天后端允许入队的 Soul 驱动搜索任务数上限；`0` 表示不设每日上限。默认 20 是保守工程起点，不代表小红书官方阈值 |
| `daily_creator_budget` | int | `0` | 每天订阅创作者抓取任务上限；`0` 表示不设每日上限 |
| `task_interval_seconds` | int | `1200` | 后端领取连续 search / creator 任务的**目标间隔**（默认 20 分钟）；每个任务按稳定的 ±25% 抖动得到实际 15–25 分钟窗口，下一次可领取时间持久化在 SQLite，后端重启、MV3 service worker 重启或多个浏览器 profile 都不能绕过。bootstrap 不受普通间隔限制，但仍受来源开关和平台风控冷却约束 |
| `min_interval_minutes` | int | `20` | `XhsTaskProducer` 两次入队之间的最小间隔；producer 还会在 pending + in-progress 搜索任务达到 5 条时停止 claim / 生成关键词，只补到 5 条，不让积压继续增长。`0` 只关闭 producer 时间闸，不关闭这道积压门 |

> **默认值升级边界：** 上述 `20 / 1200 / 20` 只用于新配置或缺少对应键的配置。已有 `config.toml` 中显式写入的值（包括 `0 / 300 / 3`）继续原样读取，不做静默迁移或强制覆盖。
>
> **安全设计要点：** 后端从不直接调用小红书搜索 / Feed API。所有“主动发现”（关键词搜索、创作者主页浏览）都在用户自己的浏览器中由扩展代理完成；搜索页因隐藏标签不渲染笔记虚拟列表，会短暂切到前台，任务结束后自动关闭并恢复原标签。被动发现则利用用户正常浏览时已经加载的卡片 URL，零额外请求。扩展识别到可见的安全验证、操作频繁或 HTTP 429 后会返回结构化 `rate_limited`；后端按连续风控轮次使用 `1h → 2h → 4h → 8h → 16h → 24h` 持久化冷却（24 小时封顶），同一活动冷却内的重复报告不增加轮次，冷却后完成一条正常 search / creator 任务才重置。冷却期间停止全部任务领取和关键词生产；安全窗口不提供可调短配置。

### `[sources.douyin]`

抖音专用 discovery 配置。初始化画像仍由浏览器扩展执行；本段控制 `openbiliclaw discover --source douyin` / `discover-douyin` 的内容发现。Cookie 不写进 `config.toml`：`cookie_env` 指向的环境变量优先；未设置时，后端读取浏览器扩展通过 `/api/sources/dy/cookie` 同步到 `data/douyin_cookie.json` 的值。设置页（插件 / 桌面 Web）可手动粘贴新 Cookie，但 `GET /api/config` 的 API-only `sources.douyin.cookie` 始终只返回脱敏预览（`reveal_keys=true` 也是兼容 no-op）；`PUT /api/config` 把非空、非掩码的新值路由到 `data/douyin_cookie.json`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否启用抖音 discovery。默认关闭，必须显式 opt-in |
| `incremental_enabled` | bool | `false` | 是否允许抖音参与扩展在线周期回拉（`bootstrap_profile`）。默认关闭；需同时开启 `[scheduler].source_incremental_enabled` 才会生效。开启后插件可能短暂打开前台标签页抓取账号数据 |
| `mode` | string | `"direct"` | 当前仅支持 `direct`，保留字段用于后续 extension/direct 切换 |
| `cookie_env` | string | `"OPENBILICLAW_DOUYIN_COOKIE"` | douyin.com Cookie header 的环境变量覆盖名；为空时使用扩展同步文件 |
| `daily_search_budget` | int | `0` | 每日搜索插件任务预算，限制 `dy_tasks(type="search")` 入队次数；`0` 表示不设每日上限 |
| `daily_hot_budget` | int | `0` | 每日热点插件任务预算，限制 `dy_tasks(type="hot")` 入队次数；`0` 表示不设每日上限，正数时 runtime 抖音缺口较大时会把有效预算临时抬高到 `max(配置值, min(缺口, 60))` |
| `daily_feed_budget` | int | `0` | 每日首页推荐流插件任务预算，限制 `dy_tasks(type="feed")` 入队次数；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `2` | direct 诊断请求的建议最小间隔；当前默认 discovery 走插件 DOM-first 链路，主要由任务预算和 runtime producer 节流保护 |
| `min_interval_minutes` | int | `3` | `DouyinDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行。v0.3.186 前写死为 `15` 且不可配置 |

当前 `search` 子来源使用浏览器插件的登录会话，从抖音首页通过 DOM 搜索框输入 / 提交触发页面加载，并以 `dy-plugin-search` 进入 discovery；`hot` 子来源同样从首页点击热榜 / 热点入口和目标热词，并以 `dy-plugin-hot-related` 进入 discovery；`feed` 子来源在首页推荐流滚动触发加载，并以 `dy-plugin-feed` 进入 discovery。插件只被动监听页面自己发出的响应和已渲染 DOM，不主动跳 `/search/...`、`/hot/...` 快捷 URL，也不主动调用 search / related / feed API bridge。插件任务空 / 失败时默认返回 0 条；direct-cookie fallback 仅保留给显式 `allow_direct_fallback=True` 的诊断代码。因 daemon 重启或插件未及时消费而被清理的 `failed/stale_pending` 任务不消耗正数每日预算。runtime 大缺口补池会优先 search / hot，feed 只用于小缺口补零散名额。`msToken` 如果存在会随 Cookie 一起使用，但扩展同步不再硬依赖它。若 Cookie 过期、页面布局变化或插件未在线，命令可能返回 0 条并提示检查登录态。

### `[sources.youtube]`

YouTube discovery 配置。初始化画像由浏览器扩展读取观看历史 / 订阅 / 点赞，也可通过 `import-youtube` 导入 Google Takeout；steady-state discovery 由后端 `YoutubeDiscoveryProducer` 独立调度 `yt_search` / `yt_trending` / `yt_channel` 三个策略。这里的预算是可选每日执行上限；默认 `0` 表示不设每日上限，每轮执行规模由平台缺口和 `scheduler.discovery_limit` 决定，行为与 B 站补池保持一致。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 YouTube 参与候选池配比和后台 discovery；`init --yes-youtube` 会写回 `true`，`--no-youtube` 或 `OPENBILICLAW_NO_YOUTUBE=1` 会写回 `false` |
| `incremental_enabled` | bool | `false` | 是否允许 YouTube 参与扩展在线周期回拉（`bootstrap_profile`）。默认关闭；需同时开启 `[scheduler].source_incremental_enabled` 才会生效。开启后插件可能短暂打开前台标签页抓取账号数据 |
| `daily_search_budget` | int | `0` | `yt_search` 每天最多生成 / 执行的 YouTube 搜索 query 数；`0` 表示不设每日上限，本轮 query 数由平台缺口 / `discovery_limit` 决定 |
| `daily_trending_budget` | int | `0` | `yt_trending` 每天最多拉取的热门候选数；`0` 表示不设每日上限，本轮拉取规模由平台缺口 / `discovery_limit` 决定 |
| `daily_channel_budget` | int | `0` | `yt_channel` 每天最多选择的订阅频道数；`0` 表示不设每日上限，本轮频道数由平台缺口 / `discovery_limit` 决定 |
| `request_interval_seconds` | int | `2` | 预留的 YouTube 请求间隔配置；当前策略主要由单轮预算和 runtime 补池节奏控制 |
| `min_interval_minutes` | int | `3` | `YoutubeDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

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
| `min_interval_minutes` | int | `3` | `XDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

X 源健康状态（`ok` / `missing_cookie` / `expired_cookie` / `rate_limited` / `blocked`）由 `storage/x_health.py` 持久化，按 401 / 403 / 429 分别退避，连续 For-You 失败会自动暂停 For-You 拉取，状态经 `GET /api/sources/x/status` 暴露到插件 / 桌面 Web 设置页。账号订阅用 `x_creator_subscriptions` 表持久化，经 `GET/POST/DELETE /api/sources/x/creators` 管理。

### `[sources.zhihu]`

知乎 discovery 配置。知乎是浏览器插件登录态源：后端入队 `zhihu_tasks`，插件在已登录 `zhihu.com` 标签页中执行 `search` / `hot` / `feed` / `creator` / `related` 任务并把 `zhihu_*` 候选回写，后端再转换为 `source_platform="zhihu"` 的 `DiscoveredContent` 写入统一待评估候选池。`fetch-zhihu` 的事件 smoke 也复用同一张 `zhihu_tasks` 表，但任务类型是 `bootstrap_events`，命令本身只打印计数、不写 memory；guided init 里选择知乎时会显式收集同一类 `bootstrap_events` 结果，把浏览 / 收藏 / 点赞 / 动态收藏转换为首轮画像信号，并写回 `enabled=true`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让知乎参与候选池配比和后台 discovery。默认关闭，必须显式 opt-in；关闭后 `ZhihuDiscoveryProducer` 不入队任务，`/api/sources/zhihu/next-task` 也不再领取自动任务，扩展不会因已排队任务打开知乎标签页；`pool_source_shares.zhihu` 配额从有效配比中剔除 |
| `incremental_enabled` | bool | `false` | 是否允许知乎参与扩展在线周期回拉（`bootstrap_events`）。默认关闭；需同时开启 `[scheduler].source_incremental_enabled` 才会生效。开启后插件可能短暂打开前台标签页抓取账号数据 |
| `source_modes` | list[str] | `["search", "hot", "feed", "creator", "related"]` | 后台和 `openbiliclaw discover --source zhihu` 允许调度的知乎 discovery 分支。插件 side panel 与桌面 Web 配置页都提供五个显式勾选项。`search` 使用统一关键词 planner；`hot` 拉热榜；`feed` 拉首页推荐；`creator` 优先用最近任务结果里的作者主页作种子，没有历史种子时使用本轮 search / hot / feed 返回的作者页；`related` 优先用最近知乎候选 URL，没有历史种子时使用本轮已返回内容 URL 作相关扩展种子 |
| `daily_search_budget` | int | `0` | 知乎搜索 discovery 每日任务预算；`0` 表示不设每日上限，本轮关键词数由统一关键词 planner / fallback 画像兴趣和平台缺口决定 |
| `daily_hot_budget` | int | `0` | 知乎热榜 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_feed_budget` | int | `0` | 知乎首页推荐 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_creator_budget` | int | `0` | 知乎作者 discovery 每日任务预算；`0` 表示不设每日上限 |
| `daily_related_budget` | int | `0` | 知乎相关扩展 discovery 每日任务预算；`0` 表示不设每日上限 |
| `request_interval_seconds` | int | `3` | 后端等待任务时的轮询间隔 / 插件搜索节奏提示；真实平台请求仍发生在用户已登录浏览器内 |
| `min_interval_minutes` | int | `3` | `ZhihuDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

### `[sources.reddit]`

Reddit 来源配置。Reddit 日常 discovery 默认走随 OpenBiliClaw 安装的 `rdt-cli` 登录态命令后端；已连接浏览器插件会把 `reddit_session` 自动同步到 `~/.config/rdt-cli/credential.json`，插件不可用时才需要手动运行 `rdt login`。Cookie 不写进 `config.toml`：桌面 Web 设置页的 Reddit Cookie 覆盖输入框可手动粘贴（`PUT /api/config` 的 `sources.reddit.cookie` 为 API-only 字段，非 `config.toml` 键），非空新值路由到 rdt-cli credential store，与插件自动同步同一存储；粘贴内容缺少 `reddit_session` 时保存以 400 `missing_reddit_session` 显式拒绝，不静默丢弃。后端会拉取 `search` / `hot` / `subreddit` / `related` 候选后转换为 `source_platform="reddit"` 的 `DiscoveredContent` 并只写入统一待评估候选池；LLM 评估和入正式推荐池由后台 `DiscoveryCandidatePipeline` 统一处理。初始化阶段仍可入队 `reddit_tasks(type="bootstrap_events")`，插件在已登录 `reddit.com` 会话里读取 saved / upvoted / subscribed subreddit 并转换为 `favorite` / `like` / `follow` 画像信号。`extension` 可显式作为浏览器登录态 discovery 后端；默认 `rdt` / `opencli` 命令后端不可用或未登录时也会自动 fallback 到插件任务。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 Reddit 参与初始化 opt-in、候选池配比和后台 discovery。默认关闭，必须显式 opt-in；关闭后 `RedditDiscoveryProducer` 不入队任务，`pool_source_shares.reddit` 配额从有效配比中剔除 |
| `incremental_enabled` | bool | `false` | 是否允许 Reddit 参与扩展在线周期回拉（`bootstrap_events`）。默认关闭；需同时开启 `[scheduler].source_incremental_enabled` 才会生效。开启后插件可能短暂打开前台标签页抓取账号数据 |
| `backend` | string | `"rdt"` | Reddit 取数后端。`rdt` 使用默认安装的 rdt-cli 登录态命令后端，并优先使用插件同步的 `reddit_session` credential；`rdt login` 仅作为手动 fallback；`extension` 使用 OpenBiliClaw 浏览器插件和当前浏览器登录态，且仍负责 bootstrap 初始化信号；`opencli` / `auto` 为兼容命令路径。命令后端状态不是 `ready` 时，CLI / producer 会自动 fallback 到插件任务 |
| `source_modes` | list[str] | `["search", "hot", "subreddit", "related"]` | 后台和 `openbiliclaw discover --source reddit` 允许调度的 Reddit discovery 分支。`search` 使用统一关键词 planner，关键词池为空时回退画像兴趣；`hot` 默认拉 `r/all`；`subreddit` 优先用最近 Reddit 候选里的 subreddit 作种子；`related` 优先用最近 Reddit 内容 URL 作相关扩展种子 |
| `daily_search_budget` | int | `300` | Reddit 搜索 discovery 每日条目预算 |
| `daily_hot_budget` | int | `300` | Reddit 热门 discovery 每日条目预算 |
| `daily_subreddit_budget` | int | `300` | Reddit subreddit discovery 每日条目预算 |
| `daily_related_budget` | int | `300` | Reddit related discovery 每日条目预算 |
| `request_interval_seconds` | int | `3` | 后端等待任务时的轮询间隔 / 插件任务节奏提示；真实平台请求发生在用户已登录浏览器内 |
| `min_interval_minutes` | int | `3` | `RedditDiscoveryProducer` 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都允许检查执行 |

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
| `min_interval_minutes` | int | `3` | producer 两次执行之间的最小间隔；`--force` 可跳过本次检查，但不能绕过上游 `429` cooldown |
| `bootstrap_limit` | int | `300` | guided init / `fetch-bangumi` 默认公开收藏上限，保存范围 `1..1000` |

用户名不是登录凭据。guided init 的账号解析按三级优先取值：个人令牌 `/v0/me` > 显式/已配置公开用户名 > 浏览器扩展在已登录 bgm.tv 页面自动识别并上报的用户名（`discovery_runtime_state["bangumi_self_info"]`，见 extension 文档）；Bangumi-only guided init 三者至少满足一个，混合初始化全部缺失时只跳过 Bangumi 画像分支并提示“仍可用于 discovery”。init 请求显式发送空 username 时会覆盖并清除旧配置值；只有 username 字段缺失的旧客户端才回退已保存值。令牌存在时以 `/v0/me` 解析出的用户名为准（与显式用户名不一致会 WARNING 并覆盖）；同步期令牌被拒绝（401）时记 WARNING 并降级到匿名公开路径，不静默失败。完整边界见 [Bangumi 来源文档](bangumi.md)。

### `[sources.linuxdo]`

Linux.do 通过浏览器扩展在真实 `linux.do` task tab 内执行同源只读 JSON `GET`；后端不持有 Linux.do Cookie，也不直连站点。公开 search / hot / feed / creator / related discovery 不要求登录；本人 bookmarks / likes / read history 仅在扩展通过 `/session/current.json` 正面确认账号后读取。`_t` 只转换成登录布尔心跳，Cookie 值和原始响应不会上传。完整契约见 [Linux.do 来源文档](linuxdo.md)。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 Linux.do 参与候选池配比、后台 discovery 和扩展在线周期 bootstrap；默认关闭，必须显式 opt-in |
| `incremental_enabled` | bool | `false` | 是否允许 Linux.do 参与扩展在线周期回拉（`bootstrap_events`）。默认关闭；需同时开启 `[scheduler].source_incremental_enabled` 才会生效。开启后插件可能短暂打开前台标签页抓取账号数据 |
| `source_modes` | list[str] | `["search", "hot", "feed", "creator", "related"]` | 允许 producer 调度的五种只读分支；`search` claim 统一关键词，`creator` / `related` 使用最近结果或同轮结果作种子 |
| `daily_search_budget` | int | `0` | search 每 UTC 日任务预算；`0` 表示不设日上限，仍受缺口、关键词和任务条数上限约束 |
| `daily_hot_budget` | int | `0` | hot 每 UTC 日任务预算；`0` 表示不设日上限 |
| `daily_feed_budget` | int | `0` | feed 每 UTC 日任务预算；`0` 表示不设日上限 |
| `daily_creator_budget` | int | `0` | creator 每 UTC 日任务预算；`0` 表示不设日上限 |
| `daily_related_budget` | int | `0` | related 每 UTC 日任务预算；`0` 表示不设日上限 |
| `request_interval_seconds` | int | `3` | 同一任务内相邻 Linux.do GET 的最小间隔秒数，保存时裁剪到 `0..30`；同时作为 producer 等待任务结果的轮询节奏提示，不改变单请求超时或分页硬上限 |
| `min_interval_minutes` | int | `3` | `LinuxdoDiscoveryProducer` 两次执行的最小间隔；`0` 允许每个 refresh tick 检查 |
| `bootstrap_limit` | int | `300` | bookmarks / likes / read history 每个 scope 的默认条数上限，保存范围 `1..300`；bootstrap 按每页 20 条估算并自动扩到足够页数（300 条对应 15 页），生产任务最多 15 页；content executor 另保留 50 页绝对防御 cap |

`[scheduler].enabled=false` 只暂停 daemon 自动补池；用户显式运行 `openbiliclaw discover-linuxdo` 或 `openbiliclaw discover --source linuxdo` 时仍会执行。显式命令不会绕过 `[sources.linuxdo].enabled`、`source_modes`、daily budget、`min_interval_minutes`、候选池或扩展在线约束。

#### 配置页来源状态契约

插件 side panel 与桌面 Web `/web` 的平台源配置页统一读取 `GET /api/sources/status`。这个端点是**纯本地读取**：不会访问任何上游平台，也不会运行 `rdt` / `opencli` 命令。页面可见时每 30 秒刷新一次，但请求只到 OpenBiliClaw 本地后端；真实平台请求仅由用户显式初始化、发现、诊断任务或已启用的后台 producer 发起。

桌面 Web 的来源卡片是设置面板 DOM 的结构边界：Linux.do、V2EX 及后续来源必须作为来源列表中的同级节点闭合，不能嵌套或包住来源总览、调度、模型等其它设置面板。否则浏览器会按容错规则重排后续节点，表现为配置页标签仍在但面板内容空白；该结构由 `tests/test_desktop_web_linuxdo_settings.py` 固定检查。
### `[sources.v2ex]`

V2EX 是匿名公开 discovery 源，支持官方匿名 JSON API / Feed，以及可选的 API 2.0 PAT。`search`、`node`、`tab`、`hot`、`latest` 都只把 Topic 写入统一待评估池；Reply 不作为独立候选。PAT 只用于增强 Node / Topic 读取和 `/api/v2/member` live probe，401/403 时自动降级为匿名。扩展任务已接入四个只读 bootstrap scope，`init --yes-v2ex` 或 guided init 来源选择可以等待其结果；浏览器登录态与 PAT 分开显示。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `false` | 是否让 V2EX 参与候选池配比和后台 discovery；默认关闭 |
| `incremental_enabled` | bool | `false` | 是否允许 V2EX 参与扩展在线周期回拉（`bootstrap_profile`）。默认关闭；需同时开启 `[scheduler].source_incremental_enabled` 才会生效。开启后插件可能短暂打开前台标签页抓取账号数据 |
| `username` | string | `""` | 可选公开用户名；用于公开 discovery / bootstrap 的身份候选，不等于登录凭据 |
| `access_token` | string | `""` | 可选 V2EX API 2.0 PAT；设置页只显示是否已配置，保存前用 `/api/v2/member` 做只读校验 |
| `token_env` | string | `"OPENBILICLAW_V2EX_TOKEN"` | PAT 环境变量名，优先级高于 `access_token` |
| `source_modes` | list[str] | `[...]` | `search / node / tab / hot / latest` |
| `tab_modes` | list[str] | `["tech", "creative", "qna"]` | Tab Feed 分支 |
| `node_allowlist` | list[str] | `[]` | 手工指定 Node slug；为空时使用画像兴趣回退 |
| `node_blocklist` | list[str] | `["sandbox"]` | 不抓取的 Node |
| `node_downweight` | list[str] | `["promotions", "jobs", "deals"]` | 提高候选准入阈值到至少 `0.72`，且单批最多保留 2 条；不会像 blocklist 一样完全删除 |
| `daily_*_budget` | int | `120/180/80/40/40` | 五个分支的 UTC 日预算；只按全局去重和共享预筛后真正保留的候选扣费，HTTP 请求与已知重复不扣；`0` 表示不设日上限 |
| `request_interval_seconds` | int | `2` | 同一 client 两次请求之间的间隔 |
| `min_interval_minutes` | int | `5` | producer 两次执行之间的最小间隔；`--force` 仅绕过本地间隔，不绕过远端 cooldown |
| `detail_fetch_limit` | int | `15` | 每轮最多为多少条字段不完整的 Topic 补官方详情；`0` 关闭详情增强 |
| `reply_enrichment_limit` | int | `10` | PAT 可用时每轮最多为多少条 Topic 读取 Reply 第一页并生成确定性摘要；`0` 关闭 |
| `max_topic_chars` | int | `6000` | Topic 主楼进入候选前的最大文本长度 |
| `max_reply_digest_chars` | int | `1200` | Topic 讨论摘要的最大长度；Reply 不独立入池 |
| `max_profile_nodes` | int | `12` | allowlist / 画像 Node 进入单轮 Node 召回的最大数量 |
| `bootstrap_topics_limit` | int | `100` | guided init / bootstrap 每次最多导入的本人主题数 |
| `bootstrap_replies_limit` | int | `300` | guided init / bootstrap 每次最多导入的本人回复数 |
| `bootstrap_favorites_limit` | int | `300` | guided init / bootstrap 每次最多导入的收藏主题数 |
| `bootstrap_max_pages_per_scope` | int | `20` | 扩展任务每个 scope 的最大页面数 |

保存时 `source_modes` / `tab_modes` 必须非空且来自允许集合；Node / Tab slug 会小写、去重并限制字符与条数。所有数值字段在直接配置保存与 `PUT /api/config` 共用同一边界，未知 V2EX 字段和布尔伪装整数都会返回校验错误；读取旧配置时则安全裁剪到边界，避免历史异常值制造无界任务。

完整字段和公开路径见 [V2EX 来源文档](v2ex.md)。

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

平台特例：抖音只要本地 Cookie 存在即显示 `unverified`，必须由实际抖音任务确认；小红书 / 知乎优先使用插件上报的 `logged_in + updated_at`，知乎仅在从未收到浏览器心跳时回落最近任务历史；Reddit `backend="rdt"` 只读取本地 credential 文件。Bangumi 不探测登录，状态由本地开关与最近 producer run ledger 计算。Linux.do 的公开发现始终匿名可用，扩展 `_t` 布尔心跳只决定个人 bookmarks / likes / read-history 是否可尝试；V2EX 匿名时为 `no_auth`，配置 PAT 后由 live probe 区分验证结论，不会把 PAT 状态误写成浏览器登录态。`xsec_token` 只是小红书内容 URL 的访问令牌，不会据此判断账号已登录。

### `[scheduler]`

TOML 与显式环境变量覆盖在构造 `SchedulerConfig` 前统一归一为真实布尔值。`enabled`、`pause_on_extension_disconnect`、`profile_consolidation_enabled`、`profile_consolidation_archive_enabled`、`auto_update_enabled`、`auto_update_allow_prerelease` 接受常见 `true/false/1/0`；空字符串或无法识别的值回到各字段默认值，绝不会把字符串 `""` 留到 API 响应后再触发类型校验错误。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | daemon 后台调度总开关；插件设置页显示为「停止后台 LLM 请求」。关闭后 runtime 的刷新、补池预计算、账户同步、猜测兴趣、主动推送和七源扩展账号周期回拉都会跳过；手动 CLI / API 请求仍按显式操作执行。若候选池为空，推荐页可能暂时没有内容 |
| `llm_budget_max_calls` | int | `120` | daemon 后台 LLM 请求在 `llm_budget_window_seconds` 内的自设上限；达到后 `ContinuousRefreshController` 暂停自动 LLM / embedding 循环直到窗口滚动，并打一条 WARNING 提示可调大上限或手动继续。`0` 表示不启用预算。默认值按保护付费 API 额度的工程安全起点设定，正常单用户发现循环通常远低于该值 |
| `llm_budget_window_seconds` | int | `3600` | 后台 LLM 预算窗口长度（秒），最小 `60`；与 `llm_budget_max_calls` 配合形成固定窗口配额 |
| `pause_on_extension_disconnect` | bool | `false` | 开启后，daemon-owned 后台 LLM / embedding 工作只在浏览器插件有 `/api/runtime-stream` 连接、或刚断开仍处于宽限窗口内时运行；离线期间不会自动补新内容 |
| `extension_disconnect_grace_seconds` | int | `90` | 插件最后一个 `runtime-stream` 连接断开后的宽限秒数；小于等于 0 或无法解析时回退到 `90` |
| `discovery_cron` | string | `"0 */8 * * *"` | 兼容旧配置的保留字段；当前 runtime 不消费这个 cron，发现补池由轮询、候选池缺口、行为阈值和下方策略间隔驱动。插件与桌面 Web 设置页均不再暴露该字段，只能通过手改 `config.toml` 保留 |
| `pool_target_count` | int | `300` | 前端真实可换候选目标；允许范围 `1..600`。`count_pool_candidates()`（含预生成 / 分类 / 可打开 / 最近看过过滤 / topic window）达到目标时 refresh（含 `force_refresh`）返回 `pool_at_cap` 不再 discover；后台定时 refresh 采用约 90% 的低水位，略低于目标时不立即跑 discovery，等库存真正低于水位再补货。raw 素材库存由独立 raw ceiling `max(pool_target_count * 2, pool_target_count + 120)` 控制，不再被压成与可换目标相同 |
| `account_sync_interval_hours` | int | `6` | 账户侧长期信号同步间隔；运行时会低频拉取 history / favorites / following |
| `source_incremental_enabled` | bool | `false` | 七源扩展账号周期回拉的显式总开关。默认关闭，避免需要前台任务页的平台定期切换标签页并抢走焦点；旧配置未包含该字段时同样按关闭处理。只影响 runtime 自动入队，不影响手动初始化、手动 `fetch-*` 或正常 discovery |
| `source_incremental_hours` | int | `24` | `source_incremental_enabled=true` 后使用的全局周期（小时），范围 `0..168`；`0` 仍会关闭七源周期回拉。它复用浏览器登录态，不是无浏览器后台同步 |
| `xhs_incremental_hours` | int 或 null | `null` | 小红书周期覆盖；缺省 / `null` 继承全局，`0` 只关闭小红书，范围 `0..168` |
| `douyin_incremental_hours` | int 或 null | `0` | 抖音账号周期回拉默认关闭，避免 `bootstrap_profile` 为读取作品 / 收藏 / 点赞 / 关注而主动切到前台并打断浏览；设置 `1..168` 小时可显式开启，`0`、缺省或 API `null` 都恢复默认关闭。手动初始化、`fetch-douyin` 与后台 feed / search / hot discovery 不受影响 |
| `youtube_incremental_hours` | int 或 null | `null` | YouTube 周期覆盖；缺省 / `null` 继承全局，`0` 只关闭 YouTube，范围 `0..168` |
| `zhihu_incremental_hours` | int 或 null | `null` | 知乎周期覆盖；缺省 / `null` 继承全局，`0` 只关闭知乎，范围 `0..168` |
| `reddit_incremental_hours` | int 或 null | `null` | Reddit 周期覆盖；缺省 / `null` 继承全局，`0` 只关闭 Reddit，范围 `0..168` |
| `linuxdo_incremental_hours` | int 或 null | `null` | Linux.do 周期覆盖；缺省 / `null` 继承全局，`0` 只关闭 Linux.do，范围 `0..168` |
| `v2ex_incremental_hours` | int 或 null | `null` | V2EX 周期覆盖；缺省 / `null` 继承全局，`0` 只关闭 V2EX，范围 `0..168` |
| `refresh_check_interval_seconds` | int | `60` | `ContinuousRefreshController` 主循环轮询间隔；小于 `15` 或无法解析时回退默认值 |
| `eval_min_batch_size` | int | `15` | API daemon raw candidate 评估 drain 的最小聚合批量；允许范围 `1..90`，小流量候选会等待凑批以减少 LLM trickle 调用。手动 CLI 是一次性进程，固定立即 drain，不读取该等待策略 |
| `eval_max_wait_seconds` | float | `90.0` | API daemon raw candidate 评估 drain 的最长等待秒数；允许范围 `0..600`，单个候选最多等待该时长后会小批量送评。协调器按剩余等待时间唤醒 |
| `signal_event_threshold` | int | `6` | 累计多少条新行为事件后触发 `search + related_chain` 补池；小于 `1` 时回退默认值 |
| `trending_refresh_minutes` | int | `3` | `trending` 策略的最小刷新间隔（分钟）；小于 `1` 时回退默认值。**v0.3.186 起单位由小时改为分钟**并与各来源 producer 的 `min_interval_minutes` 对齐；旧配置里的 `trending_refresh_hours` 仍会被读取并按 ×60 换算（`3` 小时 → `180` 分钟），保存后写回新键 |
| `explore_refresh_minutes` | int | `3` | `explore` 策略的最小刷新间隔（分钟）；小于 `1` 时回退默认值。**v0.3.186 起单位由小时改为分钟**，旧键 `explore_refresh_hours` 同样按 ×60 换算（`12` 小时 → `720` 分钟）。**它是纯下游消费闸，不驱动关键词生产**：`KeywordPlanner._explore_domains_request()` 只在「一次常规 merged 关键词调用已经在构建中」且 B 站有真实缺口时，才把 `<explore_domains>` 块搭上去，从不为 explore 单独发起 LLM 调用；LLM 调用频率完全由 planner 自己的背压决定（`_due_platforms()` 要求 `keyword_kind="regular"` 的缓存低于 `kw_cache_low` **且**该平台有真实缺口，外加 B 站的 catalyst——池子低于目标或信号事件超阈值，均与 explore 无关）。explore 词以 `keyword_kind="explore"` 独立入库，**不计入**触发生产的那条水位。因此缩短本间隔只会让 `ExploreStrategy` 更频繁地从 explore 缓存 claim 并请求 B 站，不会增加 LLM 调用次数；缓存抽干后该策略自然停摆，由背压模型兜住。统一关键词 planner 复用同一时钟：当该间隔已到或距到期不足一个 `refresh_check_interval_seconds`，且 B 站仍有补货空间时，会把探索 query 生成合并进当轮关键词调用 |
| `discovery_limit` | int | `30` | 单轮 discovery wave 的候选上限；允许范围 `1..60` |
| `delight_queue_limit` | int | `20` | 惊喜推荐队列默认加载数量；允许范围 `1..100`。桌面 Web、移动 Web 和浏览器插件默认调用 `/api/delight/pending-batch` 时共享该值，显式 query `limit` 可临时覆盖 |
| `proactive_push_interval_seconds` | int | `120` | 主动推荐 / probe 推送循环间隔；小于 `30` 时回退默认值 |
| `speculator_idle_interval_minutes` | int | `30` | `ProfileUpdatePipeline` 空闲时检查猜测兴趣生命周期的间隔；小于 `5` 时回退默认值 |
| `profile_consolidation_enabled` | bool | `true` | 是否启用 12 小时画像整理（LLM 合并重复的喜欢 / 讨厌主题，见 soul 模块 `ProfileConsolidator`）。五个 `profile_consolidation_*` 字段都会由 `_render_config_toml` 写回，设置保存后不会回落默认值 |
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
| `feedback_batch_threshold` | int | `3` | 累计多少条推荐反馈后重算偏好。旧反馈批线用它做游标批的阈值；开启 `unified_interest_line` 后同一个值改在认知流水线里计数（INTEREST 缓冲里的 FEEDBACK 信号数达到它即立即消费）。**v0.3.18x 修复：此前 `_render_config_toml` 不 emit 这一行**，插件与桌面设置页的「反馈分析积累阈值」是只写不读——任何一次保存都会把用户调过的值静默复位成 3（并因此静默改掉兴趣层节奏）。现已随保存落盘。三面构造点（`api/runtime_context.py`、`cli._build_soul_engine`、OpenClaw bootstrap）也已全部透传，此前只有 API 一面在传 |
| `unified_interest_line` | bool | `true` | 统一兴趣更新线开关（`docs/plans/2026-07-27-unified-interest-line-spec.md`）。`true` 时：① `/api/feedback` 与 `/api/events` 的显式内容反馈只写 durable event 并唤醒 app-owned `EventProcessingScheduler`（`FeedbackBatchScheduler` 为兼容 alias），HTTP 不等待 pipeline/LLM；② `process_feedback_batch_if_needed()` 只领取 `feedback_type ∈ {like,dislike,comment,dismiss}` 且非 import 的行，按稳定 `feedback-event-{row_id}` 用 `checkpointed_enqueue_batch()` 将 buffer+cursor 原子发布到 `pipeline_state.json`，再调用 `tick_if_buffered()`；hypothesis/import 行只推进 cursor，retraction 走 generic 折价；③ v0.3.191 升级先用 pipeline checkpoint 的 owner-v2 cutover fence 跳过旧 direct-owned 尾部，`feedback_state.json` 只作兼容 provenance；④ 已退役写点 `feedback_preference_overwrite` 默认停写，但历史行仍可查询。`false` 保留为恢复旧反馈批线的回退开关 |
| `auto_update_enabled` | bool | `false` | 是否启用后端自动检查并应用新版本；默认关闭，只影响后端源码，不更新浏览器插件 |
| `auto_update_check_interval_hours` | int | `6` | 后端自动更新检查间隔（小时），最小 `1`；TOML 中的 `0` / 负数 / 非整数字符串加载时回退安全默认值 `6`，`PUT /api/config` 与 `save_config()` 对非整数或 `<1` 的值直接拒绝且不落盘；手动检查不受该间隔限制 |
| `auto_update_allow_prerelease` | bool | `false` | 是否允许 `backend-vX.Y.Z-rc/beta/dev` 预发布 tag 被后端自动更新选择；默认忽略 |
| `auto_update_allowed_remotes` | list[str] | OpenBiliClaw GitHub HTTPS / SSH | 允许自动更新快进的 `origin` allowlist；守卫校验 `ls-remote --get-url` 改写后的地址和 `remote get-url --all` 的全部值，任一不可信即拒绝且绝不自动改写 git 配置。规范化支持可选 `.git`、大小写不敏感与 GitHub 官方 `ssh.github.com[:443]` 等价；镜像包装、带凭据或未匹配地址继续以 `untrusted_remote` 拒绝 |

> 运行时护栏：
> 即使 `pool_target_count` 设得较高，单次 refresh 里的 discover wave 也由 `discovery_limit` 控制（默认 `30`，最大 `60`），避免一次性把全部缺口都打满。
> 后台 refresh 还会使用约 90% 的可换池低水位；池子只是轻微低于 `pool_target_count` 时不跑 discovery。B 站完整四策略补货在小缺口阶段优先只给 `search + related_chain` 预算，`trending/explore` 延后到更深缺口。
> `pause_on_extension_disconnect` 只约束后端 daemon 自己发起的后台 LLM / embedding 工作；用户手动点击刷新、CLI 显式命令、配置保存和普通读取接口不因为插件离线而被拦截。`runtime-stream` 连接断开由后端 receive-side detector 记录，浏览器 idle disconnect 后不会让 presence 状态卡住。
>
> 七源账号周期回拉先检查 `source_incremental_enabled` 总开关，再检查每个来源自己的 `sources.<slug>.incremental_enabled`；默认两者都为 `false`，因此不会检查扩展 presence、创建任务或打开标签页。总开关为 `true` 但某来源的 `incremental_enabled=false` 时，该来源会被跳过，其 scheduler-owned pending / stale in-progress 任务也会被取消，避免再开一次标签页。scheduler 新建任务带独立 owner 标记；升级前由持久调度状态明确记录的旧任务也可识别，两者都会在 tick 或插件领取前被标记失败。手动 `incremental=true` 任务不带 scheduler owner，因此不会被误停；已被扩展领取并正在执行的页面无法由后端强制瞬间关闭，但不会再被重领。总开关开启后才检查 presence 并应用全局 / 逐源周期；除抖音外，逐源字段在 TOML 中应省略以继承，`PUT /api/config` 可用 JSON `null` 恢复继承。抖音仍额外默认 `douyin_incremental_hours=0`，只有正整数会加入轮转。

### `[scheduler.pool_source_shares]`

候选池按平台族做保底配比，默认保存的 share 是 `bilibili:xiaohongshu:douyin:youtube:twitter:zhihu:reddit:bangumi:linuxdo:v2ex:weibo = 5:1:1:1:1:1:1:1:1:1:1`。旧配置缺少后续新增的平台 key 时会自动补齐默认 share；关闭的平台保留配置值但从运行时有效配比中剔除，剩余平台重新归一化吃满 `pool_target_count`。默认安装只启用 Bilibili，因此初始有效配比仍只有 Bilibili。

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
| `linuxdo` | int | `1` | Linux.do 平台族占比；`linuxdo-search` / `linuxdo-hot` / `linuxdo-feed` / `linuxdo-creator` / `linuxdo-related` 统一计入该族 |
| `v2ex` | int | `1` | V2EX 平台族占比；`v2ex-search` / `v2ex-node` / `v2ex-tab` / `v2ex-hot` / `v2ex-latest` 统一计入该族 |
| `weibo` | int | `1` | 微博平台族占比；`weibo-search` / `weibo-hot` / `weibo-creator` 统一计入该族 |

运行时会拆分两套 quota：前端可换来源目标用于补货和 `reactivate_under_quota_pool_sources()` 的缺口判断；raw ceiling 来源目标用于 `trim_pool_source_overflow()` / `trim_pool_to_target_count()` 的硬成本边界。小平台低于可换目标时，会优先保护 / 复活它们的候选，但不会超过 raw headroom；任一平台族 raw material 高于 raw ceiling 配额时，才会先压回配额内。B 站低于后台低水位且 `[sources.bilibili].enabled=true` 时，才由 B 站 discovery 补货；小缺口优先 `search + related_chain`，更深缺口再跑 `trending/explore`。抖音低于目标且 `[sources.douyin].enabled=true` 时，后台 `DouyinDiscoveryProducer` 会通过 `DouyinDiscoveryService(cache=True)` 触发 search / hot / feed 补池；YouTube 低于目标且 `[sources.youtube].enabled=true` 时，后台 `YoutubeDiscoveryProducer` 会在独立 loop 中触发 `yt_search` / `yt_trending` / `yt_channel`，主 refresh replenishment plan 不再 inline 调度 YouTube；X 低于目标且 `[sources.twitter].enabled=true` 时，后台 `XDiscoveryProducer` 会在独立 loop 中按预算和源健康触发 `search` / `feed` / `creator` 三个策略补池；知乎低于目标且 `[sources.zhihu].enabled=true` 时，后台 `ZhihuDiscoveryProducer` 会通过浏览器插件按 `source_modes` 触发 search / hot / feed / creator / related 补池；Reddit 低于目标且 `[sources.reddit].enabled=true` 时，后台 `RedditDiscoveryProducer` 默认通过 `rdt-cli` 按 `source_modes` 触发 search / hot / subreddit / related 补 raw candidates；命令后端不可用或显式切到插件后端时，入队 OpenBiliClaw 插件任务。Bangumi 低于目标且 `[sources.bangumi].enabled=true` 时，后台 `BangumiDiscoveryProducer` 直连官方匿名 API，按分支预算写 raw candidates，并遵循持久化限流冷却。Linux.do 低于目标且 `[sources.linuxdo].enabled=true` 时，后台 `LinuxdoDiscoveryProducer` 入队同源扩展任务，以五种只读模式写 raw candidates。

`openbiliclaw init` 会按用户选择写回可参与画像初始化的来源开关：知乎、Reddit、Linux.do、V2EX 与微博可通过扩展任务导入个人事件，Bangumi 仅在提供公开用户名时读取公开收藏；没有个人身份时，这些来源仍可按各自匿名能力参与 discovery。微博公开 discovery 不需要登录，但作为唯一画像来源时必须先收到已登录微博扩展 heartbeat；混合来源若微博未就绪会明确降级，不会把公开热搜冒充个人行为。Bilibili 默认启用，也可手动关闭。交互式初始化会按事件量给出十一平台候选池比例建议；插件设置页与桌面 Web 均可编辑开关和比例，并通过 `/api/config/source-share-suggestion` 重新生成建议值。

### `[discovery]`

**统一关键词规划器 / Discover 背压 / 评估输入**（`DiscoveryConfig`）。把"每平台各自定时调 LLM 生成搜索词"换成**缺口拉动的双缓冲背压模型**：一个关键词存储（cache + 历史 + 产出）夹在「生成」与「抓取」之间，生成只在缓存见底且池子有真实缺口时触发（一次合并 LLM 调用覆盖所有缺货平台，带历史去重 + 池子分布避让）。B 站 explore 方向也复用这条关键词存储：到达 `[scheduler].explore_refresh_minutes` 的 refresh plan 窗口且 B 站有补货空间时，planner 会把 `explore_domains` 合并进同一次关键词生成，而不是新增配置项或单独 caller。同一段也承载 discovery evaluator 的可选封面图输入开关。本段**与 `[llm.routes.discovery]` 是两个独立的表**——后者选择 discovery 模块使用的 Provider 实例链，本段是规划器 / 背压 / 评估输入调参。完整设计见 [`docs/plans/2026-06-14-discover-backpressure-refactor-design.md`](../plans/2026-06-14-discover-backpressure-refactor-design.md) §6 参数表。

> ✅ `unified_keyword_planner_enabled` **v0.3.124 起默认 `true`**：搜索词走统一规划器 + 关键词存储，本段其余字段随之生效。设为 `false` 可逐字回退到旧的逐平台搜索词生成路径（旧路径保留、回退无副作用）。

#### 保存与运行时应用状态

`PUT /api/config` 的持久化阶段继续执行候选配置校验、`config.toml.bak` 快照、完整写盘和凭据 patch 语义；只有这些步骤成功才会返回 2xx。受保护的 dialogue execution、dialogue settlement、event owner 或另一轮 runtime rebuild 正忙时，后端不再让 HTTP 请求同步等待最长 25 分钟，而是返回 HTTP 202：`apply_state="queued"`、单调递增的 `apply_revision`、`reloaded=false`。插件与桌面 Web 应把它显示为“已保存、等待后台应用”，不能当作保存失败。

后台队列只合并尚未开始的修订并保留最新值；正在应用的修订完成后会立即追上最新 pending。`GET /api/config/apply-status` 返回 `requested_revision / applied_revision / state / message / error / updated_at`，其中不含配置或秘密。最终成功广播 `config_reloaded`；最新修订失败会恢复最后一次已生效配置并广播 `config_reload_failed`。初始化与配置应用互斥：应用中的 `POST /api/init` 返回 `409 config_applying`，运行中的 init 继续让配置保存返回 `409 init_running`。

#### Web 与插件设置页的「高级功能」

桌面 Web 与浏览器插件 side panel 的设置页都提供独立的「高级功能」Tab：桌面端共 7 个 Tab，插件端共 6 个 Tab；两端固定使用同一套三个 section，字段语义、默认值和保存行为保持一致。

- **推荐增强**：包含 P1 用户视觉画像、P2 弹幕语义、P3 视频关键帧的开关和预热参数。三者都是排序信号加权，不是过滤；P1/P3 依赖图像 Embedding，P2 只需文本 Embedding。P1 每个极性反馈不足 8 条时安全 no-op。关闭任一开关会保留缓存与参数并回退到原排序，不影响现有主流程；关键帧和弹幕目前仅作用于 B 站。
- **多模态处理**：独立管理「图像 Embedding 能力」和「候选封面参与 LLM 评估」。前者是 P1/P3 的依赖，后者不会改变 P1/P3；Embedding provider、模型、凭据和探测仍在模型 Tab。
- **搜索词生成**：集中管理经典、混合、灵感三档模式及成本提示；option value、顺序和文案与桌面端 / 插件端一致。

两端保存按钮遵循同一状态机：配置无变化时禁用，有输入或程序化草稿修改时启用，请求进行中再次锁定。成功保存并以服务端配置重新回填后恢复禁用；请求失败会保留脏状态并重新允许保存，因此不会因无操作触发完整配置写入，也不会吞掉可重试的修改。

两端加载时都会显式回填 `visual_profile_enabled`、`keyframe_enabled`、`keyframe_max_frames`、`keyframe_fetch_limit`、`danmaku_enabled`、`danmaku_fetch_limit`、`danmaku_max_chars`，保存时在已有 `discovery` 快照展开之后显式写入，数值范围分别为 `keyframe_max_frames=1..12`、两个 fetch limit 为 `1..200`、`danmaku_max_chars=100..2000`，默认值为 `4 / 50 / 50 / 500`。因此关闭开关不会因为保存设置而丢失预热参数或缓存。

视觉相关能力保持显式 opt-in：`[llm.embedding].multimodal_enabled`、`multimodal_evaluation_enabled`、`visual_profile_enabled`、`keyframe_enabled` 的后端默认值、配置样例和两端初始控件均为 `false`。搜索词生成则默认使用“混合”，即 `inspiration_search_enabled=true`、`inspiration_replace_merged_keywords=false`；已有配置里显式保存的值保持不变。

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
| `keyword_digest_grace_hours` | int | `24` | 画像 digest 变化后，最近且安全的旧 `regular/pending` 关键词可继续领取的宽限时长，合法范围 `0..168`。整理时当前 digest 优先；旧词命中显式 dislike / 平台 avoid、过龄、重复或超过动态高水位都会过期，原 digest 与生成溯源不会改写。`0` 恢复旧版“digest 一变即硬过期”，也是独立回滚开关。GET/PUT 配置 API、热重载、CLI/OpenClaw 构造与 TOML round-trip 均透传该值 |
| `admission_min_score` | float | `0.60` | 普通推荐池统一入池最低分。候选行 / raw payload 显式 `score_threshold` 只能抬高门槛；来源标签如 `admission_policy="observed"` 不能绕过该分数门。探索类策略固定使用 `0.58`，平台 / 插件来源不能获得特权。支持范围为 `[0.5, 1]`，非法值回退默认值；下界与 evaluator 的 reason 省略契约绑定，禁止低于 0.5 的无 reason 候选入池 |
| `eval_prefilter_mode` | string | `"shadow"` | discovery evaluator 的 embedding 预过滤模式：`"off"` 不计算相似度；`"shadow"` 只记录 `prefilter-shadow` would-filter 日志但仍送 LLM；`"enforce"` 对 top-256 recall-visible 兴趣与 compact 兴趣域均低相似的非 explore 候选缓存低分并跳过 LLM。余弦值先夹到 `0..1`，单批过滤超过 50% 时 fail-open。非法值会被运行时配置校验拦截；OpenClaw、GET/PUT 配置接口与 daemon 热重载均透传该字段。上线时先用 shadow 观察 would-filter 中是否仍有高于 `admission_min_score` 的候选，再切 enforce |
| `candidate_eval_concurrency` | int | `3` | 候选 LLM 评估的期望 worker 数，合法范围 `1..3`；每个 worker 最多 30 条，因此总 raw 在途上限为 90。超出范围的手工 TOML / API 值按本段既有整型规则回退默认 `3`。有效值为 `min(本值, max(1, llm.concurrency-1))`，为聊天等交互保留一个全局 LLM 槽位；插件与桌面 Web 设置页可修改，CLI `config-show` 自动显示。移动 Web 没有配置面板，不适用。 |
| `inspiration_search_enabled` | bool | `true` | 是否启用 query inspiration 脑暴阶段。默认与 merged keyword planner 并行组成“混合”模式；`KeywordPlanner` 会通过搜索 provider 链获取搜索预览，再让 `discovery.keyword_inspiration` LLM caller 做 Profile Curator / Detail Expander，最终把带 `aspect_id/inspiration_id/expansion_id` 元数据的关键词写入 `discovery_keywords` |
| `inspiration_search_backends` | list[str] | `["local_cache", "platform_sources", "bing_rss", "exa", "you", "serply"]` | query inspiration 搜索后端顺序。`local_cache` 会先从本地 `content_cache` 抽取相关标题 / URL / 摘要作为 evidence，本地命中不消耗外部 grounding 预算；证据不足时才 fallback。`platform_sources` 会从用户已启用且当前可同步/可注入 bridge 的平台源里抽样做 inspiration-only grounding（B站 / YouTube / X / Reddit；抖音 direct client；小红书 / 知乎 bridge 可用时），只把标题 / URL / 摘要作为灵感证据，不写候选池；`bing_rss` 是无 key 免费全网搜索兜底（`bing.com/search?format=rss`，仅供个人本地 grounding，请遵守 Bing RSS 使用条款）；`exa` 优先用 `exa_api_key` 直连 Exa `POST /search`，未填 API Key 才回退 `mcporter call exa.web_search_exa`；`you` 优先用 `you_api_key` 直连 You.com `GET /search`，未填才回退 `mcporter call you.you-search`；`serply` 需要 `serply_api_key`，直连 Serply（[serply.io](https://serply.io)，文档见 [serply.io/docs](https://serply.io/docs)）的 `GET /v1/search`，没有 mcporter 兜底，留空即跳过该后端。某个后端报错 / 限流 / 返回空结果时会继续尝试后面的后端。mcporter 路径仍需要本机安装 Node CLI 并写入 `config/mcporter.json` |
| `exa_api_key` | string | `""` | Exa 直连 API Key（可选）。填写后 `ExaInspirationProvider` 直接调用 `https://api.exa.ai/search`（`x-api-key`），不再依赖 mcporter。留空时回退 mcporter（若已安装）；两者都没有则跳过该后端 |
| `you_api_key` | string | `""` | You.com 直连 API Key（可选）。填写后 `YouInspirationProvider` 直接调用 `https://api.ydc-index.io/search`（`x-api-key`），不再依赖 mcporter。留空时回退 mcporter（若已安装）；两者都没有则跳过该后端 |
| `serply_api_key` | string | `""` | Serply 直连 API Key（可选）。填写后 `SerplyInspirationProvider` 直接调用 `https://api.serply.io/v1/search`（`X-Api-Key`）。留空则跳过该后端（无 mcporter 兜底） |
| `inspiration_replace_merged_keywords` | bool | `false` | 实验性替换模式。仅在 `inspiration_search_enabled=true` 且 inspiration provider 可用时生效：due 平台跳过旧 `discovery.keyword_planner` merged call，只通过 search-backed inspiration flow 产词；当 B 站 explore 到期且有补货空间时，也会用同一轮共享 brainstorm / grounding stage 写入 `keyword_kind="explore"` 的探索词池。开 replace 前应先用 `keyword-inspiration-report` 跑 cohort 门禁，避免无质量数据直接替换 |
| `inspiration_breadth` | str | `"high"` | 探索广度档位（Phase 2 config 收敛，13→4）：`low` / `medium` / `high`。旧的 10 个 `inspiration_*` 细粒度旋钮已删除，其派生成内部常量的有效值由本档位决定（见下表）。**默认 `high`（更宽的素材/轴/关键词产量）**；`medium` 逐项等于旧的 `_DEFAULT_INSPIRATION_*` 默认值，需与收敛前行为逐项对齐时显式设 `medium`。注意 `high` 会把每轮真实 probe 搜索与 LLM 用量放大（daemon 常驻），成本敏感可设 `medium`/`low`。非法档位（非 `low`/`medium`/`high`）→ 配置错误（`ConfigError`），未设置回退 `high` |
| `eval_prefilter_mode` | string | `"shadow"` | discovery evaluator 的 embedding 预过滤模式：`"off"` 不计算相似度；`"shadow"` 只记录 `prefilter-shadow` would-filter 日志但仍送 LLM；`"enforce"` 将低于相似度阈值的非 explore 候选以低分缓存并跳过 LLM。非法值会被运行时配置校验拦截。上线时先用 shadow 观察 would-filter 中是否仍有高于 `admission_min_score` 的候选，再切 enforce |
| `eval_scorer` | string | `"llm"` | 候选相关性校准模式。`"llm"`（默认）保持既有批量评估与缓存；`"shadow"` 并跑 learned + 完整 LLM、仍由 LLM 决定 relevance，并写入完整 privacy-safe 对照；`"learned"` 供人工通过只读 gate 后显式启用，仅在完整 LLM 元数据与本批审计成功时由 learned 覆盖 relevance。两种校准模式当前都继续调用 LLM、绕过 normal eval cache，并把同时配置的 prefilter `enforce` 当成 `shadow`，不是降本开关。scorer 不可用、异常、长度 / 数值 / 维度 / digest 非法或审计失败均回退 LLM。OpenClaw、GET/PUT 配置接口与 daemon 热重载均透传该字段；非法值由运行时配置校验拒绝，旧文件加载则规范化为 `"llm"` |
| `multimodal_evaluation_enabled` | bool | `false` | 是否在 discovery batch evaluator 中加入候选封面图。默认关闭；开启后仅当当前 evaluation 路由支持图像输入且候选有 `cover_url` 时使用，否则自动退回纯文本评估 |
| `danmaku_enabled` | bool | `false` | 是否启用**弹幕文本**加成（P2）：B 站候选喂给推荐的语义只有 `title` + `description`，而 description 常是"求三连"之类的无信息文本、`body_text` 在 B 站路径恒为空；弹幕是 B 站独有信号，反映观众实际在讨论什么。抓取走 `comment.bilibili.com/{cid}.xml`（**无需鉴权**，`cid` 直接从已有的 `/x/web-interface/view` 响应读，零额外请求），清洗后嵌入为独立排序信号。**纯文本信号，无需多模态嵌入模型**（与 P1/P3 不同）；仅对 B 站视频有效。默认关闭时加成恒 0，排序逐字节一致 |
| `danmaku_fetch_limit` | int | `50` | 每轮预热处理的视频数上限。合法范围 `1..200` |
| `danmaku_max_chars` | int | `500` | 弹幕摘要字数上限。合法范围 `100..2000`；摘要以完整配置长度走稳定 document-embedding/cache 路径，不会静默截成固定 200 字前缀 |
| `keyframe_enabled` | bool | `false` | 是否启用**视频关键帧**加成（P3）：封面是 UP 主手选的营销图、常常标题党，不代表视频内容；B 站已为每个视频预生成关键帧雪碧图（进度条悬停预览），一次请求即可取到，**无需下载视频、无需 ffmpeg**。关键帧与共享视觉画像共用质心；因此即使只开 `keyframe_enabled`，也会在多模态 embedding 可用时构建质心，但 P1 封面 bonus 仍只由 `visual_profile_enabled` 控制。帧向量取 max-pool。需同时开 `[llm.embedding].multimodal_enabled` + 多模态嵌入模型；仅对 B 站视频有效（实测 30/30 覆盖率，时长 45s–5106s）。默认关闭时加成恒 0，排序与旧版逐字节一致 |
| `keyframe_max_frames` | int | `4` | 每个视频采样的关键帧数。合法范围 `1..12`，超范围回退默认值。相邻关键帧高度冗余，4 帧已能覆盖正片（采样跨全部雪碧图均匀分布并跳过片头片尾） |
| `keyframe_fetch_limit` | int | `50` | 每轮预热处理的视频数上限。合法范围 `1..200` |
| `visual_profile_enabled` | bool | `false` | 是否启用**用户视觉画像**加成（P1）：把点赞/踩过的推荐封面聚成 k 个均值质心，候选封面↔质心同模态余弦经 **margin 评分**映射为**有符号**加成（能分清 like/dislike 的区域 boost/suppress，分不清的 contested 区弃权；聚类前 cross-clean 标签噪声、聚类后 contested 检测），在 `serve()` 排序上与封面↔文本锚点加成并行叠加。质心构建调度与 P1 bonus 开关分离：`visual_profile_enabled` 关闭时仍可为已开启的 P3 构建共享质心。**冷启动门控**：per-polarity 不足 8 个封面时不建质心（排序不变）。质心和反馈更新时间绑定当前 embedding fingerprint / 维度；切换模型会重建。需同时开 `[llm.embedding].multimodal_enabled` + 多模态嵌入模型；与 `multimodal_evaluation_enabled` 互相独立。默认关闭/无反馈数据时加成恒 0，排序与旧版逐字节一致 |
| `multimodal_batch_size` | int | `8` | 图文评估 batch 上限。合法范围 `1..12`，超范围回退默认值；纯文本评估仍使用调用方原 batch size |
| `multimodal_image_max_px` | int | `384` | 送入评估器前封面图压缩后的最大边。合法范围 `128..768`，超范围回退默认值 |
| `multimodal_image_quality` | int | `72` | JPEG 压缩质量。合法范围 `40..90`，超范围回退默认值 |
| `multimodal_image_timeout_seconds` | int | `6` | 单张封面抓取与压缩超时秒数。合法范围 `1..20`，超范围回退默认值 |

视觉 / 弹幕预热的四个数量字段（`keyframe_max_frames`、`keyframe_fetch_limit`、
`danmaku_fetch_limit`、`danmaku_max_chars`）由配置文件和 `PUT /api/config` 使用同一组范围
校验，并在 load → save → load 后保持原值。预热只查询当前 fresh、可服务的候选池；网络、HTTP、
解析或 embedding 的瞬时失败不写完成状态，确认无数据或成功产生向量后才会推进状态。关键帧状态还
绑定采样算法签名；视觉质心、关键帧和弹幕 embedding 绑定 provider / model / dimension fingerprint，
因此换模型或维度会重建 / 重嵌入，不会把旧向量与新向量比较。

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

新配置及缺少该字段的 UI/API 回退默认选择 **混合 / `hybrid`**；已有配置若显式保存为经典或灵感则继续尊重原值，不做迁移覆盖。

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

### `[soul]`（态势门控与 task-scoped cognition rollout）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `preference_prompt_view` | string | `"legacy"` | Preference prompt 的独立输入视图，只允许 `legacy` / `compact-v1`。2026-08-06 SenseTime task gate 未放行 Preference compact，因此默认保留逐字节回滚路径 |
| `awareness_prompt_view` | string | `"compact-v1"` | 仅控制 `AwarenessAnalyzer.analyze_with_confusions()` / `soul.awareness_confusions` 的输入视图，只允许 `legacy` / `compact-v1`。2026-08-06 SenseTime token + quality gate 只放行了该 caller，因此默认启用；普通 `analyze()` / `soul.awareness` 固定为 `legacy`，不继承这个值 |
| `insight_prompt_view` | string | `"legacy"` | Insight prompt 的独立输入视图，只允许 `legacy` / `compact-v1`。Insight 尚无预声明 token 阈值且本轮未获放行，默认保留 `legacy` |
| `posture_gate_mode` | string | `"shadow"` | 深层写入一致性门控（认知画像流水线 Phase 3）。`shadow`=判定异步旁路、**零延迟不阻塞原写入**，判定只落台账（`shadow_accept`/`shadow_downgrade`/`shadow_reject`，LLM 异常记 `shadow_error`）；`enforce`=写入前同步判定，reject/downgrade 拦截深层写入（downgrade 转为待验证假设），异常/解析失败保守 downgrade；`off`=完全旁路、与未接门控前逐字节一致。门控作用面仅三处：对话 goal/value/state 深层候选、管线 VALUES/CORE 层、soul 整份重建（interest 快线与 ROLE 层永不过门控） |
| `posture_gate_force_enforce` | bool | `false` | 逃生门。切到 `enforce` 需满足 save-time 三条件（最早有效 shadow 判定距今 ≥14 天 **且** 近 14 天有效判定 ≥10 条 **且** 近 7 天 ≥1 条），否则保存被 blocking 拒绝。置 `true` 无条件放行——**有风险**：门控尚未校准即启用可能误拦或误放深层写入 |
| `topic_lifecycle_serialization` | string | `"off"` | topic 状态机的 archived 序列化排除开关（认知画像流水线 Phase 4，本版**唯一最小消费**）。`off`（默认）时 `build_profile_summary` 与未接状态机前**逐字节一致**（回放门）；`on` 时把 `archived` 状态的 topic 排出 LLM 可见画像（domain/tag 两级）。规范 owner 是 `soul.profile_views.set_topic_lifecycle_serialization`；进程启动时由 `create_app` / CLI 设置，旧 `discovery.strategies._utils` 路径仅保留兼容 re-export。仅 `off`/`on` 两值，其余落默认 `off` |
| `awareness_event_batch_size` | int | `300` | 认知循环觉察每轮 LLM 调用最多携带的未处理事件数（issue #169）。默认按 256k+ 上下文模型设计（~100 token/事件，正常 12h 窗口单次调用）；80-100K 上下文的本地模型（如 qwen3.8-27B）可调小到 80-150。范围 `10..900` |
| `insight_note_batch_size` | int | `150` | 认知循环洞察每轮 LLM 调用最多携带的新觉察 note 数。默认按 256k+ 上下文模型设计；小上下文模型可调小。范围 `10..450` |
| `cognition_max_tokens` | int | `32768` | 认知循环觉察/洞察 LLM 调用的输出 token 上限。默认匹配 256k+ 模型的 dense batch；小上下文模型或严格输出限制的 provider 可调小（如 8192）。范围 `1024..128000` |

三个 prompt view 从 TOML、`GET/PUT /api/config`、CLI runtime、API 热重载与 OpenClaw
bootstrap 一路独立透传到 `SoulEngine`；其中 Awareness 值只进入 with-confusions seam，普通
Awareness seam 固定为 `legacy`。未发布的聚合字段
`soul.cognition_prompt_view` 已删除且不作为兼容别名读取，避免一次配置误把三个任务全部
切到 compact；replay 仍显式渲染 A/B 双臂，不读取这些生产默认值。

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
- B 站与多源：`bilibili.browser.*`、`sources.bilibili.enabled`、`sources.browser.*`，以及小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do / Bangumi / V2EX / 微博的来源配置
- 调度：`scheduler.enabled`、`pause_on_extension_disconnect`、`extension_disconnect_grace_seconds`、`pool_target_count`、`account_sync_interval_hours`、eval drain 凑批参数、refresh / signal / trending / explore / discovery limit / proactive push / speculator idle 等 runtime 频率参数、十一个平台的 `pool_source_shares`、猜测兴趣参数、不喜欢领域探针参数、自动更新参数；设置页可调用 `/api/config/source-share-suggestion` 按已有事件和当前表单开关填入建议比例
- 高级功能（桌面 Web 与插件设置页均有「认知循环预算」区块）：`soul.awareness_event_batch_size`、`soul.insight_note_batch_size`、`soul.cognition_max_tokens`（issue #169）
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
- 首次启动的模板包含一个等待填写 Key 的 DeepSeek 占位实例；若用户在 `/setup/` 改选其他 Provider，向导会读取 `GET /api/config.issues`，只把其中明确指向 `llm.instances.<id>.*` 的 blocking 旧实例设为 `enabled=false` 并从全局链移除。被显式自定义模块链引用的实例不会被自动改写，正常或仅 warning 的既有实例也会保留；完整多实例整理仍由桌面/插件设置页负责。校验 400 会按 `ConfigUpdateResponse.config.issues` 展示具体原因，不再把响应 JSON 截成一段不可读文本。
- 写盘前会先用新配置构建 LLM registry；blocking issue 会返回 400 且不写入 `config.toml`。
- 写盘前会生成 `config.toml.bak`。持久化成功后接口统一返回 `202 apply_state="queued"` 和单调 `apply_revision`；后台热重载失败会恢复最后一次已生效的磁盘与内存 runtime 配置，并广播 `config_reload_failed`。如果恢复本身失败，状态接口保留人工恢复提示。
- `general.data_dir` 是热重载的明确例外：如果请求值解析后的 canonical 路径不同于当前 `RuntimeContext` 已打开并由进程级锁保护的数据目录，接口会把新路径写入 `config.toml`，但本进程排队应用其它字段时仍强制使用旧的 active data dir，并在 202 响应返回 `restart_required=true`。当前数据库 / MemoryManager 和同一请求中的抖音、X 外部凭据读写都继续落在 active data dir；只有完整退出并重新启动、取得新目录的 canonical runtime lock 后才切换。`GET /api/config/apply-status` 的 `applied` 只表示可热重载部分已经应用，不表示新数据目录已启用。
- 热重载与唯一 `DialogueSettlementQueue` 交接时保持 admission 开放，直到旧 worker 的 active job 与 backlog 真正排空，再在无 `await` 临界段原子暂停、撤销旧 permit 并注册新 worker；因此保存配置期间的聊天/待聊请求不再被直接丢弃。对话 LLM 单请求上限为 20 分钟，安全 drain 窗口相应为 25 分钟；桌面/插件自己的 60 秒请求预算到期只表示后端仍在等待安全切换，不会取消后端保存。超过 25 分钟才回滚，空字符串 `TimeoutError` 会转换为可读诊断。

## 模型列表发现（不写配置）

`POST /api/config/discover-models` 接收 `instance_id` 和当前页面的 `config.llm` 草稿，在内存副本中精确构建该实例，并调用其 OpenAI-compatible `GET /models`。它不会调用 `save_config()`、不会创建迁移备份，也不会改变默认链；masked API Key 继续复用已保存密钥。PC Web、插件和 `/setup/` 都把结果填入可编辑的模型下拉框，失败时保留用户手填值。该端点与 `POST /api/config/probe-service` 都属于降级恢复控制面：即使 active registry 因旧配置无法构建，也会精确使用新草稿执行，而不是被降级 guard 提前返回 503；普通业务 API 仍不放行。

OpenAI-compatible 协议没有列举 reasoning effort 能力的标准接口；接口返回的 Effort 候选是按 Provider / 模型生成的 `local_advisory`。用户可以继续手填，最终是否接受由具体服务在真实请求时决定。

`PUT /api/config` 返回 `ConfigUpdateResponse`：

| 字段 | 说明 |
|------|------|
| `ok` | 请求是否完成。校验失败时为 `false`。 |
| `reloaded` | 是否已热重载运行时组件。 |
| `rollback_applied` | 热重载失败后是否已从 `config.toml.bak` 回滚。 |
| `restart_required` | 已写入的配置是否仍需完整重启才能全部生效。canonical `data_dir` 与当前 active data dir 不同时固定为 `true`；路径未变的正常保存和成功降级恢复为 `false`，异常 bootstrap 也可能为 `true`。 |
| `apply_state` | 后台应用阶段：持久化成功的响应为 `queued`；状态接口还会返回 `applying`、`applied` 或 `failed`。 |
| `apply_revision` | 单调递增的后台应用修订号；与 `GET /api/config/apply-status` 的 `requested_revision` / `applied_revision` 对应。 |
| `config` | 保存后或回滚后的配置快照，API Key 仍默认 masked。 |
| `message` | 给 UI 展示的人类可读状态。 |

当 daemon 因 LLM registry 配置错误进入降级模式时，`GET /api/config` 会返回 `degraded=true`、`degraded_reason="llm_registry_unavailable"` 和 blocking issues。`PUT /api/config` 写入通过校验的修复配置后，会复用降级上下文已经保留的数据库、MemoryManager、事件总线和稳定 total gate，通过 `RuntimeContext.rebuild_from_config()` 原子构造完整运行时；成功后同步清除 context / `app.state` 的 degraded 状态、重绑 API 自有 feedback scheduler，并调用 `restart_background_tasks()`。只要 `data_dir` 未改变，响应为 `reloaded=true / restart_required=false`，`/setup/` 会在同一进程中立即进入账号连接步骤，插件与桌面设置页也会立即读到可用状态；如果同时改了 `data_dir`，其它修复仍可在本进程生效，但目录切换继续要求完整重启。核心构造失败会恢复 `config.toml.bak`、保持 503 guard 并返回 `ok=false`；没有旧文件可回滚且新配置已经落盘等异常 bootstrap 也会使用 `restart_required=true`。前端保留短期续接标记与 `/api/ping` 轮询，仅用于兼容旧后端和需要重启的响应。

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
| `OPENBILICLAW_SCHEDULER_SOURCE_INCREMENTAL_ENABLED` | 显式开启七源扩展在线周期回拉；缺省或 `false` 时完全不自动入队 |
| `OPENBILICLAW_SCHEDULER_SOURCE_INCREMENTAL_HOURS` | 开启后覆盖七源扩展在线周期回拉的全局小时数（`0..168`） |
| `OPENBILICLAW_SCHEDULER_XHS_INCREMENTAL_HOURS` | 覆盖小红书周期；空值按未覆盖处理，`0` 只关闭该源 |
| `OPENBILICLAW_SCHEDULER_DOUYIN_INCREMENTAL_HOURS` | 覆盖抖音周期；空值按未覆盖处理，`0` 只关闭该源 |
| `OPENBILICLAW_SCHEDULER_YOUTUBE_INCREMENTAL_HOURS` | 覆盖 YouTube 周期；空值按未覆盖处理，`0` 只关闭该源 |
| `OPENBILICLAW_SCHEDULER_ZHIHU_INCREMENTAL_HOURS` | 覆盖知乎周期；空值按未覆盖处理，`0` 只关闭该源 |
| `OPENBILICLAW_SCHEDULER_REDDIT_INCREMENTAL_HOURS` | 覆盖 Reddit 周期；空值按未覆盖处理，`0` 只关闭该源 |
| `OPENBILICLAW_SCHEDULER_LINUXDO_INCREMENTAL_HOURS` | 覆盖 Linux.do 周期；空值按未覆盖处理，`0` 只关闭该源 |
| `OPENBILICLAW_API_AUTH_ENABLED` | 覆盖 `[api.auth].enabled`（局域网密码门禁总开关） |
| `OPENBILICLAW_API_AUTH_PASSWORD` | 明文访问密码；启动时即 scrypt hash，优先于 `_PASSWORD_HASH`（适合 Docker / 多 worker 注入同一密码） |
| `OPENBILICLAW_API_AUTH_PASSWORD_HASH` | 预生成的 scrypt 密码哈希；覆盖 `[api.auth].password_hash` |
| `OPENBILICLAW_API_AUTH_SESSION_SECRET` | 登录态 HMAC 签名密钥；覆盖 `[api.auth].session_secret`（多进程共用同一密钥） |
| `OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS` | 覆盖 `[api.auth].session_ttl_hours`（0=永不过期） |
| `OPENBILICLAW_API_AUTH_TRUST_LOOPBACK` | 覆盖 `[api.auth].trust_loopback`（本机是否免登录） |
| `OPENBILICLAW_NO_XHS` | 设为 `1` 时永久跳过 `init` 的小红书接入，即使脚本传了 `--yes-xhs` |
| `OPENBILICLAW_NO_DOUYIN` | 设为 `1` 时永久跳过 `init` 的抖音接入，即使脚本传了 `--yes-douyin` |
| `OPENBILICLAW_NO_YOUTUBE` | 设为 `1` 时永久跳过 `init` 的 YouTube 接入，即使脚本传了 `--yes-youtube` |
| `OPENBILICLAW_NO_LINUXDO` | 设为 `1` 时永久跳过 `init` 的 Linux.do 接入，即使脚本传了 `--yes-linuxdo` |
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
| `OPENBILICLAW_LINUXDO_BOOTSTRAP_WAIT_SECONDS` | Linux.do 从任务入队到终态结果的总硬上限，默认 `1950` 秒（32.5 分钟）；pending 领取最多约 3 分钟，领取后还受按页数/scope/节流计算的最长约 29 分钟执行期限与 30 秒结果余量约束；显式较小值（如 `180`）可能截断已领取任务 |
| `OPENBILICLAW_LINUXDO_BOOTSTRAP_DEDUPE_HOURS` | Linux.do `bootstrap_events` 近期任务复用窗口，默认 `6` 小时；只复用在途或 `ok/empty` 终态，`failed/degraded` 会重新入队；设为 `0` 关闭复用，`fetch-linuxdo --force` 可绕过单次复用 |
| `OPENBILICLAW_LINUXDO_BOOTSTRAP_MAX_ITEMS` | Linux.do bookmarks / likes / read-history 每个 scope 的任务上限，默认 `300` |

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
- 可选 `docker-compose.https.yml` 只消费 `OPENBILICLAW_DOMAIN`，将 `8420` 收紧到宿主机 loopback，并通过 Caddy 发布 `80/443`；它不写入 `config.toml`，完整流程见 [HTTPS 部署](../https-deployment.md)

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
