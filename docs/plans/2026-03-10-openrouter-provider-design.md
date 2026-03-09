# OpenRouter Provider 设计

## 背景
当前仓库已经支持 `openai`、`claude`、`deepseek`、`ollama` 四类 LLM provider，并通过 `LLMRegistry`、`LLMService` 和统一配置层进行调用。用户希望新增 `openrouter` provider，并同时支持 OpenRouter 特有的可选请求头配置。

## 目标
- 新增 `OpenRouterProvider`
- 支持 `default_provider = "openrouter"`
- 新增 `[llm.openrouter]` 配置段
- 支持可选请求头：`HTTP-Referer`、`X-Title`
- 保持现有 provider 架构与测试风格一致

## 方案选择
采用 `OpenAIProvider` 子类化方案：`OpenRouterProvider(OpenAIProvider)`。

原因：
- OpenRouter 兼容 OpenAI 风格 API，适合复用已有超时、重试、错误归一化、JSON mode 逻辑
- 与现有 `DeepSeekProvider`、`OllamaProvider` 结构一致
- 避免把 OpenRouter 混成 `openai` 配置伪装，保持配置和文档语义清晰

## 架构设计
### Provider
新增 `src/openbiliclaw/llm/openrouter_provider.py`：
- 默认 `base_url = "https://openrouter.ai/api/v1"`
- 支持 `api_key`、`model`
- 支持可选字段：`http_referer`、`x_title`
- 通过覆盖 `OpenAIProvider` 的额外请求头钩子注入 OpenRouter 特有请求头

### OpenAI 兼容扩展点
在 `OpenAIProvider` 中新增一个小的可覆写方法，例如 `_extra_headers()`：
- 默认返回 `{}`
- 子类可覆盖以返回额外请求头
- `complete()` 执行请求时统一 merge 这些头

### 配置层
在 `src/openbiliclaw/config.py` 中：
- 为 `LLMConfig` 新增 `openrouter: ProviderConfig`
- 允许 `default_provider = "openrouter"`
- 当默认 provider 为 `openrouter` 时，运行时校验要求 `api_key` 必填
- `http_referer`、`x_title` 为可选配置，不参与必填校验

### Registry
在 `src/openbiliclaw/llm/registry.py` 中：
- 有 `api_key` 时注册 `openrouter`
- 当 `default_provider = "openrouter"` 时，设为默认 provider
- `summarize_registry()` 和 CLI `config-show` 自然通过 registry 反映 `openrouter`

## 配置样例
`config.example.toml` 中新增：

```toml
[llm]
default_provider = "openrouter"

[llm.openrouter]
api_key = ""
model = "openai/gpt-4o-mini"
base_url = "https://openrouter.ai/api/v1"
http_referer = ""
x_title = "OpenBiliClaw"
```

## 错误处理与边界
- 若未设置 `http_referer` / `x_title`，不报错，直接省略请求头
- 这轮不实现 OpenRouter 平台特定的模型路由或排序能力
- 这轮只把 OpenRouter 视为一个普通 provider 纳入统一 registry

## 测试策略
### 配置测试
- `default_provider = "openrouter"` 时配置能正确加载
- 缺少 `api_key` 时运行时校验失败

### Provider 测试
- `OpenRouterProvider` 默认 `base_url` 正确
- 能正确注入 `HTTP-Referer` 和 `X-Title`
- 省略可选头时仍可调用

### Registry 测试
- `build_llm_registry()` 能注册 `openrouter`
- `default_provider = "openrouter"` 时 registry 默认 provider 正确

## 文档更新
实现完成后同步更新：
- `docs/modules/llm.md`
- `docs/modules/config.md`
- `docs/changelog.md`
