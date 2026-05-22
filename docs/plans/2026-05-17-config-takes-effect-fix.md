# Config Takes Effect Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Resolve the two independent bugs that surface as "前端配置修改没生效":
- **A.** `PUT /api/config` no longer blocks on the post-reload speculator tick; the popup save button always recovers within a bounded budget.
- **B.** `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]` provider/model overrides actually route at LLM call time, falling back silently to `default_provider` when the override is empty or unregistered.

**Architecture:** Detach `speculator.force_tick` via the existing `task_registry.track` pattern in `RuntimeContext.restart_background_tasks`; add a 60s `AbortController` budget to the popup's `requestJson` and surface an amber timeout toast. Add an optional `model` parameter to every chat-provider `complete()` signature; teach `LLMService` to resolve caller tags through built-in route buckets (`soul`, `discovery`, `recommendation`, `evaluation`) and dispatch routed calls via `LLMRegistry.complete_provider(name, model=...)` so chat-capability and rate-limit bookkeeping remain centralized. Wire `module_overrides` through every config-backed runtime builder (`RuntimeContext`, `SoulEngine`, CLI builders, OpenClaw bootstrap, and SocraticDialogue fallback where applicable).

**Tech Stack:** Python (dataclasses / FastAPI / pytest / asyncio) for backend; existing extension popup JavaScript plus TypeScript node tests (`node --test --experimental-strip-types`) for the extension.

---

### Task 1: Detach Post-Reload Speculator Tick

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Test: `tests/test_api_app.py` (extend existing `TestBackendAPI` class)

**Step 1: Write failing tests**

Add tests proving:
- `restart_background_tasks` returns within 1 wall-clock second when the bound `SoulEngine._speculator.force_tick` is monkeypatched to `await asyncio.sleep(60)`.
- After `restart_background_tasks` returns, `ctx.task_registry.stats().get("post_reload_speculate") == 1`; call `await ctx.task_registry.cancel_all()` in test cleanup.
- `restart_background_tasks` returns normally when `force_tick` raises `RuntimeError("boom")`; monkeypatch `ctx.task_registry.track` to capture the scheduled task, then assert the captured task finishes with no task exception because the detached helper swallows speculator failures.
- Existing behavior preserved: when `soul_engine` is None or `background_llm_work_allowed()` is False, no `post_reload_speculate*` task is scheduled.
- `PUT /api/config` integration test: with a fake SoulEngine whose `_speculator.force_tick` awaits an `asyncio.Event` the test never sets, `PUT /api/config` returns within 5s with `reloaded=True`.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -k "restart_tasks_detaches_speculator or put_config_does_not_block_on_speculator" -q
```

Expected: fail because `force_tick` is still awaited inline at `runtime_context.py:453-458`.

**Step 3: Implement detach**

In `src/openbiliclaw/api/runtime_context.py:434-460`:

- Replace the `await speculator.force_tick(...)` / `except TypeError` block with a small helper coroutine that does the same `force_tick` call (with the same `TypeError` back-compat shim) inside its own `try/except Exception: pass`.
- Schedule the helper via `self.task_registry.track("post_reload_speculate", _run_post_reload_speculate())`.
- Replace the outer `try/except Exception: pass` (which previously also caught the now-detached call) with logic that only covers `soul_engine.get_profile()` and `memory_manager.load_discovery_runtime_state()` — both still sync-ish prerequisites we want to fail silently per current behavior.
- Add a single `logger.debug("post-reload speculator scheduled as background task")` at the schedule site.

Do not change the `prewarm_pool_mmr_embeddings` block — it is already detached and is the model we are following.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass. Also run the full hot-reload suite to confirm no regression:

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -k "config or reload or default_provider" -q
```

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/runtime_context.py tests/test_api_app.py
git commit -m "fix(api): detach post-reload speculator tick so config PUT returns promptly"
```

---

### Task 2: Add Timeout Support to Popup `requestJson`

**Files:**
- Modify: `extension/popup/popup-api.js`
- Test: `extension/tests/popup-api.test.ts` (extend existing)

**Step 1: Write failing tests**

Add tests proving:
- `requestJson(path, { timeoutMs: 50 })` rejects with an `AbortError` (`error.name === "AbortError"`) when the fake fetch never resolves within 50 ms. This requires exporting `requestJson` from `popup-api.js`; it is currently private.
- `requestJson(path, {})` (no timeout option) preserves today's behavior: no abort, no signal forwarded unless caller supplies one.
- `requestJson(path, { signal: callerSignal, timeoutMs: 200 })` aborts on whichever signal fires first; if the caller signal aborts before the timeout, the resulting error preserves the caller's `signal.reason` if available.
- `updateConfig(data)` rejects with an `AbortError` when the fake fetch hangs longer than 60s (use a fake timer to advance virtual time).
- Non-2xx responses still throw the same `error.status` / `error.details` shape the popup save handler already parses.

**Step 2: Run tests to verify failure**

```bash
cd extension && node --test --experimental-strip-types tests/popup-api.test.ts
```

Expected: fail because `requestJson` ignores `timeoutMs` and `updateConfig` does not set one.

**Step 3: Implement timeout plumbing**

In `extension/popup/popup-api.js`:

- Extract a tiny `withTimeout(signal, timeoutMs)` helper that returns an `AbortSignal` combining a caller-supplied signal (if any) with a `setTimeout(controller.abort, timeoutMs)`.
- Export `requestJson(path, options = {})`, extract `timeoutMs` from `options` so the custom field is never forwarded to `fetch`, build the combined signal via `withTimeout`, forward it as `signal` to `fetch`, and clear the timer in a `finally`.
- In `updateConfig`, pass `timeoutMs: 60_000` (define a module-level constant `CONFIG_PUT_TIMEOUT_MS = 60_000`).
- Do NOT change other call sites — adopting timeouts elsewhere is out of scope; some callers (delight respond, etc.) already build their own ad-hoc `AbortController` and should keep working unchanged.

**Step 4: Run tests to verify pass**

Run the same node test and confirm pass.

**Step 5: Commit**

```bash
git add extension/popup/popup-api.js extension/tests/popup-api.test.ts
git commit -m "feat(extension): bound config PUT with 60s AbortController timeout"
```

---

### Task 3: Popup Save Handler Renders Timeout Toast

**Files:**
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-settings.test.ts` (extend existing source-level settings tests; no jsdom dependency is currently installed)

