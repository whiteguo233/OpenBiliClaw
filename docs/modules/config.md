# 配置参考

> `config.toml` 所有配置段落详解。

## 快速开始

```bash
cp config.example.toml config.toml
# 编辑 config.toml，填入 LLM API Key
```

## 配置段落

### `[general]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `language` | string | `"zh"` | Agent 输出语言（`zh` / `en`） |
| `data_dir` | string | `"data"` | 数据目录（记忆、Cookie、数据库） |

### `[llm]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `default_provider` | string | `"openai"` | 默认 Provider：`openai` / `claude` / `gemini` / `deepseek` / `ollama` / `openrouter` |

### `[llm.openai]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | OpenAI API Key（default_provider=openai 时必填） |
| `model` | string | `"gpt-4o"` | 模型名称 |
| `base_url` | string | `""` | 留空使用官方 API，可设置兼容 API 地址 |

### `[llm.claude]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | Anthropic API Key（default_provider=claude 时必填） |
| `model` | string | `"claude-sonnet-4-20250514"` | 模型名称 |

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
| `model` | string | `"deepseek-chat"` | 模型名称 |
| `base_url` | string | `"https://api.deepseek.com"` | API 地址 |

### `[llm.ollama]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `model` | string | `"llama3"` | 本地模型名称 |
| `base_url` | string | `"http://localhost:11434"` | Ollama 服务地址 |

> Ollama 不需要 API Key，适合本地开发测试。

### `[llm.openrouter]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `api_key` | string | `""` | OpenRouter API Key（default_provider=openrouter 时必填） |
| `model` | string | `"openai/gpt-4o-mini"` | OpenRouter 模型名称 |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API 地址 |
| `http_referer` | string | `""` | 可选的 `HTTP-Referer` 请求头 |
| `x_title` | string | `"OpenBiliClaw"` | 可选的 `X-Title` 请求头 |

> `http_referer` 和 `x_title` 都是可选项；留空时不会阻止请求发送。

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

### `[scheduler]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用定时发现 |
| `discovery_cron` | string | `"0 */4 * * *"` | 发现任务 cron 表达式 |

### `[storage]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `db_path` | string | `"data/openbiliclaw.db"` | SQLite 数据库路径 |

### `[logging]`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `level` | string | `"INFO"` | 控制台日志级别 |
| `file_level` | string | `"DEBUG"` | 文件日志级别 |
| `directory` | string | `"logs"` | 日志目录 |
| `filename` | string | `"openbiliclaw.log"` | 日志文件名 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `OPENBILICLAW_BILIBILI_COOKIE` | 集成测试用 B 站 Cookie |
| `GOOGLE_API_KEY` | Gemini 官方推荐 API Key 环境变量，优先级高于 `GEMINI_API_KEY` |
| `GEMINI_API_KEY` | Gemini 官方兼容环境变量，`default_provider=gemini` 时可替代 `llm.gemini.api_key` |
