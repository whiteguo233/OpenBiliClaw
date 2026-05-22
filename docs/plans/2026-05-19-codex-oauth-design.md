# Codex OAuth 登录支持设计

**目标**

让 OpenBiliClaw 可以在用户明确 opt-in 时复用本机 Codex CLI 的 ChatGPT OAuth 凭据调用 OpenAI 协议接口，作为没有 API key 时的实验性认证路径。

**官方边界**

OpenAI 官方 API 文档仍以 Platform API key 作为通用 API 认证方式。Codex 文档说明 ChatGPT 登录 token 用于 Codex CLI、Codex IDE 插件和 Codex 云端环境，并建议 API 相关用法继续使用 OpenAI Platform API key。因此本功能必须标注为 **实验性 / 非官方 / 可能失效**，不能把它描述成稳定的第三方 OAuth 接入。

参考：
- https://developers.openai.com/api/reference/overview#authentication
- https://developers.openai.com/codex/auth

**核心决策**

- 不新增 provider：Codex OAuth token 仍用于 OpenAI 协议调用，复用 `OpenAIProvider`。
- 新增认证层：`codex_auth.py` 负责凭据读取、导入、状态、刷新和安全落盘。
- 首版不自建 PKCE：`openbiliclaw login codex` 复用官方 Codex CLI 登录结果（`~/.codex/auth.json`），再导入到 OpenBiliClaw 自己的凭据文件。
- 配置新增 `auth_mode`：`[llm.openai].auth_mode = "codex_oauth"` 时使用 Codex token；空值和 `"api_key"` 保持旧行为。
- Provider 使用 `token_provider`：请求前获取有效 token，401 时带锁强制刷新并重试一次。
- 安全默认：`codex_oauth` 只允许空 `base_url` 或 OpenAI 官方 API 域名，避免把 ChatGPT token 发给第三方 OpenAI-compatible 代理。

**凭据来源与存储**

来源：
- 默认导入 `~/.codex/auth.json`
- `openbiliclaw login codex` 在没有可导入凭据时可调用 `codex login`，由官方 Codex CLI 完成浏览器登录；完成后再导入
- 支持 `--source <path>` 导入非默认位置，便于测试和迁移

OpenBiliClaw 存储：
- 默认路径：`~/.openbiliclaw/codex_auth.json`
- 目录权限尽量设为 `0700`
- 文件权限尽量设为 `0600`
- JSON 只保存运行必需字段：`access_token`、`refresh_token`、`expires_at`、`account_id`

**Token 刷新策略**

- 主动刷新：`get_valid_codex_token()` 在 token 过期前 5 分钟刷新。
- 被动刷新：OpenAI API 返回 401 时，`OpenAIProvider` 调用强制刷新回调并重试一次。
- 并发保护：刷新使用 `asyncio.Lock`，避免多个 LLM 请求同时刷新同一个 token。
- 失败处理：刷新失败抛出 `CodexAuthError`，上层展示“重新运行 `openbiliclaw login codex`”。

**范围**

修改文件：
- `src/openbiliclaw/config.py`：`LLMProviderConfig.auth_mode`，配置渲染和诊断。
- `src/openbiliclaw/llm/openai_provider.py`：支持 `token_provider` 与 401 单次刷新重试。
- `src/openbiliclaw/llm/registry.py`：`auth_mode=codex_oauth` 时构造 OpenAI provider。
- `src/openbiliclaw/cli.py`：新增 `login codex` 命令。
- `src/openbiliclaw/api/models.py` / `src/openbiliclaw/api/app.py`：让 `/api/config` 保留和更新 `auth_mode`。
- `config.example.toml`：新增配置示例和风险注释。

新增文件：
- `src/openbiliclaw/llm/codex_auth.py`：Codex 凭据模型、导入、状态、刷新、CLI 登录辅助。

文档：
- `docs/modules/llm.md`
- `docs/modules/config.md`
- `docs/modules/cli.md`
- `docs/changelog.md`

**不在首版范围内**

- 自建 OAuth PKCE 浏览器流程：需要先验证 OpenAI 是否仍允许第三方复用 Codex client_id，不能作为默认实现。
- Device Code Flow：无头环境后续再考虑。
- 多 token 轮换 / credential pool：单用户项目暂不需要。
- 将 Codex OAuth token 用于 `openai_compatible` 或自定义 `base_url`。

**风险**

1. **非官方集成**：OpenAI 可能随时改变 Codex token 格式、刷新端点、权限或服务端校验。
2. **凭据格式漂移**：Codex CLI 的 `auth.json` 是外部工具内部文件，字段结构可能变化。
3. **订阅与额度差异**：ChatGPT 账号 token 不等价于 Platform API key，模型可用性、速率限制和计费口径可能与 API key 不同。
4. **安全泄露**：必须阻止把 Codex token 发送到第三方 `base_url`。
5. **区域限制**：部分地区登录或调用可能返回 403。

**验收标准**

- `openbiliclaw login codex --import` 可导入已有 `~/.codex/auth.json`。
- `openbiliclaw login codex` 在无本地凭据时尝试调用官方 `codex login`，完成后导入凭据。
- `openbiliclaw login codex --status` 显示 token 状态、账号、过期时间，不泄露 token。
- `openbiliclaw login codex --logout` 删除 OpenBiliClaw 本地 token。
- `auth_mode = "codex_oauth"` 后，OpenAI provider 使用 `get_valid_codex_token()` 而不是 `api_key`。
- Token 临期时主动刷新；请求 401 时强制刷新并重试一次。
- `auth_mode = "codex_oauth"` 且配置了第三方 `base_url` 时给出 blocking 配置问题。
- 现有 `api_key` 模式行为不变。