**Step 1: Write failing tests**

Add tests proving:
- The save handler has an `err?.name === "AbortError"` branch before `renderStructuredConfigError(err)` / generic `保存失败`.
- The abort branch renders an amber/warning toast with text matching `/超时.*可能已写入.*后台/` and a message clarifying that the user may refresh settings to confirm.
- The abort branch returns before the success branch, so `applyRuntimeConfig(result.config)` and `renderIssues(result.config.issues)` cannot run for an abort.
- The shared `finally` still resets `saveBtn.disabled` and `setSaveButtonMode(...)`.
- Existing structured-error rendering (`renderStructuredConfigError`) and the generic `保存失败` toast remain wired for non-abort errors (regression).

**Step 2: Run tests to verify failure**

```bash
cd extension && node --test --experimental-strip-types tests/popup-settings.test.ts
```

Expected: fail because the save handler currently treats AbortError as a generic error and re-throws into the `保存失败: <err.message>` path.

**Step 3: Implement timeout UX branch**

In `extension/popup/popup.js` around lines 4422-4463 (the `saveBtn.addEventListener("click", ...)` body):

- After `const data = collectForm();`, wrap the `updateConfig(data)` call in a try/catch that:
  - Detects `err?.name === "AbortError"` first.
  - Renders an amber toast: `"后端处理超时，保存请求可能已写入；热重载可能仍在后台进行。请稍后刷新设置确认。"` using the existing `showToast(msg, "warning")` API.
  - Re-enables the save button via the existing `finally`.
  - Returns early so the success branch does not run.
- The existing `renderStructuredConfigError(err)` check stays exactly as-is for non-abort errors.

