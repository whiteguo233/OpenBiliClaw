# Config Service Probe Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add "test LLM" and "test Embedding" buttons to PCWeb and extension settings, backed by a no-write backend probe API.

**Architecture:** Add `POST /api/config/probe-service`, which builds a temporary config from current saved config plus the submitted form payload, then runs either a tiny chat completion or `EmbeddingService.probe()`. Both frontends collect their existing settings form state and call the endpoint, rendering inline success/failure messages without saving or hot-reloading config.

**Tech Stack:** FastAPI, Pydantic, OpenBiliClaw config dataclasses, LLM registry/provider interfaces, vanilla JS desktop Web UI, vanilla JS browser-extension popup, pytest, Node `node --test`.

---

## Current Worktree Note

The repository currently has unrelated modified files in API, desktop Web,
extension, docs, and tests. Execute this plan in the current tree carefully, or
first isolate unrelated work in a separate branch/worktree. Never revert
existing changes that are not part of this feature.

### Task 1: Add Backend Probe Models

**Files:**

- Modify: `src/openbiliclaw/api/models.py`

**Step 1: Write the failing model/API import test**

Add focused assertions to `tests/test_api_config_guards.py` or a new
`tests/test_api_config_probe.py` that imports:

```python
from openbiliclaw.api.models import ConfigServiceProbeIn, ConfigServiceProbeResponse
```

Expected shape:

```python
def test_config_probe_models_accept_llm_request() -> None:
    payload = ConfigServiceProbeIn(
        kind="llm",
        config={"llm": {"default_provider": "openai"}},
    )
    assert payload.kind == "llm"
    assert payload.config["llm"]["default_provider"] == "openai"

def test_config_probe_response_defaults_to_inline_error_shape() -> None:
    result = ConfigServiceProbeResponse(ok=False, kind="embedding")
    assert result.provider == ""
    assert result.model == ""
    assert result.message == ""
    assert result.error == ""
    assert result.latency_ms == 0
```

**Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_api_config_probe.py::test_config_probe_models_accept_llm_request tests/test_api_config_probe.py::test_config_probe_response_defaults_to_inline_error_shape -q
```

Expected: FAIL because the model classes do not exist.

**Step 3: Implement minimal models**

In `src/openbiliclaw/api/models.py`, add near the configuration API models:

```python
class ConfigServiceProbeIn(BaseModel):
    """No-write request to probe the submitted LLM or embedding config."""

    kind: Literal["llm", "embedding"]
    config: dict[str, object] = Field(default_factory=dict)


class ConfigServiceProbeResponse(BaseModel):
    """Result of a user-triggered provider connectivity probe."""

    ok: bool
    kind: Literal["llm", "embedding"]
    provider: str = ""
    model: str = ""
    message: str = ""
    error: str = ""
    latency_ms: int = 0
```

**Step 4: Re-run the tests**

Run the command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py tests/test_api_config_probe.py
git commit -m "feat: add config probe api models"
```

Skip the commit if this is being executed inside a dirty shared worktree and
the user has not approved commits.

### Task 2: Extract Temporary LLM Config Application

**Files:**

- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_config_probe.py`

**Step 1: Write failing tests for no-write temporary config behavior**

Add tests that save a real config, call the new endpoint later, and assert the
config file is unchanged. The endpoint does not exist yet, so these should fail
with 404.

```python
def test_probe_llm_applies_unsaved_provider_payload_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    cfg = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-old", model="gpt-old"),
            deepseek=LLMProviderConfig(api_key="sk-new", model="deepseek-chat"),
        )
    )
    save_config(cfg, config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

    calls: list[tuple[str, str | None]] = []

    class FakeRegistry:
        available_providers = ["openai", "deepseek"]
        default_provider = "deepseek"

        def is_chat_capable(self, name: str) -> bool:
            return name == "deepseek"

        async def complete_provider(self, provider_name, messages, **kwargs):
            calls.append((provider_name, kwargs.get("model")))
            return LLMResponse(content="OK", provider=provider_name, model=kwargs.get("model") or "")

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_llm_registry",
        lambda probe_cfg: FakeRegistry(),
    )

    before = config_path.read_bytes()
    client = TestClient(create_app(memory_manager=object(), database=object(), soul_engine=object()))
    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "llm",
            "config": {
                "llm": {
                    "default_provider": "deepseek",
                    "deepseek": {"api_key": "sk-new", "model": "deepseek-chat"},
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["provider"] == "deepseek"
    assert calls == [("deepseek", "deepseek-chat")]
    assert config_path.read_bytes() == before
    assert not (tmp_path / "config.toml.bak").exists()
```

**Step 2: Run the failing test**

Run:

```bash
pytest tests/test_api_config_probe.py::test_probe_llm_applies_unsaved_provider_payload_without_writing -q
```

Expected: FAIL with 404.

**Step 3: Add a shared helper for applying LLM payloads**

In `src/openbiliclaw/api/app.py`, extract the LLM portion of `update_config`
into a helper inside `create_app`, before the config routes:

```python
def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _apply_llm_update(cfg: Any, llm_data: object) -> None:
    if not isinstance(llm_data, dict):
        return
    # Move the existing "Apply LLM updates" logic from update_config here:
    # default_provider, concurrency, timeout, fallback fields, all provider
    # blocks, embedding, and module overrides.
    # Preserve the existing guards:
    # - masked api_key values are skipped;
    # - empty api_key/model/base_url values do not clobber existing non-empty values;
    # - embedding output_dimensionality validation still raises HTTPException(400).
```

Then replace the old in-route LLM block with:

```python
if "llm" in update:
    _apply_llm_update(cfg, update["llm"])
```

This keeps `PUT /api/config` and `POST /api/config/probe-service` aligned.

**Step 4: Re-run existing config guard tests**

Run:

```bash
pytest tests/test_api_config_guards.py tests/test_api_config_transactional.py -q
```

Expected: PASS. If a guard test fails, the extraction changed save semantics and
must be fixed before continuing.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_config_probe.py
git commit -m "refactor: share llm config update logic"
```

Skip the commit if commits are not approved.

### Task 3: Implement LLM Probe Endpoint

**Files:**

- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/api/models.py` imports in `app.py`
- Test: `tests/test_api_config_probe.py`

**Step 1: Add failing LLM error tests**

Add:

```python
def test_probe_llm_returns_inline_failure_for_unregistered_provider(...):
    # Fake registry has no chat-capable "deepseek".
    # Expected: status 200, ok false, error mentions provider.

def test_probe_llm_returns_inline_failure_when_provider_raises(...):
    # Fake complete_provider raises LLMProviderError("bad key").
    # Expected: status 200, ok false, error contains "bad key".
```

**Step 2: Run failing LLM probe tests**

Run:

```bash
pytest tests/test_api_config_probe.py::test_probe_llm_applies_unsaved_provider_payload_without_writing tests/test_api_config_probe.py::test_probe_llm_returns_inline_failure_for_unregistered_provider tests/test_api_config_probe.py::test_probe_llm_returns_inline_failure_when_provider_raises -q
```

Expected: FAIL until the endpoint exists.

**Step 3: Implement route and helper**

In `app.py` imports, include:

```python
ConfigServiceProbeIn,
ConfigServiceProbeResponse,
```

Add helper:

```python
async def _probe_llm_config(cfg: Any) -> ConfigServiceProbeResponse:
    from openbiliclaw.llm.registry import build_llm_registry

    started = time.perf_counter()
    provider = str(getattr(cfg.llm, "default_provider", "") or "").strip().lower()
    try:
        registry = build_llm_registry(cfg)
        provider = provider or registry.default_provider
        provider_cfg = getattr(cfg.llm, provider, None)
        model = str(getattr(provider_cfg, "model", "") or "").strip()
        if not registry.is_chat_capable(provider):
            return ConfigServiceProbeResponse(
                ok=False,
                kind="llm",
                provider=provider,
                model=model,
                error=f"LLM provider {provider!r} is not registered or not chat-capable.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        timeout_s = min(max(float(getattr(cfg.llm, "timeout", 300) or 300), 10.0), 30.0)
        response = await asyncio.wait_for(
            registry.complete_provider(
                provider,
                [
                    {"role": "system", "content": "Reply with only OK."},
                    {"role": "user", "content": "OpenBiliClaw connectivity probe."},
                ],
                temperature=0,
                max_tokens=8,
                reasoning_effort="",
                model=model or None,
            ),
            timeout=timeout_s,
        )
        ok = bool(str(getattr(response, "content", "") or "").strip())
        return ConfigServiceProbeResponse(
            ok=ok,
            kind="llm",
            provider=provider,
            model=str(getattr(response, "model", "") or model),
            message="LLM provider is available." if ok else "",
            error="" if ok else "LLM provider returned an empty response.",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        return ConfigServiceProbeResponse(
            ok=False,
            kind="llm",
            provider=provider,
            error=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
```

Add route near the config routes:

```python
@app.post("/api/config/probe-service", response_model=ConfigServiceProbeResponse)
async def probe_config_service(payload: ConfigServiceProbeIn) -> ConfigServiceProbeResponse:
    from copy import deepcopy
    from openbiliclaw.config import load_config

    cfg = deepcopy(load_config())
    update = payload.config or {}
    if isinstance(update.get("llm"), dict):
        _apply_llm_update(cfg, update["llm"])
    if payload.kind == "llm":
        return await _probe_llm_config(cfg)
    return await _probe_embedding_config(cfg)
```

`_probe_embedding_config` is added in Task 4.

**Step 4: Re-run LLM probe tests**

Run the command from Step 2.

Expected: PASS for LLM tests.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_api_config_probe.py
git commit -m "feat: add llm config probe endpoint"
```

Skip the commit if commits are not approved.

### Task 4: Implement Embedding Probe Endpoint Behavior

**Files:**

- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_config_probe.py`

**Step 1: Add failing embedding tests**

Add:

```python
def test_probe_embedding_returns_success_when_service_probe_passes(...):
    class FakeEmbeddingService:
        async def probe(self) -> bool:
            return True

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_embedding_service",
        lambda cfg, registry: FakeEmbeddingService(),
    )

    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "embedding",
            "config": {
                "llm": {
                    "embedding": {
                        "provider": "openai",
                        "api_key": "sk-embedding",
                        "model": "text-embedding-3-small",
                    }
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True

def test_probe_embedding_returns_failure_when_provider_is_empty(...):
    response = client.post(
        "/api/config/probe-service",
        json={"kind": "embedding", "config": {"llm": {"embedding": {"provider": ""}}}},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "not configured" in response.json()["error"].lower()

def test_probe_embedding_returns_failure_when_service_probe_fails(...):
    class FakeEmbeddingService:
        async def probe(self) -> bool:
            return False
    # Expected: ok false, no config write.
```

**Step 2: Run failing embedding tests**

Run:

```bash
pytest tests/test_api_config_probe.py::test_probe_embedding_returns_success_when_service_probe_passes tests/test_api_config_probe.py::test_probe_embedding_returns_failure_when_provider_is_empty tests/test_api_config_probe.py::test_probe_embedding_returns_failure_when_service_probe_fails -q
```

Expected: FAIL until `_probe_embedding_config` exists.

**Step 3: Implement `_probe_embedding_config`**

Add:

```python
async def _probe_embedding_config(cfg: Any) -> ConfigServiceProbeResponse:
    from openbiliclaw.llm.base import LLMRegistry
    from openbiliclaw.llm.registry import build_embedding_service

    started = time.perf_counter()
    emb_cfg = getattr(getattr(cfg, "llm", None), "embedding", None)
    provider = str(getattr(emb_cfg, "provider", "") or "").strip().lower()
    model = str(getattr(emb_cfg, "model", "") or "").strip()
    if not provider:
        return ConfigServiceProbeResponse(
            ok=False,
            kind="embedding",
            provider="",
            model=model,
            error="Embedding provider is not configured.",
        )
    try:
        service = build_embedding_service(cfg, LLMRegistry())
        if service is None:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="embedding",
                provider=provider,
                model=model,
                error="Embedding service could not be built from the submitted config.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        timeout_s = 15.0
        ok = bool(await asyncio.wait_for(service.probe(), timeout=timeout_s))
        return ConfigServiceProbeResponse(
            ok=ok,
            kind="embedding",
            provider=provider,
            model=model,
            message="Embedding provider is available." if ok else "",
            error="" if ok else "Embedding provider returned no vector.",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        return ConfigServiceProbeResponse(
            ok=False,
            kind="embedding",
            provider=provider,
            model=model,
            error=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
```

**Step 4: Re-run backend probe tests**

Run:

```bash
pytest tests/test_api_config_probe.py tests/test_api_config_guards.py tests/test_api_config_transactional.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_config_probe.py
git commit -m "feat: add embedding config probe"
```

Skip the commit if commits are not approved.

### Task 5: Add Extension API Helper

**Files:**

- Modify: `extension/popup/popup-api.js`
- Test: `extension/tests/popup-api.test.ts`

**Step 1: Write failing API helper test**

Add to `extension/tests/popup-api.test.ts`:

```ts
test("probeConfigService posts no-write config probe payload", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { ok: true, kind: "llm", provider: "openai", message: "LLM provider is available." };
      },
    } as Response;
  };

  const result = await probeConfigService("llm", {
    llm: { default_provider: "openai", openai: { api_key: "sk-test" } },
  });

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config/probe-service");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    kind: "llm",
    config: { llm: { default_provider: "openai", openai: { api_key: "sk-test" } } },
  });
  assert.equal(result.ok, true);
});
```

Also add `probeConfigService` to the import list at the top of the test.

**Step 2: Run the failing test**

Run from `extension/`:

```bash
npm test -- tests/popup-api.test.ts
```

Expected: FAIL because `probeConfigService` is not exported.

**Step 3: Implement helper**

In `extension/popup/popup-api.js`:

```js
export async function probeConfigService(kind, config) {
  return requestJson("/config/probe-service", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    timeoutMs: 35_000,
    body: JSON.stringify({ kind, config }),
  });
}
```

**Step 4: Re-run test**

Run the command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add extension/popup/popup-api.js extension/tests/popup-api.test.ts
git commit -m "feat: add extension config probe api"
```

Skip the commit if commits are not approved.

### Task 6: Wire Extension Settings Buttons

**Files:**

- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-settings.test.ts`

**Step 1: Write failing static wiring test**

Add:

```ts
test("settings page exposes and wires LLM and embedding probe buttons", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="cfgProbeLlm"/);
  assert.match(popupHtml, /id="cfgProbeEmbedding"/);
  assert.match(popupHtml, /id="cfgProbeLlmStatus"/);
  assert.match(popupHtml, /id="cfgProbeEmbeddingStatus"/);
  assert.match(popupJs, /probeConfigService\("llm", collectForm\(\)\)/);
  assert.match(popupJs, /probeConfigService\("embedding", collectForm\(\)\)/);
  assert.match(popupJs, /function renderProbeResult/);
});
```

**Step 2: Run failing settings test**

Run from `extension/`:

```bash
npm test -- tests/popup-settings.test.ts
```

Expected: FAIL because the buttons/status elements are missing.

**Step 3: Add HTML controls**

In `extension/popup/popup.html`, add a compact action row near the LLM section
hint:

```html
<div class="settings-probe-row">
  <button id="cfgProbeLlm" class="action-button action-secondary" type="button">测试 LLM</button>
  <span id="cfgProbeLlmStatus" class="settings-probe-status" aria-live="polite"></span>
