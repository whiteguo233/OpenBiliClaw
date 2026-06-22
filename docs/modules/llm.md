# LLM 多模型支持

> 统一的多 LLM Provider 接口，支持 OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible，带显式备选 Provider、retry 和健康检查。

## 概述

`llm/` 包提供了一套抽象的 LLM 调用接口，上层模块（Soul Engine、Discovery Engine 等）通过 `LLMService` 或 `LLMRegistry` 发起调用，不需要关心底层用的是哪个模型。

核心设计：
- **Provider 抽象** — `LLMProvider` ABC 定义统一接口
- **Registry 管理** — 根据 config 自动注册可用 provider，fallback 默认关闭、可在配置中显式打开
- **Service 门面** — `LLMService` 封装 prompt 组装 + 调用 + 校验
- **统一异常** — 所有 provider 错误归一化为标准异常类型

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 2.1 Provider 实现 | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible，带 retry + 超时 |
| 2.2 Provider Registry | ✅ | 自动注册 + 可配置 fallback + health check |
| 2.3 Prompt 管理与 Service | ✅ | Prompt 构建器 + LLMService 门面 |
| 4.5 核心记忆加载 | ✅ | 统一 core memory 注入入口，覆盖 Soul 全链路 |
| v0.3.117 reasoning-first 探活 | ✅ | `LLMProvider.health_check()` 与配置页 LLM 测试探针不再强制传入极小 `max_tokens`，避免 SenseNova 等 OpenAI-compatible reasoning-first 模型先产出 `message.reasoning`、尚未到 `message.content` 就被截断，从而误报空响应 |
| v0.3.75 Per-module LLM 路由生效 | ✅ | `LLMService` 按 caller bucket 路由 `[llm.soul/discovery/recommendation/evaluation]`，通过 `LLMRegistry.complete_provider()` 精确调用 chat-capable provider；provider 错误不 spill 到 default，拼错 provider INFO 一次后降级 |
| v0.3.75 Provider per-call model | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OpenAI-compatible 的 `complete(..., model=...)` 支持单次模型覆盖，不修改 provider 实例默认 `_model` |
| 体验优化：B站动态语气 | ✅ | 推荐、画像总结和聊天 prompt 统一接入 `ToneProfile`，在“老B友”基础上按用户画像微调语气 |
| v0.3.0 Ollama embedding 兜底 | ✅ | `OllamaProvider.embed()` 走原生 `/api/embeddings`，配合 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需要额外的 embedding API Key |
| v0.3.0 EmbeddingService 双层缓存 | ✅ | L1 内存 + L2 SQLite 持久化；`build_embedding_service` 按 provider 自动选默认 model（gemini→gemini-embedding-001 / openai→text-embedding-3-small / ollama→bge-m3） |
| v0.3.113 Embedding 目标维度 | ✅ | `[llm.embedding].output_dimensionality` 默认 1024，与 Ollama `bge-m3` 对齐；Gemini 传 `output_dimensionality`，`provider = "openai"` 且模型为 `text-embedding-3-*` 时传 `dimensions`，Ollama / OpenRouter / 泛 OpenAI-compatible 等未确认支持的后端不传。L2 cache 仅在 provider 确认支持目标维度时按 `model#dim=N` 签名隔离，同一文本的不同维度向量不会互相覆盖，也不会把未生效的兼容后端标成伪维度 |
| v0.3.97 EmbeddingService 实时探活 | ✅ | `EmbeddingService.probe()` 绕过 L1/L2 缓存直接打一次 provider，返回是否拿到非空向量；供 `/api/health.embedding_ready` 做**实时**就绪判定（缓存命中的旧成功不会掩盖 provider 已掉线 / 模型没拉）。`/api/health` 侧自带 TTL + single-flight，probe 不缓存结果、每次都真打 |
| v0.3.114 配置页服务探测 | ✅ | `POST /api/config/probe-service` 对用户当前表单草稿做无写入真实探测：LLM 走临时 `LLMRegistry.complete_provider()`，embedding 走临时 `EmbeddingService.probe()`，结果供 PCWeb / 插件设置页行内展示 |
| v0.3.20 Embedding fallback 能力识别 | ✅ | `LLMProvider.supports_embedding` 类属性显式声明 provider 是否真的有 embeddings endpoint。Claude / DeepSeek / OpenRouter 标 `False`（前者无 API、后两者继承自 OpenAIProvider 但实际后端不路由 embeddings）；OpenAI / Gemini / Ollama 标 `True`。当前只在 `[llm.embedding].fallback_provider` 非空时尝试一个显式备选 provider |
| v0.3.89.1 OpenRouter embedding 显式路径 | ✅ | `[llm.embedding].provider = "openrouter"` 现在会被 `_build_dedicated_embedding_provider` 构造成 `OpenRouterProvider` 实例（必须配 `model = "<vendor>/<model>"`，例如 `google/gemini-embedding-2-preview`；无显式 model 时拒绝构建，避免 404）。`OpenRouterProvider.supports_embedding` 仍保持 `False` —— 只有用户显式在 `[llm.embedding]` 选 openrouter 才走这条路径，不污染 chat-side 的自动回退链。`[llm.openrouter]` 的 `http_referer` / `x_title` 也会透传给 embedding 实例，让 OpenRouter 后台账单与 chat 流量归一 |
| v0.3.20 OpenAI Provider embed | ✅ | `OpenAIProvider.embed()` 走 `/v1/embeddings`，默认 `text-embedding-3-small`。OpenAI 用户没显式配 embedding 时不再静默返回 None。失败返回 `[]`（与 Ollama / Gemini 一致），调用方降级处理 |
| v0.3.31 DeepSeek 空内容兜底 | ✅ | DeepSeek 返回 HTTP 200 但 `content=""` 时，provider 会重试一次；`reasoning_effort` 开启时仍先关闭 thinking 重试，普通模式则原参数重试，避免 explore / structured task 因一次空内容直接降级为空结果 |
| v0.3.32 Embedding 与 LLM Provider 解耦 | ✅ | `EmbeddingConfig` 拥有独立的 `api_key` / `base_url` / `output_dimensionality`；`build_embedding_service` 直接构造一个独立 provider 实例（不走 chat-side `LLMRegistry`），切换 chat 模型不会改变 embedding provider / model / 维度，并把旧的 `embedding_wants_ollama` 自动注册 hack 删掉 |
| v0.3.x 显式 fallback provider | ✅ | 自动 fallback 链已移除。`LLMRegistry.complete()` 只在 `[llm].fallback_provider` 非空时按 `default_provider → fallback_provider` 尝试；embedding 只在 `[llm.embedding].fallback_provider` 非空时尝试一个备选 provider，空 provider 不再跟随 `[llm].default_provider` |
| v0.3.98 Ollama 作 chat fallback 识别 | ✅ | `_ollama_is_chat_capable()` 新增第四个入口：`[llm].fallback_provider = "ollama"`。此前只认 `[llm.ollama] model` / `default_provider` / 模块 override，导致用户把本地 Ollama 设为 chat 兜底、却没单独配 `[llm.ollama] model` 时，Ollama 被判为 embedding-only 并被 `_fallback_order()` 静默剔除，主 provider 失败直接 `LLMFallbackError`。现在尊重该意图（无 `model` 时用 `llama3` 默认，需本地已 `ollama pull` chat 模型；`bge-m3` 这类 embedding 模型仍无法兜底 chat）|
| v0.3.32 OpenAI 协议兼容 provider | ✅ | 新增 `openai_compatible` 一级 provider（独立 `[llm.openai_compatible]` block），用于 Groq / Together / Azure OpenAI / vLLM / 自建等任何走 OpenAI 协议的服务。底层复用 `OpenAIProvider`，但 `provider_name="openai_compatible"`，与 `[llm.openai]` 互不干扰。`base_url` 必填（缺失会被 `_collect_config_issues` 拦下、`_maybe_openai_compatible_provider` 拒绝注册）。embedding 段也接受 `openai_compatible` |
| v0.3.69 Gemini reasoning-first 模型适配 | ✅ | `GeminiProvider._is_reasoning_first_model` 用 prefix 识别 `gemini-3.x` / `gemini-2.5-pro*`，json_mode 下不再附加 `thinking_budget=0`（这些模型会以 `400 INVALID_ARGUMENT` 拒绝）；`gemini-2.5-flash` 等非 reasoning-first 模型继续走省钱通路。pricing 补全 `gemini-3.1-pro-preview` / `gemini-3-pro-preview` 别名，配套 CLI / config / 文档统一改用真实模型 ID |
| v0.3.71 Prompt-cache 与 400 诊断 | ✅ | `build_awareness_prompt` / `build_batch_content_evaluation_prompt` 的 user prompt 按稳定画像在前、本次批次在后排序，并使用 `sort_keys=True` 的确定性 JSON；`OpenAIProvider._map_error()` 会把 OpenAI-compatible HTTP 400 响应体摘要写入 WARNING 和错误文本，便于定位 MiMo 等兼容服务的请求 schema 问题 |
| v0.3.71 Awareness 缓存形态回归锁 | ✅ | `build_awareness_prompt` 的 system 内容固定为模块级常量 `_AWARENESS_SYSTEM_PROMPT`，user 块顺序锁定为 `<soul_profile>` → `<preference_summary>` → `<recent_events>`，并通过 `tests/test_llm_prompts.py` 的 byte-equal / 末尾块 / 不同字典 key 序仍产相同字节三组回归测试保证未来改动不会再把变量数据放进 system、不把 recent_events 之后塞入稳定块、或丢掉 `sort_keys=True` |
| v0.3.74 结构化输出共享解析 | ✅ | 新增 `llm/json_utils.py`，统一提供 `extract_llm_json_list()` / `extract_llm_json_object()` / `parse_llm_json_tolerant()`。调用方可传 item/object predicate 和 wrapper aliases，兼容 root array/object、`results/items/data/output/scores/evaluations` 等 wrapper、singleton dict、Markdown fenced JSON、JSONL、多 root echo 后最终结果，以及 MiMo 形态的 malformed `{ [ ... ] }` 数组包裹 |
| v0.3.74 Ollama embedding 空凭据静默本地默认 | ✅ | `embedding.provider="ollama"` 且 embedding `api_key/base_url` 为空时直接构造本地 Ollama provider，默认 `http://localhost:11434/v1`；如果 chat-side `[llm.ollama].base_url` 非空，会复用并规范化到 `/v1`，不再触发 `_emit_embedding_compat_warning()`。远端 embedding provider 留空凭据时仍保留一次性向后兼容 WARNING |
| v0.3.77 LM Studio JSON mode 兼容 | ✅ | `OpenAIProvider` 的 `json_mode=True` 对普通 OpenAI-compatible 后端默认使用 `json_object`，遇到 `response_format.type` 只允许 `json_schema/text` 时用通用 `json_schema` 重试；对本地 LM Studio（默认 `localhost/127.0.0.1:1234` 或 URL 含 `lmstudio` / `lm-studio`）首次请求即不发送 `response_format`，依赖 prompt 约束 JSON，避免 compat 层在 `json_object` / `json_schema` 下丢失 `message.content` 后再浪费一整次 LLM 调用 |
| v0.3.78 Codex OAuth 实验认证 | ✅ | `[llm.openai].auth_mode="codex_oauth"` 时，OpenAI provider 复用 Codex CLI 的 ChatGPT OAuth 凭据；`codex_auth.py` 负责导入 `~/.codex/auth.json`、安全落盘、临期刷新，`OpenAIProvider` 在 401 时强制刷新并重试一次。该路径为非官方实验集成，只允许 OpenAI 官方 `base_url` |
| v0.3.x LLM 限流识别 | ✅ | `is_llm_rate_limit_error()` 会沿异常链识别 `LLMRateLimitError`、cooldown、429 / quota / resource exhausted 文本；discovery / recommendation 批量调用据此跳过逐条 fallback，避免一次 provider 限流放大成 N 个必失败调用和堆栈日志 |
| v0.3.x Eval-batch 负样本锚定与跨平台公平 | ✅ | `build_batch_content_evaluation_prompt` 新增可选 `negative_examples` kwarg；非空时在 user prompt `<source_context>` 与 `<content_batch>` 之间插入 `<negative_examples>` 块（`sort_keys=True` 决定性 JSON）。`None` / `[]` 退回原 user 字节形态以保留 cold-start 缓存前缀。`_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` 加入永久规则：按话术 / 商业意图 / 标题结构层面 pattern-match 候选与示例，不要看关键词重叠；混源 batch 中不得仅因 `source_platform` 不同而抬高或压低 preference score，只能把平台作为内容语境。规则改动一次后 system message 保持 call-invariant |
| v0.3.x dislike-aware prompts | ✅ | `build_preference_analysis_prompt` 明确把 negative / dislike / thumbs_down 事件限制为 `disliked_topics` 与风格避让证据，禁止提取为正向兴趣；`build_awareness_prompt` 可从近期 dislike 生成“最近开始避开 X”的保守观察；单条 / 批量推荐表达 prompt 会消费 `profile_summary.disliked_topics`，命中避雷项时不得热情背书 |
| v0.3.x 避雷探针多样性 prompt | ✅ | `build_avoidance_generation_prompt` 会携带 `existing_avoidance_details`，让 LLM 看到已有 active 的 `source_mode`、`source_signal`、体验轴和 specifics；system prompt 要求同一 `source_mode` + 同一粗主题 / 证据源只生成一个候选，已有 AI positive_boundary 时不再输出 AI 教程 / 测评 / 趋势换皮项 |

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