Do NOT touch the `portChanged` branch or the runtime-stream reconnect logic; the timeout case does not retroactively know whether the port change committed and should not attempt to reconnect.

**Step 4: Run tests to verify pass**

Run the same node test plus the broader popup-save regression set:

```bash
cd extension && node --test --experimental-strip-types tests/popup-settings.test.ts
```

**Step 5: Commit**

```bash
git add extension/popup/popup.js extension/tests/popup-settings.test.ts
git commit -m "feat(extension): render amber timeout toast on config PUT abort"
```

---

### Task 4: Per-Call `model` Override on Chat Providers

**Files:**
- Modify: `src/openbiliclaw/llm/base.py`
- Modify: `src/openbiliclaw/llm/openai_provider.py`
- Modify: `src/openbiliclaw/llm/claude_provider.py`
- Modify: `src/openbiliclaw/llm/gemini_provider.py`
- Modify: `src/openbiliclaw/llm/ollama_provider.py`
- Modify: `src/openbiliclaw/llm/openrouter_provider.py`
- Test: `tests/test_llm_providers.py`

**Step 1: Write failing tests**

For each chat-capable concrete provider, add tests proving:
- `complete(messages, model=None)` uses `self._model` (existing behavior).
- `complete(messages, model="alt-model-x")` uses `alt-model-x` for the SDK kwargs of this call only; `self._model` is unchanged after the call.
- `complete(messages, model="")` is treated identically to `model=None` (empty string = "no override").
- The returned `LLMResponse.model` reflects whichever model the provider actually sent to the SDK (the test inspects either the kwargs the mock SDK saw, or the response.model echoed back).
- Gemini-specific regression: when `model="gemini-3-pro"` is passed, the `thinking_budget=0` JSON-mode shortcut checks the effective per-call model, not the provider's configured `self._model`.
- The `LLMRegistry.complete(...)` chain still works unchanged (regression — registry never passes `model` per-call yet, so default behavior is preserved).

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_llm_providers.py -k "model_override or per_call_model" -q
```

Expected: fail because no provider accepts a `model` kwarg.

**Step 3: Implement per-call model**

- In `src/openbiliclaw/llm/base.py:82-110` (`LLMProvider.complete` abstract signature), add `model: str | None = None` after `reasoning_effort`. Document in the docstring: "Per-call model override. ``None`` or empty string = use the provider's configured ``self._model``."
- In each concrete provider:
  - Add `model: str | None = None` to the `complete()` signature in the same position.
  - Resolve `effective_model = (model or "").strip() or self._model` near the top of the method.
  - Replace every use of `self._model` inside the body with `effective_model` (typically a single SDK kwargs line).
  - Do NOT mutate `self._model`.
- For providers that subclass the OpenAI provider (DeepSeek lives in `openai_provider.py`, plus any `openai_compatible` adapter): ensure the override propagates through the inherited path. If the subclass overrides `_request_with_retry` or similar, also pass `model` through.

**Step 4: Run tests to verify pass**

Run the same pytest command plus the broader LLM suite to catch regressions:

```bash
uv run --extra dev python -m pytest tests/test_llm_providers.py tests/test_llm_registry.py tests/test_llm_service.py -q
```

**Step 5: Commit**

```bash
git add src/openbiliclaw/llm/base.py src/openbiliclaw/llm/openai_provider.py src/openbiliclaw/llm/claude_provider.py src/openbiliclaw/llm/gemini_provider.py src/openbiliclaw/llm/ollama_provider.py src/openbiliclaw/llm/openrouter_provider.py tests/test_llm_providers.py
git commit -m "feat(llm): accept per-call model override on every chat provider"
```

---

### Task 5: `LLMService` Route-Bucket Routing

**Files:**
- Modify: `src/openbiliclaw/llm/base.py`
- Modify: `src/openbiliclaw/llm/service.py`
- Test: `tests/test_llm_service.py`
- Test: `tests/test_llm_registry.py`

**Step 1: Write failing tests**

Add tests proving:
- `LLMService(registry=..., memory=...)` constructed without `module_overrides` continues to dispatch via `registry.complete(...)` (regression).
- `LLMService(..., module_overrides={"soul": ModuleOverride("claude", "")})` dispatches `caller="soul.preference"` via `registry.complete_provider("claude", ..., model=None)`; `registry.complete` is not called.
- Same setup, `caller="soul"` (no dot) routes to claude.
- Same setup with `module_overrides={"soul": ModuleOverride("claude", "claude-3-opus")}` → dispatch passes `model="claude-3-opus"` to the provider.
- `module_overrides={"soul": ModuleOverride("Claude", "")}` normalizes provider names to lowercase and routes via `claude`.
- `module_overrides={"soul": ModuleOverride("unregistered_provider", "")}` falls back to `registry.complete(...)` and logs INFO once per `(route_bucket, attempted_provider)` per process (use `caplog` to assert exactly one INFO record).
- `module_overrides={"soul": ModuleOverride("ollama", "")}` falls back to `registry.complete(...)` and logs INFO once when the registry has `ollama` registered with `chat_capable=False`.
- `module_overrides={"soul": ModuleOverride("", "")}` (empty provider) routes via the default chain and does not log an unknown-provider INFO.
- `caller=""` (no caller tag) bypasses routing entirely.
- `caller="recommendation.write_expression"` with `module_overrides={"recommendation": ModuleOverride("claude", "claude-3-opus")}` dispatches via claude with model override; usage recorder receives `response.model == "claude-3-opus"` (test the recorder writes the routed model).
- `caller="recommendation.delight_score"` with `module_overrides={"evaluation": ModuleOverride("deepseek", "deepseek-v4-flash"), "recommendation": ModuleOverride("claude", "")}` routes to evaluation/deepseek because the evaluation-specific prefix beats the broader recommendation bucket.
- `caller="eval.relevance"` with `module_overrides={"evaluation": ModuleOverride("deepseek", "")}` routes to evaluation/deepseek.
- `caller="discovery.search.queries"` with `module_overrides={"discovery": ModuleOverride("gemini", "")}` routes to discovery/gemini.
- `caller="sources.xhs.keyword_gen"` with `module_overrides={"discovery": ModuleOverride("gemini", "")}` routes to discovery/gemini.
- All four routing wrappers — `complete_with_core_memory`, `complete_structured_task`, `complete_with_tools`, `complete_socratic_dialogue` — honor the same routing (they all funnel through `complete_with_core_memory`, so verifying the four paths is one extra integration assertion per wrapper).
- `LLMRegistry.complete_provider("claude", ...)` calls exactly one provider, passes `model=...`, does not walk fallback, marks rate-limit cooldown on `LLMRateLimitError`, and refuses providers registered with `chat_capable=False`.
- Existing `tests/test_llm_service.py` tests stay green.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_llm_service.py tests/test_llm_registry.py -k "routing or module_override or route_bucket or complete_provider" -q
```