</div>
```

Add another row near the embedding hint:

```html
<div class="settings-probe-row">
  <button id="cfgProbeEmbedding" class="action-button action-secondary" type="button">测试 Embedding</button>
  <span id="cfgProbeEmbeddingStatus" class="settings-probe-status" aria-live="polite"></span>
</div>
```

Add CSS in the existing popup style block:

```css
.settings-probe-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 8px 0 10px;
}
.settings-probe-status {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
}
.settings-probe-status[data-tone="success"] { color: #2ecc71; }
.settings-probe-status[data-tone="error"] { color: #e74c3c; }
```

Use existing button classes if these selectors conflict with current popup
styling.

**Step 4: Wire JS**

Import `probeConfigService` in `extension/popup/popup.js`.

Inside `initSettingsOverlay`, add:

```js
function renderProbeResult(statusEl, result) {
  if (!statusEl) return;
  const ok = Boolean(result?.ok);
  statusEl.dataset.tone = ok ? "success" : "error";
  statusEl.textContent = ok
    ? (result.message || "服务可用。")
    : (result.error || result.message || "服务不可用。");
}

async function runConfigProbe(kind, button, statusEl) {
  if (!(button instanceof HTMLButtonElement)) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "测试中…";
  if (statusEl) {
    statusEl.dataset.tone = "";
    statusEl.textContent = "正在发起真实请求…";
  }
  try {
    const result = await probeConfigService(kind, collectForm());
    renderProbeResult(statusEl, result);
  } catch (err) {
    if (statusEl) {
      statusEl.dataset.tone = "error";
      statusEl.textContent = err?.message || "后端不可达。";
    }
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.getElementById("cfgProbeLlm")?.addEventListener("click", () => {
  void runConfigProbe("llm", document.getElementById("cfgProbeLlm"), document.getElementById("cfgProbeLlmStatus"));
});
document.getElementById("cfgProbeEmbedding")?.addEventListener("click", () => {
  void runConfigProbe("embedding", document.getElementById("cfgProbeEmbedding"), document.getElementById("cfgProbeEmbeddingStatus"));
});
```

**Step 5: Re-run extension tests**

Run:

```bash
cd extension && npm test -- tests/popup-settings.test.ts tests/popup-api.test.ts
```

Expected: PASS.

**Step 6: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/tests/popup-settings.test.ts
git commit -m "feat: add extension provider probe buttons"
```

Skip the commit if commits are not approved.

### Task 7: Wire PCWeb Settings Buttons

**Files:**

- Modify: `src/openbiliclaw/web/desktop/index.html`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css`
- Test: `tests/test_desktop_web_pool_status.py` or new `tests/test_desktop_web_settings.py`

**Step 1: Write failing desktop static test**

Create `tests/test_desktop_web_settings.py`:

```python
from pathlib import Path


def test_desktop_settings_exposes_and_wires_provider_probe_buttons() -> None:
    html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert 'id="probeLlm"' in html
    assert 'id="probeEmbedding"' in html
    assert 'id="probeLlmStatus"' in html
    assert 'id="probeEmbeddingStatus"' in html
    assert 'probeConfigService("llm", buildConfigUpdate())' in js
    assert 'probeConfigService("embedding", buildConfigUpdate())' in js
    assert ".settings-probe-row" in css
```

**Step 2: Run failing desktop test**

Run:

```bash
pytest tests/test_desktop_web_settings.py::test_desktop_settings_exposes_and_wires_provider_probe_buttons -q
```

Expected: FAIL because controls are missing.

**Step 3: Add PCWeb HTML controls**

In `src/openbiliclaw/web/desktop/index.html`, add below the LLM default
subpanel:

```html
<div class="settings-probe-row">
  <button class="small-btn" id="probeLlm" type="button">测试 LLM</button>
  <span class="settings-probe-status" id="probeLlmStatus" aria-live="polite"></span>
</div>
```

Add below the embedding default subpanel:

```html
<div class="settings-probe-row">
  <button class="small-btn" id="probeEmbedding" type="button">测试 Embedding</button>
  <span class="settings-probe-status" id="probeEmbeddingStatus" aria-live="polite"></span>
</div>
```

**Step 4: Add PCWeb CSS**

In `src/openbiliclaw/web/desktop/assets/css/app.css`, near settings styles:

```css
.settings-probe-row {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-height: 36px;
}
.settings-probe-status {
  color: var(--muted);
  font-size: var(--text-sm);
  line-height: 1.45;
}
.settings-probe-status[data-tone="success"] { color: var(--success); }
.settings-probe-status[data-tone="error"] { color: var(--danger); }
```

If `--success` / `--danger` are not defined, use existing semantic colors from
the file.

**Step 5: Add PCWeb JS helper and bindings**

In `src/openbiliclaw/web/desktop/assets/js/app.js`, add:

```js
async function probeConfigService(kind, config) {
  return requestJson("/config/probe-service", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, config })
  });
}
```

Add render/run helpers near settings helpers:

```js
function renderProbeResult(statusEl, result) {
  if (!statusEl) return;
  const ok = Boolean(result?.ok);
  statusEl.dataset.tone = ok ? "success" : "error";
  statusEl.textContent = ok
    ? (result.message || "服务可用。")
    : (result.error || result.message || "服务不可用。");
}

async function runConfigProbe(kind, buttonId, statusId) {
  const button = document.getElementById(buttonId);
  const statusEl = document.getElementById(statusId);
  if (!button) return;
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "测试中…";
  if (statusEl) {
    statusEl.dataset.tone = "";
    statusEl.textContent = "正在发起真实请求…";
  }
  try {
    renderProbeResult(statusEl, await probeConfigService(kind, buildConfigUpdate()));
  } catch (err) {
    if (statusEl) {
      statusEl.dataset.tone = "error";
      statusEl.textContent = err?.message || "后端不可达。";
    }
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}
```

Bind after existing settings bindings:

```js
safeBind("#probeLlm", "click", () => void runConfigProbe("llm", "probeLlm", "probeLlmStatus"));
safeBind("#probeEmbedding", "click", () => void runConfigProbe("embedding", "probeEmbedding", "probeEmbeddingStatus"));
```

**Step 6: Re-run desktop static test**

Run:

```bash
pytest tests/test_desktop_web_settings.py -q
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/desktop/assets/css/app.css tests/test_desktop_web_settings.py
git commit -m "feat: add desktop provider probe buttons"
```

Skip the commit if commits are not approved.

### Task 8: Documentation Updates

**Files:**

- Modify: `docs/modules/config.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/llm.md`
- Modify: `docs/changelog.md`
- Review/update if architecture docs already describe config-setting data flow:
  `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`

**Step 1: Update module docs**

In `docs/modules/config.md`, add `POST /api/config/probe-service` to the public
API section with request/response examples and the no-write guarantee.

In `docs/modules/extension.md`, mention the settings page has LLM and embedding
test buttons that call the backend probe endpoint.

In `docs/modules/llm.md`, add a short note that availability probes are real
low-token/one-vector requests and are separate from cached `/api/health`
embedding readiness.

**Step 2: Update changelog**

Add a current-version bullet:

```markdown
- Added no-write LLM / Embedding probe buttons in PCWeb and extension settings so users can validate provider credentials before saving.
```

**Step 3: Architecture docs decision**

If `docs/architecture.md` or `docs/spec.md` already show settings/config data
flow, add a small note:

```text
Settings UI -> /api/config/probe-service -> temporary Config -> LLM registry / EmbeddingService
```

If README diagrams only show high-level module topology and no settings flow,
do not redraw them for this endpoint-only addition; mention that explicitly in
the PR summary.

**Step 4: Commit**

```bash
git add docs/modules/config.md docs/modules/extension.md docs/modules/llm.md docs/changelog.md docs/architecture.md docs/spec.md README.md README_EN.md
git commit -m "docs: document config service probes"
```

Only add architecture/README files that actually changed. Skip commit if
commits are not approved.

### Task 9: Verification

**Files:**

- No edits unless verification exposes failures.

**Step 1: Backend focused verification**

Run:

```bash
ruff check src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_api_config_probe.py
pytest tests/test_api_config_probe.py tests/test_api_config_guards.py tests/test_api_config_transactional.py -q
```

Expected: PASS.

**Step 2: Frontend focused verification**

Run:

```bash
pytest tests/test_desktop_web_settings.py -q
cd extension && npm test -- tests/popup-api.test.ts tests/popup-settings.test.ts
```

Expected: PASS.

**Step 3: Broader safety verification**

Run from repo root:

```bash
ruff check src/ tests/
mypy src/
pytest
cd extension && npm run typecheck && npm test
```

Expected: PASS. If full `pytest` is too slow for the current session, at least
run the focused suites above and report the unrun broader suites clearly.

**Step 4: Manual UI smoke**

Start the backend if not already running, then open PCWeb:

```bash
openbiliclaw start
```

Use the in-app browser or local browser at the backend Web URL. Verify:

- PCWeb settings shows `测试 LLM` and `测试 Embedding`;
- clicking each button disables only that button and shows an inline result;
- saving remains a separate action;
- extension popup settings shows the same two buttons and statuses.

**Step 5: Final status**

Report:

- changed files;
- exact tests run and results;
- whether docs/architecture/README diagrams changed or were intentionally left
  unchanged because module topology did not change;
- any provider probe that could not be manually tested due missing real keys.