# 单次调用覆盖模型；不会写回 provider._model
response = await provider.complete(
    [{"role": "user", "content": "hello"}],
    model="gpt-4.1-mini",
)

# JSON mode；普通 OpenAI-compatible 后端使用 response_format 约束并保留 json_schema fallback。
# 本地 LM Studio 首次请求即跳过 response_format，依赖 prompt 约束 JSON 输出。
response = await provider.complete(
    [{"role": "user", "content": "只返回 JSON 对象"}],
    json_mode=True,
)

# 健康检查
available = await provider.health_check()  # bool
# health_check 使用 provider 默认输出预算，兼容先输出 reasoning 再输出 content 的服务。
# 设置页 / 插件的配置探针也遵循同一原则，不额外压小 max_tokens。

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

### Codex OAuth 凭据辅助

```python
from openbiliclaw.llm.codex_auth import (
    get_valid_codex_token,
    import_codex_credentials,
    load_codex_credentials,
)

# 导入官方 Codex CLI 登录态，默认读取 ~/.codex/auth.json，
# 写入 ~/.openbiliclaw/codex_auth.json。
credentials = import_codex_credentials()
print(credentials.account_id)

# Provider 运行时会调用它；临期时自动刷新。
token = await get_valid_codex_token()
```

Codex OAuth 是实验路径：OpenAI 官方 API 认证仍以 Platform API key 为准；该模块只复用本机 Codex CLI 凭据，不自建 OAuth PKCE 浏览器流程，也不会把 token 打印到 CLI 输出。