Expected: fail because `LLMService` has no `module_overrides` parameter, `LLMRegistry` has no `complete_provider` helper, and `complete_with_core_memory` always calls `registry.complete(...)`.

**Step 3: Implement routing**

In `src/openbiliclaw/llm/base.py`:

- Add `def is_chat_capable(self, name: str) -> bool` to `LLMRegistry`. It returns `name in self._providers and name not in self._chat_disabled`.
- Add `async def complete_provider(self, provider_name: str, messages: ..., *, temperature=..., max_tokens=..., json_mode=..., reasoning_effort=None, model=None) -> LLMResponse`.
- `complete_provider` must:
  - Normalize `provider_name = provider_name.strip().lower()`.
  - Raise `KeyError` or `LLMProviderError` when the provider is missing or not chat-capable; `LLMService._resolve_route` should prevent this in normal operation.
  - Check `_provider_on_cooldown(provider_name)` and raise `LLMRateLimitError` if still cooling down.
  - Call exactly `self.get(provider_name).complete(..., model=model, ...)`.
  - Pop cooldown on success.
  - On `LLMRateLimitError`, call `_mark_rate_limited(provider_name)` and re-raise.
  - On other `LLMProviderError` / `LLMTimeoutError`, re-raise without walking fallback.

In `src/openbiliclaw/llm/service.py`:

- Extend the `SupportsComplete` protocol with `complete_provider(...)` and `is_chat_capable(name)` so `mypy src/` understands routed dispatch. Existing tests that pass minimal fake registries must gain no-op/default implementations.
- Define a frozen dataclass `ModuleOverride` with `provider: str = ""` and `model: str = ""` (both default empty). Place it near the top of the file, alongside the existing priority dataclass / constants.
- Define `_ROUTE_BUCKET_PREFIXES` as an ordered tuple of `(prefix, bucket)` pairs, longest/specific prefixes first:
  - `("recommendation.delight_score", "evaluation")`
  - `("recommendation.evaluate_batch", "evaluation")`
  - `("discovery.evaluate", "evaluation")`
  - `("eval", "evaluation")`
  - `("discovery.search", "discovery")`
  - `("discovery.explore", "discovery")`
  - `("discovery.trending", "discovery")`
  - `("discovery.related", "discovery")`
  - `("yt_search", "discovery")`
  - `("sources.xhs", "discovery")`
  - `("recommendation", "recommendation")`
  - `("soul", "soul")`
- Add `_route_bucket_for_caller(caller: str) -> str | None` using the same boundary rule as `_resolve_priority`: a prefix matches when `caller == prefix` or `caller.startswith(prefix + ".")`.
- Add `module_overrides: Mapping[str, ModuleOverride] = field(default_factory=dict)` to the `LLMService` dataclass. Use `Mapping` to allow callers to pass any read-only mapping. Default empty preserves all existing behavior.
- Add a private classmethod / instance method `_resolve_route(self, caller: str) -> tuple[str, str | None]`:
  - Empty caller → `("", None)`.
  - Resolve `route_bucket = _route_bucket_for_caller(caller.lower())`; unknown caller → `("", None)`.
  - No override entry for that route bucket → `("", None)`.
  - Override provider empty → `("", None)` with no log.
  - Normalize provider with `.strip().lower()`.
  - Override provider not chat-capable per `self.registry.is_chat_capable(provider_name)` → log INFO once (use a `set[tuple[str, str]]` instance attribute `_logged_unknown_override_keys` to dedupe `(route_bucket, attempted_provider)` pairs) and return `("", None)`.
  - Override model empty → return `(provider_name, None)`.
  - Else return `(provider_name, model)`.
- In `complete_with_core_memory`, after computing `priority` and before entering the priority gate, call `route = self._resolve_route(caller)`. Then:
  - If `route[0]` is empty: keep the existing `self.registry.complete(...)` call.
  - Else: `response = await self.registry.complete_provider(route[0], messages, temperature=..., max_tokens=..., json_mode=..., reasoning_effort=..., model=route[1])`.
- The priority gate, usage recorder, error handling, and empty-content guard wrap the same way as today (they are call-shape agnostic).

Tests using `FakeRegistry` need `.complete_provider(...)` and `.is_chat_capable(name)` shims — extend the existing fake in `tests/test_llm_service.py`.

**Step 4: Run tests to verify pass**

Run the full LLM service + provider test suite:

```bash
uv run --extra dev python -m pytest tests/test_llm_service.py tests/test_llm_providers.py tests/test_llm_registry.py -q
```

**Step 5: Commit**

```bash
git add src/openbiliclaw/llm/base.py src/openbiliclaw/llm/service.py tests/test_llm_service.py tests/test_llm_registry.py
git commit -m "feat(llm): route LLM calls by module override buckets"
```

---

### Task 6: Wire `module_overrides` Through Config-Backed Builders

