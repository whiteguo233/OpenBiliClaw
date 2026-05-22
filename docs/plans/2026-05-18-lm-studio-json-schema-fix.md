# LM Studio JSON Schema Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix LM Studio OpenAI-compatible structured calls so `complete_structured_task()` no longer fails with `response_format.type must be json_schema or text`.

**Architecture:** Keep `json_object` as the default JSON mode for existing OpenAI-compatible providers, but add a narrow LM Studio path in `OpenAIProvider` that sends a permissive `json_schema` response format. For other compatible services, detect the exact `response_format.type` rejection and retry once with the same `json_schema` shape. The registry fallback chain stays unchanged; the reported misleading Ollama tail disappears because the LM Studio request no longer fails.

**Tech Stack:** Python 3.12+/3.14-compatible provider code, OpenAI Python SDK Chat Completions, pytest/pytest-asyncio, Ruff, MyPy.

---

### Task 1: Reproduce LM Studio Response-Format Request Shape

**Files:**
- Test: `tests/test_llm_providers.py`

**Step 1: Write the failing test**

Add a test near the existing `OpenAIProvider` tests:

```python
@pytest.mark.asyncio
async def test_openai_provider_uses_json_schema_for_lm_studio_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="lm-studio",
        model="qwen3.5-9b",
        base_url="http://127.0.0.1:1234/v1",
        provider_name="openai_compatible",
    )
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response('{"ok": true}')

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    response_format = captured["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert "json_schema" in response_format
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_llm_providers.py -q -k "lm_studio_json_mode"
```

Expected: FAIL because current code sends `{"type": "json_object"}`.

**Step 3: Commit**

Do not commit yet. Continue to Task 2 so the fallback behavior is covered before implementation.

---

### Task 2: Reproduce Generic Compatibility Fallback

**Files:**
- Test: `tests/test_llm_providers.py`

**Step 1: Write the failing test**

Add a second test proving non-LM-Studio compatible services can recover if they reject `json_object`:

```python
@pytest.mark.asyncio
async def test_openai_provider_retries_json_mode_with_schema_when_json_object_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        base_url="http://localhost:8000/v1",
        provider_name="openai_compatible",
    )
    response_formats: list[dict[str, object]] = []

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        response_format = kwargs["response_format"]
        assert isinstance(response_format, dict)
        response_formats.append(response_format)
        if response_format["type"] == "json_object":
            raise LLMProviderError(
                "openai_compatible request failed: HTTP 400: "
                '"response_format.type" must be "json_schema" or "text"'
            )
        return _openai_response('{"ok": true}')

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    assert response.content == '{"ok": true}'
    assert [item["type"] for item in response_formats] == ["json_object", "json_schema"]
```

**Step 2: Run both tests to verify failure**

```bash
.venv/bin/pytest tests/test_llm_providers.py -q -k "lm_studio_json_mode or schema_when_json_object_rejected"
```

Expected: both tests fail for the expected reasons:

- LM Studio test sees `json_object`.
- fallback test raises `LLMProviderError` instead of retrying.

**Step 3: Commit**

Do not commit yet. Implement in Task 3 first so the commit contains red-green behavior.

---

### Task 3: Implement JSON Schema Response Format Selection

**Files:**
- Modify: `src/openbiliclaw/llm/openai_provider.py`

**Step 1: Add the generic schema helper**

Near the module logger, add:

```python
def _generic_json_schema_response_format() -> dict[str, Any]:
    """OpenAI structured-output shape for arbitrary JSON object tasks."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_response",
            "strict": False,
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        },
    }
```

Also import:

```python
from urllib.parse import urlparse
```

**Step 2: Add response-format selection helpers**

Inside `OpenAIProvider`:

```python
def _json_response_format(self) -> dict[str, Any]:
    if self._prefers_json_schema_response_format():
        return _generic_json_schema_response_format()
    return {"type": "json_object"}

def _prefers_json_schema_response_format(self) -> bool:
    """Return True for backends known to reject OpenAI JSON-object mode."""
    raw_base_url = self.base_url.strip()
    if not raw_base_url:
        return False
    normalized = raw_base_url.lower()
    if "lmstudio" in normalized or "lm-studio" in normalized:
        return True
    parsed_url = raw_base_url if "://" in raw_base_url else f"http://{raw_base_url}"
    parsed = urlparse(parsed_url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"} and port == 1234

@staticmethod
def _uses_json_object(response_format: object) -> bool:
    return isinstance(response_format, dict) and response_format.get("type") == "json_object"

@staticmethod
def _json_object_response_format_rejected(exc: LLMProviderError) -> bool:
    message = str(exc).lower()
    return "response_format.type" in message and "json_schema" in message and "text" in message
```