### Registry

```python
from openbiliclaw.llm import build_llm_registry
from openbiliclaw.config import load_config

registry = build_llm_registry(load_config())
print(registry.available_providers)  # ["openai", "claude", "gemini", "deepseek", "ollama", "openrouter", "openai_compatible"]
print(registry.default_provider)     # "deepseek"

# 默认不 fallback；如需备选，设置 [llm].fallback_provider 为第二个 provider
response = await registry.complete([{"role": "user", "content": "hi"}])

# 精确调用某个 chat-capable provider，不走 fallback；用于 per-module override
response = await registry.complete_provider(
    "deepseek",
    [{"role": "user", "content": "hi"}],
    model="deepseek-v4-flash",
)
assert registry.is_chat_capable("ollama") in (True, False)

# 全量健康检查
results = await registry.health_check_all()
# {"openai": HealthCheckResult(available=True, is_default=True), ...}
```

### 配置草稿探测 API

```http
POST /api/config/probe-service
```

该接口面向设置页，不写配置文件。后端会把请求中的 `config.llm` 合并到当前配置的内存副本，然后按 `kind` 真实打一次目标服务：

- `kind="llm"`：构建临时 `LLMRegistry`，校验 `default_provider` 是 chat-capable，再调用 `complete_provider(provider, ...)` 发送最小 chat completion。
- `kind="embedding"`：构建临时 `EmbeddingService`，调用 `probe()` 绕过 L1/L2 cache 获取一次真实向量。