**Files:**
- Modify: `src/openbiliclaw/llm/service.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Test: `tests/test_api_app.py` (extend hot-reload tests)
- Test: `tests/test_soul_engine.py` (or equivalent existing soul engine test file)
- Test: `tests/test_llm_service.py`
- Test: `tests/test_openclaw_adapter.py` or the narrowest existing OpenClaw bootstrap test

**Step 1: Write failing tests**

Add tests proving:
- `collect_module_overrides(config)` returns route buckets for `soul`, `discovery`, `recommendation`, `evaluation`, lowercases provider names, preserves model strings, and filters out entries with empty provider even when model is non-empty.
- `SoulEngine(llm=..., memory=..., usage_recorder=..., module_overrides={"soul": ModuleOverride("claude", "")})` constructs an internal `LLMService` whose `module_overrides` matches the input. Existing constructor calls without the new kwarg still work and produce a `LLMService` with empty `module_overrides`.
- `RuntimeContext._rebuild_components` builds a `module_overrides` dict from `new_config.llm.{soul,discovery,recommendation,evaluation}` (filtering out modules with empty provider) and passes it to:
  - the top-level `LLMService(registry=..., ..., module_overrides=...)` construction;
  - the `SoulEngine(..., module_overrides=...)` construction.
- Hot-reload integration: `PUT /api/config` with `{"llm": {"default_provider": "openai", "soul": {"provider": "claude", "model": ""}}}` then assert `ctx.llm_service.module_overrides["soul"] == ModuleOverride("claude", "")` and `ctx.soul_engine._llm_service.module_overrides["soul"] == ModuleOverride("claude", "")`.
- Override removed: subsequent `PUT /api/config` with `{"llm": {"soul": {"provider": ""}}}` results in `ctx.llm_service.module_overrides` no longer containing `"soul"` (or the entry has empty provider).
- Defensive: modules with provider empty but model non-empty are NOT registered in the overrides dict (the model alone has no effect; we don't pretend it does).
- `SocraticDialogue(llm=registry, soul_engine=..., module_overrides=overrides)._build_service()` passes the overrides into the fallback `LLMService`.
- `build_openclaw_adapter_services()` passes the collected overrides into both `SoulEngine` and the top-level `LLMService` (use monkeypatched builders/fakes; do not hit real providers).

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py tests/test_soul_engine.py tests/test_llm_service.py tests/test_openclaw_adapter.py -k "module_overrides or routing_wiring or collect_module_overrides" -q
```

Expected: fail because the wiring does not exist.

**Step 3: Implement wiring**

In `src/openbiliclaw/llm/service.py`:

- Add `collect_module_overrides(cfg: object) -> dict[str, ModuleOverride]`.
- It should read `cfg.llm.soul`, `cfg.llm.discovery`, `cfg.llm.recommendation`, and `cfg.llm.evaluation`.
- For each module, include an entry only when `provider.strip()` is non-empty.
- Normalize provider to lowercase with `provider.strip().lower()`.
- Preserve `model.strip()` as the per-call model override.

In `src/openbiliclaw/api/runtime_context.py`:

- Import `collect_module_overrides` from `openbiliclaw.llm.service`.
- In `_rebuild_components`, call the helper once and pass the result both into `LLMService(..., module_overrides=overrides)` (line 145) and into `SoulEngine(..., module_overrides=overrides)` (line 176).

In `src/openbiliclaw/soul/engine.py`:

- Add `module_overrides: Mapping[str, ModuleOverride] | None = None` to the `__init__` signature, keyword-only. Default `None` → `{}` when constructing the internal `LLMService`.
- Pass `module_overrides=module_overrides or {}` into the internal `LLMService(registry=llm, memory=memory, usage_recorder=usage_recorder, module_overrides=...)` at line 94.
- Document in the class docstring: "module_overrides routes module-tagged callers to alternate providers per `docs/modules/llm.md`."

In `src/openbiliclaw/soul/dialogue.py`:

- Add `module_overrides: Mapping[str, ModuleOverride] | None = None` to `SocraticDialogue.__init__` and store `self._module_overrides = module_overrides or {}`.
- In `_build_service()`, pass `module_overrides=self._module_overrides` into the fallback `LLMService`.
- Leave the FastAPI path unchanged when an explicit `llm_service` is injected; that service already carries routing.

