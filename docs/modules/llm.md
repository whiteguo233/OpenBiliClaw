# LLM 多模型支持

> 统一的多 LLM Provider 接口，支持 OpenAI / Claude / DeepSeek / Ollama / OpenRouter，带 fallback、retry 和健康检查。

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
| 2.1 Provider 实现 | ✅ | OpenAI / Claude / DeepSeek / Ollama / OpenRouter，带 retry + 超时 |
| 2.2 Provider Registry | ✅ | 自动注册 + fallback + health check |
| 2.3 Prompt 管理与 Service | ✅ | Prompt 构建器 + LLMService 门面 |
| 4.5 核心记忆加载 | ✅ | 统一 core memory 注入入口，覆盖 Soul 全链路 |

## 公开 API

### Provider 类

```python
from openbiliclaw.llm import (
    ClaudeProvider,
    DeepSeekProvider,
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
```

### Registry

```python
from openbiliclaw.llm import build_llm_registry
from openbiliclaw.config import load_config

registry = build_llm_registry(load_config())
print(registry.available_providers)  # ["openai", "deepseek", "ollama", "openrouter"]
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
# prompt 自动包含用户画像（core memory），空响应自动拦截

response = await service.complete_structured_task(
    system_instruction="你要从用户行为中提取结构化偏好。",
    user_input='{"events": [...]}',
)
# 自动注入 core memory，并以 json_mode 调用 provider
```

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
default_provider = "openai"  # "openai" | "claude" | "deepseek" | "ollama"
default_provider = "openrouter"  # 也支持 "openrouter"

[llm.openai]
api_key = ""
model = "gpt-4o"
base_url = ""  # 留空使用默认，或设置兼容 API 地址

[llm.claude]
api_key = ""
model = "claude-sonnet-4-20250514"

[llm.deepseek]
api_key = ""
model = "deepseek-chat"
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

1. **retry 策略**：3 次重试 + 线性退避（0.25s × attempt），`LLMResponseError` 不重试（空响应换 provider 也不会变好）
2. **fallback 顺序**：默认 provider 优先，然后按注册顺序尝试
3. **Protocol DI**：`SupportsComplete` Protocol 解耦了调用方和具体实现，测试时可注入 Fake
4. **Prompt 集中管理**：所有 prompt 在 `prompts.py` 中定义，不散落在各模块
5. **统一上下文注入**：`complete_with_core_memory()` / `complete_structured_task()` 负责把核心记忆注入到所有 Soul 相关任务里
6. **OpenAI-compatible 复用**：DeepSeek、OpenRouter 这类兼容 OpenAI 协议的 provider 复用同一套重试、超时和错误归一化逻辑，只在子类中注入默认地址或额外请求头
