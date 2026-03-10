# Gemini Provider 设计

## 背景
当前仓库已经支持 `openai`、`claude`、`deepseek`、`ollama`、`openrouter` 五类 LLM provider，并通过 `LLMRegistry`、`LLMService` 和统一配置层进行调用。用户这次要求按 Gemini 官方 quickstart 接入新的 provider，也就是直接使用 `Gemini Developer API`，而不是 Google Cloud Vertex AI。

参考官方 quickstart：
- `google-genai` 是官方推荐 Python SDK
- `genai.Client()` 可直接从 `GEMINI_API_KEY` 读取 API key
- 示例模型使用 `gemini-2.5-flash`

## 目标
- 新增 `GeminiProvider`
- 支持 `default_provider = "gemini"`
- 新增 `[llm.gemini]` 配置段
- provider 底层使用官方 `google-genai` SDK
- 保持现有 provider 抽象、registry、健康检查和文档结构一致

## 方案选择
采用独立 provider 方案：`GeminiProvider(LLMProvider)`。

原因：
- Gemini quickstart 推荐 `google-genai`，不是 OpenAI-compatible 端点
- Gemini SDK 同时提供同步和异步接口，适合直接适配现有抽象
- 避免把 Gemini 强行塞进 `OpenAIProvider`，减少后续 JSON mode、usage 字段和错误语义偏差

## 架构设计
### Provider
新增 `src/openbiliclaw/llm/gemini_provider.py`：
- 构造参数支持 `api_key`、`model`
- 默认模型为 `gemini-2.5-flash`
- 使用 `genai.Client(api_key=...)`
- 请求时走 `client.aio.models.generate_content(...)`
- 将 OpenBiliClaw 现有 `messages` 列表拼接为单个文本 prompt
- `json_mode=True` 时通过 `GenerateContentConfig(response_mime_type="application/json")` 请求结构化输出

### Prompt 适配
Gemini quickstart 的最小请求形式是 `contents="..."`，而仓库上层统一传入 OpenAI 风格 `messages`。这轮不改上层接口，只在 provider 内部做适配：
- 按顺序遍历 `messages`
- 输出为带角色前缀的纯文本 prompt
- system / user / assistant 都保留，避免丢上下文

示例：

```text
[SYSTEM]
你是一个有帮助的助手。

[USER]
你好

[ASSISTANT]
你好，请问想聊什么？
```

### 配置层
在 `src/openbiliclaw/config.py` 中：
- 为 `LLMConfig` 新增 `gemini: LLMProviderConfig`
- 允许 `default_provider = "gemini"`
- 当默认 provider 为 `gemini` 时，若 `config.toml` 未填写 `api_key`，允许回退读取环境变量 `GOOGLE_API_KEY` / `GEMINI_API_KEY`
- 若配置和环境变量都缺失，则运行时校验报错

### Registry
在 `src/openbiliclaw/llm/registry.py` 中：
- 有 `config.llm.gemini.api_key` 或 Gemini 官方环境变量时注册 `gemini`
- 当 `default_provider = "gemini"` 时设为默认 provider

## 配置样例
`config.example.toml` 中新增：

```toml
[llm]
default_provider = "gemini"

[llm.gemini]
api_key = ""
model = "gemini-2.5-flash"
```

说明：
- 推荐直接在环境里设置 `GEMINI_API_KEY`
- `config.toml` 中的 `api_key` 仍然保留，方便与现有 provider 风格一致

## 错误处理与边界
- 这轮只支持文本输入/文本输出
- 这轮不实现 Gemini 文件上传、多模态、工具调用或流式返回
- 若 SDK 返回空文本，统一映射为 `LLMResponseError`
- provider 错误继续归一化为 `LLMProviderError` / `LLMRateLimitError` / `LLMTimeoutError`

## 测试策略
### 配置测试
- `default_provider = "gemini"` 时配置能正确加载
- 当 `api_key` 为空但存在 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` 时，运行时校验通过
- 当配置与环境变量都缺失时，运行时校验失败

### Provider 测试
- `GeminiProvider` 默认模型正确
- 能把 `messages` 渲染为 Gemini 可接受的文本 prompt
- `json_mode=True` 时会请求 `application/json`
- 能正确标准化返回内容和 usage

### Registry 测试
- `build_llm_registry()` 能注册 `gemini`
- `default_provider = "gemini"` 时 registry 默认 provider 正确
- 仅依赖官方环境变量时也能注册

## 文档更新
实现完成后同步更新：
- `docs/modules/llm.md`
- `docs/modules/config.md`
- `docs/changelog.md`
- `docs/v0.1-todolist.md`