In `src/openbiliclaw/cli.py`:

- Wherever a builder loads `cfg = load_config()` and constructs `SoulEngine` or `LLMService`, compute `overrides = collect_module_overrides(cfg)` and pass it through.
- Cover `_build_soul_engine`, `_build_recommendation_engine`, `_build_discovery_engine`, `_build_dialogue`, and the XHS producer command path that creates `LLMService(registry=registry, memory=memory)`.

In `src/openbiliclaw/integrations/openclaw/bootstrap.py`:

- Compute `module_overrides = collect_module_overrides(config)` after `config = load_config()`.
- Pass it into `SoulEngine(...)` and `LLMService(...)`.

**Step 4: Run tests to verify pass**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py tests/test_soul_engine.py tests/test_llm_service.py tests/test_openclaw_adapter.py tests/test_cli.py -q
```

**Step 5: Commit**

```bash
git add src/openbiliclaw/llm/service.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/dialogue.py src/openbiliclaw/cli.py src/openbiliclaw/integrations/openclaw/bootstrap.py tests/test_api_app.py tests/test_soul_engine.py tests/test_llm_service.py tests/test_openclaw_adapter.py tests/test_cli.py
git commit -m "feat(runtime): wire per-module LLM overrides into config-backed builders"
```

---

### Task 7: End-to-End Routing Regression Test

**Files:**
- Create: `tests/test_llm_module_override_routing.py`

**Step 1: Write the regression test**

The test must:
- Build parameterized `Config` objects with `default_provider="openai"` plus at least two configured providers for routing assertions (e.g. openai + claude + deepseek fake provider overrides).
- Build the real `LLMRegistry` via `build_llm_registry(cfg)`.
- Monkeypatch each registered provider's `.complete` to an `AsyncMock` returning a distinctive `LLMResponse(provider=<name>)`.
- Construct an `LLMService` with `module_overrides` derived from cfg via `collect_module_overrides(cfg)`.
- Cover at least this matrix:
  - `[llm.soul].provider="claude"`, caller `soul.preference` → claude.
  - `[llm.discovery].provider="claude"`, caller `discovery.search.queries` → claude.
  - `[llm.discovery].provider="claude"`, caller `sources.xhs.keyword_gen` → claude.
  - `[llm.recommendation].provider="claude"`, caller `recommendation.write_expression` → claude.
  - `[llm.evaluation].provider="deepseek"`, caller `eval.relevance` → deepseek.
  - `[llm.evaluation].provider="deepseek"` and `[llm.recommendation].provider="claude"`, caller `recommendation.delight_score` → deepseek (specific evaluation prefix wins).
- For each case, assert `response.provider == expected_provider`; assert the default provider was not called and the expected provider was called exactly once.

This is the test that would have failed against `main` today and proves the documented contract holds.

**Step 2: Run test to verify pass**

```bash
uv run --extra dev python -m pytest tests/test_llm_module_override_routing.py -v
```

Expected: pass on this branch; would have failed on `main` pre-Tasks 4-6.

**Step 3: No implementation** — Tasks 4-6 already shipped the routing. This task is a single dedicated regression so the contract is impossible to silently break in future refactors.

**Step 4: Commit**

```bash
git add tests/test_llm_module_override_routing.py
git commit -m "test(llm): pin per-module override routing as a top-level regression"
```

---

### Task 8: Documentation and Changelog

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/llm.md` (create if absent)
- Modify: `docs/modules/api.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/architecture.md`
- Modify: `docs/changelog.md`
- Modify: `README.md`
- Modify: `README_EN.md`

**Step 1: Update module docs**

