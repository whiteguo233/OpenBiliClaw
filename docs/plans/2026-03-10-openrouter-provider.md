# OpenRouter Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 OpenBiliClaw 新增 `openrouter` LLM provider，并支持 OpenRouter 特有的可选请求头配置。

**Architecture:** 采用 `OpenAIProvider` 子类化方案，新增 `OpenRouterProvider` 复用现有 OpenAI-compatible 调用逻辑。通过在 `OpenAIProvider` 中引入一个可覆写的额外请求头钩子，实现 OpenRouter 的 `HTTP-Referer` 和 `X-Title` 注入，同时在 config、registry、测试和文档层面完整接入。

**Tech Stack:** Python 3.11, openai SDK, dataclasses, Ruff, MyPy, pytest

---

### Task 1: 补配置层与校验的失败测试

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`

**Step 1: Write the failing test**

在 `tests/test_config.py` 增加：
```python
def test_build_config_supports_openrouter_provider() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "openrouter",
                "openrouter": {
                    "api_key": "test-key",
                    "model": "openai/gpt-4o-mini",
                    "base_url": "https://openrouter.ai/api/v1",
                    "http_referer": "https://example.com",
                    "x_title": "OpenBiliClaw",
                },
            }
        }
    )

    assert config.llm.default_provider == "openrouter"
    assert config.llm.openrouter.api_key == "test-key"
    assert config.llm.openrouter.model == "openai/gpt-4o-mini"
```

再补：
```python
def test_validate_runtime_config_requires_openrouter_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openrouter",
            openrouter=LLMProviderConfig(api_key="", model="openai/gpt-4o-mini"),
        )
    )

    with pytest.raises(ConfigError, match="llm.openrouter.api_key"):
        validate_runtime_config(config)
```

**Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_config.py -q
```
Expected: FAIL because `openrouter` is not defined on `LLMConfig` and/or validation does not recognize it.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/config.py`：
- 给 `LLMProviderConfig` 增加 `http_referer: str = ""` 和 `x_title: str = ""`
- 给 `LLMConfig` 新增 `openrouter: LLMProviderConfig`
- 在配置加载逻辑中解析 `llm.openrouter`
- 在运行时校验逻辑中允许 `default_provider = "openrouter"` 并要求 `api_key`

在 `config.example.toml` 新增：
```toml
[llm.openrouter]
api_key = ""
model = "openai/gpt-4o-mini"
base_url = "https://openrouter.ai/api/v1"
http_referer = ""
x_title = "OpenBiliClaw"
```

**Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_config.py -q
```
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_config.py src/openbiliclaw/config.py config.example.toml
git commit -m "feat: add openrouter config support"
```

### Task 2: 补 provider 与 registry 的失败测试

**Files:**
- Modify: `tests/test_llm_providers.py`
- Modify: `tests/test_llm_registry.py`
- Create: `src/openbiliclaw/llm/openrouter_provider.py`
- Modify: `src/openbiliclaw/llm/openai_provider.py`
- Modify: `src/openbiliclaw/llm/registry.py`
- Modify: `src/openbiliclaw/llm/__init__.py`

**Step 1: Write the failing test**

在 `tests/test_llm_providers.py` 增加：
```python
def test_openrouter_provider_uses_default_base_url_and_headers() -> None:
    provider = OpenRouterProvider(
        api_key="test-key",
        model="openai/gpt-4o-mini",
        http_referer="https://example.com",
        x_title="OpenBiliClaw",
    )

    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider._extra_headers() == {
        "HTTP-Referer": "https://example.com",
        "X-Title": "OpenBiliClaw",
    }
```

在 `tests/test_llm_registry.py` 增加：
```python
def test_build_llm_registry_registers_openrouter() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openrouter",
            openrouter=LLMProviderConfig(
                api_key="test-key",
                model="openai/gpt-4o-mini",
                base_url="https://openrouter.ai/api/v1",
            ),
        )
    )

    registry = build_llm_registry(config)
    assert "openrouter" in registry.providers
    assert registry.default_provider == "openrouter"
```

**Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_llm_providers.py tests/test_llm_registry.py -q
```
Expected: FAIL because `OpenRouterProvider` does not exist and registry does not register it.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/llm/openai_provider.py`：
- 新增 `_extra_headers(self) -> dict[str, str]`，默认返回 `{}`
- 在实际请求调用中 merge 这些 headers

新增 `src/openbiliclaw/llm/openrouter_provider.py`：
```python
class OpenRouterProvider(OpenAIProvider):
    def __init__(..., http_referer: str = "", x_title: str = "") -> None:
        ...

    def _extra_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        ...
        return headers
```

在 `src/openbiliclaw/llm/registry.py`：
- 导入 `OpenRouterProvider`
- 新增构造函数 `_build_openrouter_provider(...)`
- 在注册逻辑中加入 `openrouter`

在 `src/openbiliclaw/llm/__init__.py` 导出 `OpenRouterProvider`

**Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_llm_providers.py tests/test_llm_registry.py -q
```
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_llm_providers.py tests/test_llm_registry.py src/openbiliclaw/llm/openai_provider.py src/openbiliclaw/llm/openrouter_provider.py src/openbiliclaw/llm/registry.py src/openbiliclaw/llm/__init__.py
git commit -m "feat: add openrouter llm provider"
```

### Task 3: 更新文档并全量验证

**Files:**
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

在 `docs/modules/llm.md`：
- 更新已实现功能表格，加入 OpenRouter
- 更新示例导入与 registry 示例
- 更新 provider 列表与配置说明

在 `docs/modules/config.md`：
- 补 `[llm.openrouter]` 配置段说明
- 补 `http_referer` 和 `x_title` 的用途

在 `docs/changelog.md`：
- 追加 OpenRouter provider 支持记录

**Step 2: Run formatting, type checks, and tests**

Run:
```bash
ruff check src/ tests/
mypy src/
pytest -q
```
Expected: all pass.

**Step 3: Commit**

```bash
git add docs/modules/llm.md docs/modules/config.md docs/changelog.md
git commit -m "docs: document openrouter provider"
```