失败以 `ok=false` 的正常响应返回，前端可直接显示 provider / model / latency / error；详见 [配置参考](config.md)。

### LLMService

```python
from openbiliclaw.llm import LLMService
from openbiliclaw.llm.service import module_overrides_from_config

service = LLMService(
    registry=registry,
    memory=memory_manager,
    module_overrides=module_overrides_from_config(config),
)
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

from openbiliclaw.llm import is_llm_rate_limit_error

try:
    await service.complete_structured_task(system_instruction="...", user_input="...")
except Exception as exc:
    if is_llm_rate_limit_error(exc):
        # 批量调用方可跳过逐条 fallback，等待下一轮调度重试。
        ...
```

### 结构化 JSON 解析 helper

```python
from openbiliclaw.llm.json_utils import extract_llm_json_list, extract_llm_json_object

scores = extract_llm_json_list(
    response.content,
    wrapper_aliases=("scores", "evaluations"),
    item_predicate=lambda item: isinstance(item, dict) and "score" in item,
)

profile_delta = extract_llm_json_object(
    response.content,
    wrapper_aliases=("result", "data"),
    object_predicate=lambda obj: isinstance(obj, dict) and "summary" in obj,
)
```

这些 helper 是 MiMo / OpenAI-compatible / reasoning 模型结构化输出的统一容错边界。调用方仍应用 predicate 限定自己真正接受的 shape，避免 schema echo 或 prompt 示例被误当作结果。

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

#### 分模块路由(v0.3.75+)

`LLMService` 的 `module_overrides` 来自 `module_overrides_from_config(config)`。
路由不使用 caller 第一段朴素判断，而是内置 bucket：

