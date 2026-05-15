# LLM 多模型支持

> 统一的多 LLM Provider 接口，支持 OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter，带 fallback、retry 和健康检查。

## 概述

`llm/` 包提供了一套抽象的 LLM 调用接口，上层模块（Soul Engine、Discovery Engine 等）通过 `LLMService` 或 `LLMRegistry` 发起调用，不需要关心底层用的是哪个模型。

核心设计：
- **Provider 抽象** — `LLMProvider` ABC 定义统一接口
- **Registry 管理** — 根据 config 自动注册可用 provider，支持 fallback
- **Service 门面** — `LLMService` 封装 prompt 组装 + 调用 + 校验
- **统一异常** — 所有 provider 错误归一化为标准异常类型

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 2.1 Provider 实现 | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter，带 retry + 超时 |
| 2.2 Provider Registry | ✅ | 自动注册 + fallback + health check |
| 2.3 Prompt 管理与 Service | ✅ | Prompt 构建器 + LLMService 门面 |
| 4.5 核心记忆加载 | ✅ | 统一 core memory 注入入口，覆盖 Soul 全链路 |
| 体验优化：B站动态语气 | ✅ | 推荐、画像总结和聊天 prompt 统一接入 `ToneProfile`，在“老B友”基础上按用户画像微调语气 |
| v0.3.0 Ollama embedding 兜底 | ✅ | `OllamaProvider.embed()` 走原生 `/api/embeddings`，配合 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需要额外的 embedding API Key |
| v0.3.0 EmbeddingService 双层缓存 | ✅ | L1 内存 + L2 SQLite 持久化；`build_embedding_service` 按 provider 自动选默认 model（gemini→gemini-embedding-001 / openai→text-embedding-3-small / ollama→bge-m3） |
| v0.3.20 Embedding 自动 fallback | ✅ | `LLMProvider.supports_embedding` 类属性显式声明 provider 是否真的有 embeddings endpoint。Claude / DeepSeek / OpenRouter 标 `False`（前者无 API、后两者继承自 OpenAIProvider 但实际后端不路由 embeddings）；OpenAI / Gemini / Ollama 标 `True`。`build_embedding_service` 据此跑 fallback 链（请求的 provider → ollama → gemini → openai），主 LLM 没有 embedding 能力时透明回退而不是返回 None |
| v0.3.20 OpenAI Provider embed | ✅ | `OpenAIProvider.embed()` 走 `/v1/embeddings`，默认 `text-embedding-3-small`。OpenAI 用户没显式配 embedding 时不再静默返回 None。失败返回 `[]`（与 Ollama / Gemini 一致），调用方降级处理 |
| v0.3.31 DeepSeek 空内容兜底 | ✅ | DeepSeek 返回 HTTP 200 但 `content=""` 时，provider 会重试一次；`reasoning_effort` 开启时仍先关闭 thinking 重试，普通模式则原参数重试，避免 explore / structured task 因一次空内容直接降级为空结果 |
| v0.3.32 Embedding 与 LLM Provider 解耦 | ✅ | `EmbeddingConfig` 拥有独立的 `api_key` / `base_url`；`build_embedding_service` 直接构造一个独立 provider 实例（不走 chat-side `LLMRegistry`），并把旧的 `embedding_wants_ollama` 自动注册 hack 删掉。老 config 留空 `api_key` 时透明回落到 `[llm.<provider>].api_key` 并打一条一次性 WARNING（`_emit_embedding_compat_warning`） |
| v0.3.32 OpenAI 协议兼容 provider | ✅ | 新增 `openai_compatible` 一级 provider（独立 `[llm.openai_compatible]` block），用于 Groq / Together / Azure OpenAI / vLLM / 自建等任何走 OpenAI 协议的服务。底层复用 `OpenAIProvider`，但 `provider_name="openai_compatible"`，与 `[llm.openai]` 互不干扰。`base_url` 必填（缺失会被 `_collect_config_issues` 拦下、`_maybe_openai_compatible_provider` 拒绝注册）。embedding 段也接受 `openai_compatible` |
| v0.3.69 Gemini reasoning-first 模型适配 | ✅ | `GeminiProvider._is_reasoning_first_model` 用 prefix 识别 `gemini-3.x` / `gemini-2.5-pro*`，json_mode 下不再附加 `thinking_budget=0`（这些模型会以 `400 INVALID_ARGUMENT` 拒绝）；`gemini-2.5-flash` 等非 reasoning-first 模型继续走省钱通路。pricing 补全 `gemini-3.1-pro-preview` / `gemini-3-pro-preview` 别名，配套 CLI / config / 文档统一改用真实模型 ID |

## 公开 API

### Provider 类

```python
from openbiliclaw.llm import (
    ClaudeProvider,
    DeepSeekProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
)

# 创建 provider
provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")
response = await provider.complete([{"role": "user", "content": "hello"}])
print(response.content)  # str
print(response.provider)  # "openai"
print(response.usage)     # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}

# 健康检查
available = await provider.health_check()  # bool

provider = OpenRouterProvider(
    api_key="or-...",
    model="openai/gpt-4o-mini",
    http_referer="https://example.com",
    x_title="OpenBiliClaw",
)

provider = GeminiProvider(
    api_key="gemini-key",
    model="gemini-2.5-flash",
)
```

