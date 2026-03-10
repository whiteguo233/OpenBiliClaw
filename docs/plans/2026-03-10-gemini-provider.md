# Gemini Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 OpenBiliClaw 新增按 Gemini 官方 quickstart 接入的 `gemini` LLM provider。

**Architecture:** 新增独立的 `GeminiProvider`，底层使用官方 `google-genai` SDK 的 Gemini Developer API 路径。上层接口保持不变，provider 内部把现有 `messages` 适配成单文本 prompt，并在 config、registry、测试和文档层面完整接入。

**Tech Stack:** Python 3.11+, google-genai SDK, dataclasses, Ruff, MyPy, pytest

---

### Task 1: 补配置层与环境变量回退的失败测试

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`

**Step 1: Write the failing test**

在 `tests/test_config.py` 增加：

```python
def test_build_config_supports_gemini_provider() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "gemini",
                "gemini": {
                    "api_key": "test-key",
                    "model": "gemini-2.5-flash",
                },
            }
        }
    )

    assert config.llm.default_provider == "gemini"
    assert config.llm.gemini.api_key == "test-key"
    assert config.llm.gemini.model == "gemini-2.5-flash"
```

再补：

```python
def test_validate_runtime_config_allows_gemini_env_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="", model="gemini-2.5-flash"),
        )
    )

    validate_runtime_config(config)
```

以及：

```python
def test_validate_runtime_config_requires_gemini_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="", model="gemini-2.5-flash"),
        )
    )

    with pytest.raises(ConfigError, match="llm.gemini.api_key"):
        validate_runtime_config(config)
```

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Expected: FAIL because `gemini` is not defined on `LLMConfig` and validation does not recognize Gemini env fallback.

**Step 3: Write minimal implementation**

- 给 `LLMConfig` 新增 `gemini: LLMProviderConfig`
- 解析 `llm.gemini`
- 允许 `default_provider = "gemini"`
- 校验时支持回退到 `GOOGLE_API_KEY` / `GEMINI_API_KEY`
- 在 `config.example.toml` 新增 `[llm.gemini]`

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Expected: PASS.

### Task 2: 补 provider 与 registry 的失败测试

**Files:**
- Modify: `tests/test_llm_providers.py`
- Modify: `tests/test_llm_registry.py`
- Create: `src/openbiliclaw/llm/gemini_provider.py`
- Modify: `src/openbiliclaw/llm/registry.py`
- Modify: `src/openbiliclaw/llm/__init__.py`
- Modify: `pyproject.toml`

**Step 1: Write the failing test**

在 `tests/test_llm_providers.py` 增加：

```python
def test_gemini_provider_defaults() -> None:
    provider = GeminiProvider(api_key="test-key")
    assert provider.name == "gemini"
```

以及异步测试：

```python
@pytest.mark.asyncio
async def test_gemini_provider_normalizes_response() -> None:
    ...
```

覆盖：
- usage 标准化
- `json_mode=True` 时写入 `response_mime_type="application/json"`
- message 渲染为单文本 prompt

在 `tests/test_llm_registry.py` 增加：

```python
def test_build_llm_registry_registers_gemini() -> None:
    ...
```

以及环境变量注册测试：

```python
def test_build_llm_registry_registers_gemini_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/test_llm_providers.py tests/test_llm_registry.py -q
```

Expected: FAIL because `GeminiProvider` and registry support do not exist.

**Step 3: Write minimal implementation**

- 在 `pyproject.toml` 增加 `google-genai>=1.66`
- 新增 `src/openbiliclaw/llm/gemini_provider.py`
- 在 `registry.py` 注册 `gemini`
- 在 `__init__.py` 导出 `GeminiProvider`

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/test_llm_providers.py tests/test_llm_registry.py -q
```

Expected: PASS.

### Task 3: 更新文档并做收尾验证

**Files:**
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`

**Step 1: Update docs**

- 在 `docs/modules/llm.md` 中加入 Gemini provider、配置和示例
- 在 `docs/modules/config.md` 中加入 `[llm.gemini]` 和环境变量说明
- 在 `docs/changelog.md` 中加入 Gemini provider 支持记录
- 在 `docs/v0.1-todolist.md` 中标记对应 LLM provider 支持进度

**Step 2: Run validation**

Run:
```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_llm_providers.py tests/test_llm_registry.py -q
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m mypy src/
```

Expected: all pass.