| caller 前缀 | module bucket |
|---|---|
| `soul.*` | `soul` |
| `discovery.search/explore/trending/related.*`、`yt_search.*`、`sources.xhs.*` | `discovery` |
| `recommendation.delight_score`、`recommendation.evaluate_batch`、`discovery.evaluate*`、`eval.*` | `evaluation` |
| 其他 `recommendation.*` | `recommendation` |

命中 override 后走 `registry.complete_provider(provider, ..., model=model)`：

- override provider 错误 / rate-limit：直接报错，不自动 spill 到 default。
- override provider 未注册或不是 chat-capable：按 `(bucket, provider)` INFO 一次，然后走默认 provider 路径；是否跨 provider fallback 取决于 `[llm].fallback_provider` 是否非空。
- 只填 `model` 不填 `provider`：使用 `registry.default_provider` + per-call model。

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
default_provider = "deepseek"  # "deepseek" | "openai" | "claude" | "gemini" | "ollama" | "openrouter" | "openai_compatible"

[llm.openai]
api_key = ""
model = "gpt-4o"
base_url = ""  # 留空使用默认，或设置兼容 API 地址
auth_mode = "" # "" / "api_key" / "codex_oauth"

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
base_url = "http://localhost:11434/v1"

[llm.openrouter]
api_key = ""
model = "openai/gpt-4o-mini"
base_url = "https://openrouter.ai/api/v1"
http_referer = ""
x_title = "OpenBiliClaw"
```

## 设计决策

1. **retry 策略**：传输 / provider 临时错误走 3 次重试 + 线性退避（0.25s × attempt）；通用 OpenAI-compatible 的 `LLMResponseError` 默认不重试。DeepSeek 例外：线上观测到它会偶发 HTTP 200 但 `content=""`，因此 `DeepSeekProvider` 对空内容额外重试一次。HTTP 400 会记录 provider response body 摘要，避免只看到 `Error code: 400`
2. **fallback 顺序**：默认关闭。chat 只在 `[llm].fallback_provider` 非空时按默认 provider 优先、随后这个显式备选 provider 尝试；embedding 只在 `[llm.embedding].fallback_provider` 非空时按显式 provider 优先、随后这个备选 provider 尝试。Embedding provider 留空表示禁用，不再跟随默认 LLM。
3. **Protocol DI**：`SupportsComplete` Protocol 解耦了调用方和具体实现，测试时可注入 Fake
4. **Prompt 集中管理**：所有 prompt 在 `prompts.py` 中定义，不散落在各模块
5. **统一上下文注入**：`complete_with_core_memory()` / `complete_structured_task()` 负责把核心记忆注入到所有 Soul 相关任务里
6. **OpenAI-compatible 复用**：DeepSeek、OpenRouter 这类兼容 OpenAI 协议的 provider 复用同一套重试、超时和错误归一化逻辑，只在子类中注入默认地址或额外请求头
7. **Gemini 独立适配**：Gemini 走官方 `google-genai` SDK，不强行复用 OpenAI-compatible 抽象；provider 内部负责把统一 `messages` 渲染成 quickstart 风格的单文本 prompt
8. **Gemini 可选依赖降级**：环境里缺少 `google-genai` 时，`llm` 包和 registry 仍可正常导入；只有真正实例化 Gemini provider 时才会给出明确缺依赖错误
9. **Prompt 风格集中收口**：推荐、画像和聊天的“老B友”语气由共享 `ToneProfile` 驱动，不允许各模块各自发散成不同人格
10. **Prompt-cache 约定**：高频结构化 builder 的 system prompt 必须保持静态；user prompt 按“画像 / 长期偏好 / 来源上下文 / 本批内容”从稳定到易变排序，并使用确定性 JSON，便于 DeepSeek / Claude / OpenAI / Gemini 的 provider-side prompt cache 复用前缀
11. **结构化输出只在 helper 处放宽**：业务模块不再各自手写 JSON 截取逻辑；容错集中在 `json_utils.py`，模块侧用 predicate 收紧语义，避免一个 provider 的异常 shape 修复污染其他任务。
12. **分模块 override 不隐式改意图**：`[llm.<module>]` 命中时必须精确调用用户指定的 chat provider；只有 provider 拼错或不是 chat-capable 时才降级到默认链并 INFO 一次。模型覆盖通过 per-call `model=` 完成，避免污染 provider 实例状态或影响其他模块。
13. **Codex OAuth 只做认证层**：`auth_mode="codex_oauth"` 不注册新 provider，而是给现有 `OpenAIProvider` 注入动态 token provider。该模式只允许 OpenAI 官方 `base_url`，防止 ChatGPT OAuth token 泄露给 OpenAI-compatible 代理。