**Step 3: Use the helper in `complete()`**

Replace:

```python
if json_mode:
    kwargs["response_format"] = {"type": "json_object"}
```

with:

```python
if json_mode:
    kwargs["response_format"] = self._json_response_format()
```

**Step 4: Add the fallback retry**

Wrap the `_request_with_retry` call:

```python
try:
    response = await self._request_with_retry(**kwargs)
except LLMProviderError as exc:
    if (
        json_mode
        and self._uses_json_object(kwargs.get("response_format"))
        and self._json_object_response_format_rejected(exc)
    ):
        logger.info(
            "%s rejected json_object response_format; retrying with json_schema",
            self._provider_name,
        )
        kwargs["response_format"] = _generic_json_schema_response_format()
        response = await self._request_with_retry(**kwargs)
    else:
        raise
```

Do not change usage normalization, empty-content handling, retries, or embedding code.

**Step 5: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_llm_providers.py -q -k "lm_studio_json_mode or schema_when_json_object_rejected"
```

Expected: 2 tests pass.

**Step 6: Run provider suite**

```bash
.venv/bin/pytest tests/test_llm_providers.py -q
```

Expected: all provider tests pass.

**Step 7: Commit**

```bash
git add src/openbiliclaw/llm/openai_provider.py tests/test_llm_providers.py
git commit -m "fix(llm): support LM Studio json_schema response format"
```

---

### Task 4: Update LLM Documentation and Changelog

**Files:**
- Modify: `docs/modules/llm.md`
- Modify: `docs/changelog.md`

**Step 1: Update module docs**

In `docs/modules/llm.md`:

- Add a row to the implemented-features table:

```markdown
| v0.3.75 LM Studio JSON mode 兼容 | ✅ | `OpenAIProvider` 的 `json_mode=True` 默认仍使用 OpenAI `json_object`；但当 `base_url` 指向 LM Studio 默认本地端口 `localhost/127.0.0.1:1234` 或兼容服务返回 `response_format.type` 只允许 `json_schema/text` 时，会改用通用 `json_schema` response_format。修复 LM Studio 0.4.x 在偏好分析阶段 400，并避免主调用失败后才 fallback 到模板 Ollama model 的误导性错误 |
```

- In the public API snippet, add a short `json_mode=True` example and note LM Studio auto-switches to `json_schema`.

**Step 2: Update changelog**

In the top current-version block of `docs/changelog.md`, add:

```markdown
- 修复 [#12](https://github.com/whiteguo233/OpenBiliClaw/issues/12)：LM Studio 的 OpenAI-compatible `/v1/chat/completions` 不接受 `response_format={"type":"json_object"}`，`OpenAIProvider` 现在对 LM Studio 默认本地端口直接使用 `json_schema`，并在其它兼容服务明确拒绝 `json_object` 时自动用通用 JSON schema 重试，避免初始化偏好分析阶段 400 后再误导性 fallback 到模板里的 Ollama `qwen2.5:7b`。
```

**Step 3: Commit**

```bash
git add docs/modules/llm.md docs/changelog.md
git commit -m "docs(llm): document LM Studio json_schema compatibility"
```

---

### Task 5: Final Verification

**Files:**
- Verify: all changed files

**Step 1: Format Python files**

```bash
.venv/bin/ruff format src/openbiliclaw/llm/openai_provider.py tests/test_llm_providers.py
```

Expected: files formatted or unchanged.

**Step 2: Run lint**

```bash
.venv/bin/ruff check src/ tests/
```

Expected: `All checks passed!`

**Step 3: Run type check**

```bash
.venv/bin/mypy src/
```

Expected: `Success: no issues found in 127 source files`

**Step 4: Run full test suite**

```bash
.venv/bin/pytest
```

Expected: full suite passes. Existing skipped browser/e2e tests and deprecation warnings are acceptable if the command exits 0.

**Step 5: Inspect diff**

```bash
git diff --stat
git diff -- src/openbiliclaw/llm/openai_provider.py tests/test_llm_providers.py docs/modules/llm.md docs/changelog.md
```

Expected:

- Only `OpenAIProvider`, provider tests, LLM docs, and changelog are modified.
- No `config.toml`, cookies, API keys, generated build artifacts, or unrelated docs are included.

**Step 6: Commit verification cleanup if needed**

If formatting changed files after earlier commits:

```bash
git add src/openbiliclaw/llm/openai_provider.py tests/test_llm_providers.py
git commit -m "style(llm): format LM Studio response-format fix"
```

Otherwise no additional commit is needed.