### Registry

```python
from openbiliclaw.llm import build_llm_registry
from openbiliclaw.config import load_config

registry = build_llm_registry(load_config())
print(registry.available_providers)  # ["openai", "gemini", "deepseek", "ollama", "openrouter"]
print(registry.default_provider)     # "openai"

# 带 fallback 的调用（默认 provider 失败时自动尝试下一个）
response = await registry.complete([{"role": "user", "content": "hi"}])

# 全量健康检查
results = await registry.health_check_all()
# {"openai": HealthCheckResult(available=True, is_default=True), ...}
```

### LLMService

```python
from openbiliclaw.llm import LLMService

service = LLMService(registry=registry, memory=memory_manager)
response = await service.complete_socratic_dialogue(
    user_message="我最近喜欢看纪录片",
    history=[...],
)
# prompt 自动包含用户画像（core memory）和动态 tone profile，空响应自动拦截

response = await service.complete_structured_task(
    system_instruction="你要从用户行为中提取结构化偏好。",
    user_input='{"events": [...]}',
)
# 自动注入 core memory，并以 json_mode 调用 provider
```

#### 全局优先级队列(v0.3.63+)

`LLMService` 内部用 `PrioritySemaphore`(capacity=1, heapq + monotonic
counter) 串行化所有 `await registry.complete(...)` 调用,按 `caller`
tag 解析优先级,longest-prefix 命中:

| caller 前缀 | priority | 说明 |
|---|---|---|
| `recommendation.write_expression` | 1 | popup 可见的池子表达式回填 |
| `discovery.evaluate_batch` | 1 | 当前 discovery 批次评估 |
| `recommendation.delight_score` | 2 | 后台批量打分 |
| `soul.*` / `xhs.*` | 2 | 灵魂分析 / 小红书分类 |
| 其他 / 空 | 3 | 默认 |

数字越小越先服务。无竞争时 free passthrough 不增加开销。维护者新增
caller tag 时无需在意优先级——默认 priority=3 不会插队挤掉已知的
priority≤2 任务。

### 异常体系

```
LLMProviderError          # 基类
├── LLMRateLimitError     # 429 / rate limit
├── LLMTimeoutError       # 请求超时
└── LLMResponseError      # 响应无效（空内容）

LLMFallbackError          # 所有 provider 都失败
RegistryBuildError        # 无法构建 registry（无可用 provider）

LLMServiceError           # Service 层基类
├── LLMResponseContentError  # Service 层空响应
└── LLMProviderExecutionError  # Provider 调用失败
```

## 配置项

```toml
[llm]
default_provider = "openai"  # "openai" | "claude" | "gemini" | "deepseek" | "ollama" | "openrouter"

[llm.openai]
api_key = ""
model = "gpt-4o"
base_url = ""  # 留空使用默认，或设置兼容 API 地址

[llm.claude]
api_key = ""
model = "claude-sonnet-4-20250514"

[llm.gemini]
api_key = ""  # 也支持通过 GOOGLE_API_KEY / GEMINI_API_KEY 注入
model = "gemini-2.5-flash"

[llm.deepseek]
api_key = ""
# 默认 deepseek-v4-flash;可选 deepseek-v4-pro;旧 deepseek-chat / deepseek-reasoner 将于 2026/07/24 弃用
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"

[llm.ollama]
model = "llama3"
base_url = "http://localhost:11434"

[llm.openrouter]
api_key = ""
model = "openai/gpt-4o-mini"
base_url = "https://openrouter.ai/api/v1"
http_referer = ""
x_title = "OpenBiliClaw"
```

## 设计决策

1. **retry 策略**：传输 / provider 临时错误走 3 次重试 + 线性退避（0.25s × attempt）；通用 OpenAI-compatible 的 `LLMResponseError` 默认不重试。DeepSeek 例外：线上观测到它会偶发 HTTP 200 但 `content=""`，因此 `DeepSeekProvider` 对空内容额外重试一次
2. **fallback 顺序**：默认 provider 优先，然后按注册顺序尝试
3. **Protocol DI**：`SupportsComplete` Protocol 解耦了调用方和具体实现，测试时可注入 Fake
4. **Prompt 集中管理**：所有 prompt 在 `prompts.py` 中定义，不散落在各模块
5. **统一上下文注入**：`complete_with_core_memory()` / `complete_structured_task()` 负责把核心记忆注入到所有 Soul 相关任务里
6. **OpenAI-compatible 复用**：DeepSeek、OpenRouter 这类兼容 OpenAI 协议的 provider 复用同一套重试、超时和错误归一化逻辑，只在子类中注入默认地址或额外请求头
7. **Gemini 独立适配**：Gemini 走官方 `google-genai` SDK，不强行复用 OpenAI-compatible 抽象；provider 内部负责把统一 `messages` 渲染成 quickstart 风格的单文本 prompt
8. **Gemini 可选依赖降级**：环境里缺少 `google-genai` 时，`llm` 包和 registry 仍可正常导入；只有真正实例化 Gemini provider 时才会给出明确缺依赖错误
9. **Prompt 风格集中收口**：推荐、画像和聊天的“老B友”语气由共享 `ToneProfile` 驱动，不允许各模块各自发散成不同人格