- `docs/modules/config.md` — under `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]`: clarify that the override now routes at runtime (no caveats, no "planned"). Explicitly document: empty `provider` = no override; non-empty `provider` not registered = silent INFO fallback; non-empty `model` requires non-empty `provider` to have effect.
- `docs/modules/llm.md` — add a "Per-module route buckets" section describing the built-in route table (`soul`, discovery generation prefixes, evaluation/scoring prefixes, recommendation copy prefixes), the `(provider_name, model)` dispatch, chat-capable provider eligibility, fallback semantics, and that error propagation is per-provider (rate-limit on the override does NOT silently spill to default).
- `docs/modules/api.md` — under `PUT /api/config`, document the new timing guarantee: response returns once components are atomically swapped; post-reload speculator runs detached and does not block the response.
- `docs/modules/extension.md` — popup save flow: 60s timeout, amber toast on timeout, cache snapshot behavior unchanged.
- `docs/architecture.md` — if the runtime data flow mentions module overrides as planned/future, mark active. If it does not mention them at all, add a short paragraph in the LLM-layer section.

**Step 2: Changelog and README highlights**

Per CLAUDE.md rules:
- `docs/changelog.md` — add a top entry for the next version (read current version from `src/openbiliclaw/__init__.py`, bump appropriately). Two bullets minimum: one for "config PUT no longer hangs on post-reload speculator + 60s frontend timeout" and one for "per-module LLM overrides now route at runtime". Both bullets reference the user-facing symptom they fix.
- `README.md` / `README_EN.md` — **replace** the existing 📌 highlights callout. New callout must:
  - Have ≤ 4 bullets, each ≤ 1 sentence, surfacing only user-facing wins (config save UX + per-module routing).
  - End with `完整变更详见 [docs/changelog.md](docs/changelog.md)。` (CN) / `Full changelog: [docs/changelog.md](docs/changelog.md).` (EN).
  - Be identical between CN/EN in bullet count, items, and order.
  - Skip internal smokes, test coverage, refactor notes.

**Step 3: Verify CLAUDE.md pre-merge checklist**

Confirm:
- [ ] `docs/modules/{config,llm,api,extension}.md` updated.
- [ ] `docs/changelog.md` has a new entry.
- [ ] `docs/architecture.md` reflects the now-active routing layer.
- [ ] No CLI / config flag changes other than the documented `[llm.<module>]` semantics shift (no new flags introduced — `docs/modules/cli.md` does not need an update).
- [ ] README CN/EN 📌 highlights callout **replaced** (not appended), ≤4 bullets, CN/EN in sync.

**Step 4: Commit**

```bash
git add docs/modules/config.md docs/modules/llm.md docs/modules/api.md docs/modules/extension.md docs/architecture.md docs/changelog.md README.md README_EN.md
git commit -m "docs: document config-PUT timing + per-module LLM routing"
```

---

## Order, Dependencies, Shippability

- Tasks 1-3 form Workstream A and can ship together as one PR (backend detach + frontend timeout/UX). Task 1 can ship independently before 2-3 if needed — the backend fix alone eliminates the root cause; the frontend timeout is defense-in-depth.
- Tasks 4-7 form Workstream B and must ship together in order: Task 4 (provider model override) is a prerequisite for Task 5 (LLMService routing), which is a prerequisite for Task 6 (wiring) and Task 7 (regression). Splitting B across PRs would leave a half-wired routing pipeline.
- Task 8 lands with whichever PR includes the user-facing change being documented. If A and B ship in separate PRs, split Task 8 between them — A gets the PUT-timing + popup-timeout docs; B gets the routing + changelog + README highlights.
- Workstream A and Workstream B are logically independent, but they are not file-disjoint: Task 1 and Task 6 both touch `src/openbiliclaw/api/runtime_context.py` and `tests/test_api_app.py`. If parallelized, land/rebase A before B's runtime wiring or expect a small merge conflict.

## Out of Scope

- A wholly offline popup that queues edits when the daemon is unreachable (already covered by the separate config-deadlock-fix degraded mode).
- Making `rebuild_from_config` itself preemptable or showing per-component progress in the popup.
- Adding user-configurable per-task routing finer than the built-in route buckets (`caller="soul.preference"` getting a different target than `caller="soul.insight"`).
- Routing for embeddings (`build_embedding_service` already resolves provider from `[llm.embedding]`).
- A static-validation pass that surfaces "you picked `claud` but meant `claude`" as a popup issue — listed under design Risk but deferred.
