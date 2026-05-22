# Config Deadlock Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the config-deadlock chain and same-session reliability failures evidenced by the raw user logs: stale backend update checks, unsafe config saves, silent credential erasure risk, no FastAPI recovery surface when LLM registry construction fails, mimo JSON shape failures, noisy Ollama embedding warnings, and missing file-log traceback guarantees.

**Architecture:** Tag-prefix-aware auto-updater (`backend-v*` plus legacy bare backend tags, never extension releases); explicit reset + mask/empty-value guards on config field writes; transactional `PUT /api/config` (validate -> snapshot -> write -> reload -> rollback-on-failure) with parseable 400 bodies; FastAPI-only degraded mode by catching `RegistryBuildError` in `create_app`, plus popup last-known-good `chrome.storage.local` cache fallback; shared LLM structured-output coercion for wrapper / JSONL / echoed-schema shapes; Ollama-specific embedding warning hygiene; file logging tests that prove exception tracebacks are retained.

**Tech Stack:** Python (FastAPI / httpx / asyncio / pytest) for backend; popup vanilla JS + node `--test` for extension; no new dependencies.

**Reference Design:** `docs/plans/2026-05-17-config-deadlock-fix-design.md`.

---

## Workstream A — Auto-updater filters for `backend-v*` tags

### Task A1: Failing tests for `_parse_backend_version` + `_fetch_latest_version`

**Files:**
- Test: `tests/test_runtime_updater.py` (extend if exists; create otherwise)

**Step 1: Write failing tests**

Cover:
- `_parse_backend_version("backend-v0.3.71") == (0, 3, 71)`.
- `_parse_backend_version("backend-v0.3.71-rc1") == (0, 3, 71)` (suffix tolerated).
- `_parse_backend_version("extension-v0.3.24") is None`.
- `_parse_backend_version("v0.3.71") == (0, 3, 71)` (legacy bare-tag).
- `_parse_backend_version("0.3.71") == (0, 3, 71)` (legacy bare-tag).
- `_parse_backend_version("backend-vfoo") is None`.
- `_parse_backend_version("") is None`.
- Stubbed `httpx.AsyncClient` returning a `/tags` payload containing `[{"name":"extension-v0.3.24"},{"name":"backend-v0.3.71"},{"name":"backend-v0.3.69"}]` → `_fetch_latest_version` returns `"backend-v0.3.71"`.
- Stubbed paginated `/tags`: page 1 contains only extension tags, page 2 contains `backend-v0.3.69`, page 3 empty → `_fetch_latest_version` returns `"backend-v0.3.69"`.
- Stubbed `/tags` returning only extension tags → `_fetch_latest_version` returns `""`.
- Stubbed `/tags` returning empty list → `_fetch_latest_version` returns `""`.
- Stubbed httpx error → `_fetch_latest_version` returns `""`, WARN logged once.
- Regression from raw logs: when `/tags` contains only `extension-v0.3.20` / `extension-v0.3.24`, `AutoUpdateService.check_and_update_now` returns `{"updated": False, "reason": "no_backend_tag_yet"}` and logs INFO with that reason, not `Already up-to-date: current=0.3.64, remote=extension-v...`.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_runtime_updater.py -q
```

Expected: fail because `_parse_backend_version` does not exist yet and `_fetch_latest_version` returns extension tags.

**Step 3: Implement changes**

In `src/openbiliclaw/runtime/updater.py`:
- Add module-level `_BACKEND_TAG_PREFIX = "backend-v"`.
- Add `_parse_backend_version(tag: str) -> tuple[int, ...] | None`:
  - Strip whitespace.
  - If starts with `_BACKEND_TAG_PREFIX`, strip prefix.
  - Else if tag starts with `v` / `V` followed by a digit, strip that one leading `v` for legacy support.
  - Else if first character is a letter, return `None` (non-backend tag such as `extension-v0.3.24`).
  - Parse leading `\d+(\.\d+)*` segments; return `None` if no leading digits.
  - Tolerate `-rc1` / `+build` suffixes by stopping at the first non-`.` non-digit segment.
- Rewrite `_fetch_latest_version`:
  - Skip `/releases/latest` entirely (it's misleading under the source-only release policy).
  - Call `/tags` with `per_page=100&page=N`; scan pages `1..5` or until an empty page.
  - Filter to entries where `_parse_backend_version(tag.name)` is not `None`.
  - Return the max by parsed-version tuple (use `max(..., key=...)` after collecting parsed pairs).
  - Return `""` on empty filtered list, HTTP error, or unexpected response shape.
- Adjust `check_and_update_now` so the `""` return logs INFO `no_backend_tag_yet` instead of treating it as up-to-date.

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_runtime_updater.py -q
ruff check src/openbiliclaw/runtime/updater.py tests/test_runtime_updater.py
mypy src/openbiliclaw/runtime/updater.py
```

**Step 5: Commit**

```
fix(updater): filter for backend-v tags so auto-update stops sleeping on extension releases
```

---

### Task A2: Document the release-policy ↔ updater contract

**Files:**
- Create or modify: `docs/modules/runtime.md` (if creating, also update `docs/index.md`)
- Modify: `docs/architecture.md`
- Modify: `docs/changelog.md`
- Modify: `src/openbiliclaw/runtime/updater.py` (docstring block at module top)

**Step 1: Update repo-visible docs**

Add a short "Auto-update release contract" section:
- backend source updates are discovered from git tags (`backend-v*`; legacy `v*` / bare semver tolerated);
- extension release artifacts use `extension-v*` and must be ignored by backend auto-update;
- `/releases/latest` is not authoritative for backend because current GitHub Releases are extension artifacts.

If this creates `docs/modules/runtime.md`, add it to `docs/index.md`. Add a one-line `docs/changelog.md` entry for the updater fix.

The local `.claude/.../feedback_release_policy.md` memory may be updated manually after the PR, but it is not a repo artifact and must not be part of the commit.

**Step 2: Update module docstring**

The updater module top docstring (`updater.py:1`) must explicitly state:
- backend releases are git-tag-only with prefix `backend-v`;
- extension releases use `extension-v` prefix and MUST be ignored;
- `_fetch_latest_version` queries `/tags` (NOT `/releases/latest`) because under the current policy, `releases/latest` only ever returns extension tags.

**Step 3: Commit**

```
docs(updater): pin release-policy contract referenced by tag-filter logic
```

---

## Workstream B — Transactional `PUT /api/config`

### Task B1: Failing tests for validate / snapshot / rollback

**Files:**
- Test: `tests/test_api_config_transactional.py` (new file)

**Step 1: Write failing tests**

Use FastAPI `TestClient` against a freshly built app with a tmp `OPENBILICLAW_PROJECT_ROOT`. These tests assume Workstream C already added `reset_fields`, so validation can intentionally clear a key without relying on accidental empty-string erasure. Cover:

- **Validate rejection:** Seed config with `default_provider="openai"` and only `llm.openai.api_key` set. PUT `{"reset_fields":["llm.openai.api_key"]}`. Response is 400 with a parseable JSON body shaped like `ConfigUpdateResponse`: `ok=false`, `reloaded=false`, `rollback_applied=false`, and `config.issues` containing a blocking `llm` / `llm.openai.api_key` issue. On-disk `config.toml` is byte-identical to pre-PUT; no `config.toml.bak` was created.
- **Successful save + reload:** PUT a valid payload changing only `llm.openai.model`. Response is 200 with `{"reloaded": true, "rollback_applied": false, "restart_required": false}`. `config.toml` reflects the change. `config.toml.bak` exists and is byte-identical to the pre-PUT content.
- **Reload failure → rollback:** PUT a valid payload but monkeypatch `RuntimeContext.rebuild_from_config` to raise `RuntimeError("simulated")`. Response is 200 with `{"reloaded": false, "rollback_applied": true}`. `config.toml` on disk reverted to pre-PUT content. `config.toml.bak` still exists.
- **Rollback failure → 500:** PUT a valid payload; monkeypatch `rebuild_from_config` to raise; monkeypatch `Path.replace` (or whatever os primitive we use for restore) to also raise. Response is 500 with body explicitly naming `config.toml.bak` and instructing manual restoration.
- **Concurrent saves:** Two parallel `PUT /api/config` requests (use `asyncio.gather` with `httpx.AsyncClient`). Both succeed in sequence; final `config.toml` matches the second PUT's payload; `config.toml.bak` matches the first PUT's persisted state (not the original pre-test content).

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_api_config_transactional.py -q
```

**Step 3: Implement changes**

In `src/openbiliclaw/api/app.py` `update_config` (`api/app.py:3376-3611`):

1. Add module-level `_CONFIG_SAVE_LOCK = asyncio.Lock()` (or attach to the runtime context for testability).
2. After payload coercion, normal field updates, and explicit `reset_fields` application, call new `_validate_llm_buildable(cfg)`:
   ```python
   def _validate_llm_buildable(cfg: Config) -> list[ConfigIssue]:
       """Return blocking issues that would prevent a runtime rebuild."""
       issues = list(_collect_config_issues(cfg))
       try:
           build_llm_registry(cfg)
       except RegistryBuildError as exc:
           issues.append(ConfigIssue(
               field="llm",
               message=f"LLM registry would fail to build: {exc}",
               severity="blocking",
           ))
       return issues
   ```
   (Add `severity` to `ConfigIssue` if it doesn't already have it; default `"warning"`.)
3. Update `ConfigIssueOut` to include `severity: str = "warning"`, and make `_config_to_response` preserve it.
4. If any issue has `severity=="blocking"`, return a `JSONResponse(status_code=400, content=ConfigUpdateResponse(ok=False, config=..., message=..., reloaded=False, rollback_applied=False).model_dump(mode="json"))` BEFORE any disk write. Do not use `HTTPException`, because the popup needs the same response shape on both success and validation failure.
5. Inside `async with _CONFIG_SAVE_LOCK:`, perform:
   - Resolve target path via existing logic (probably `_default_config_path()` or similar).
   - If `config.toml` exists, copy it to `config.toml.bak` (use `shutil.copy2` with explicit error wrapping).
   - `save_config(cfg)` — overwrite `config.toml`.
   - `await ctx.rebuild_from_config(cfg)` inside try/except.
6. On rebuild exception: copy `config.toml.bak` back over `config.toml`; build response with `reloaded=False, rollback_applied=True`; preserve the original exception in the `reload_message` (truncate to ~200 chars).
7. On rebuild success: build response with `reloaded=True, rollback_applied=False`.
8. If `.bak` copy failed before `save_config`: abort with 500 BEFORE writing; the user's existing file is untouched.
9. If `.bak` restore failed after `save_config`: log CRITICAL with full traceback; return 500 with explicit body `{"error": "config_persistence_corrupted", "manual_recovery": "config.toml may be in inconsistent state; if config.toml.bak exists, manually copy it back."}`.

Update `ConfigUpdateResponse` (in `api/models.py` or wherever it lives) to add:
- `rollback_applied: bool = False`
- `restart_required: bool = False` (always False in this workstream; populated by Workstream D)

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_api_config_transactional.py -q
pytest tests/test_api_config.py -q  # existing tests must still pass
ruff check src/openbiliclaw/api/app.py
mypy src/openbiliclaw/api/app.py
```

**Step 5: Commit**

```
feat(api): make PUT /api/config transactional with validate + snapshot + rollback
```

---

### Task B2: Popup renders structured config validation errors

**Files:**
- Modify: `extension/popup/popup-api.js`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-api.test.ts`
- Test: `extension/tests/popup-settings.test.ts`

**Step 1: Write failing tests**

Cover:
- `requestJson("/config", ...)` on HTTP 400 with JSON body preserves `error.details` (or a named `ApiError.details`) instead of throwing only `"/config request failed: 400"`.
- Settings save receives a 400 `ConfigUpdateResponse` with `config.issues` and renders `settingsIssues` inline.
- Validation rejection toast uses error tone and does not claim the config was saved.

**Step 2: Run tests to verify failure**

```bash
cd extension && npm test -- popup-api.test.ts popup-settings.test.ts
```

Expected: fail because `requestJson` currently discards non-2xx JSON bodies and the settings save catch only displays `err.message`.

**Step 3: Implement**

In `popup-api.js`, parse JSON for non-2xx responses when possible:

```js
if (!response.ok) {
  let details = null;
  try {
    details = await response.json();
  } catch {
    details = null;
  }
  const error = new Error(`${path} request failed: ${response.status}`);
  error.status = response.status;
  error.details = details;
  throw error;
}
```

In `popup.js`, inside settings save `catch`, if `err.details?.config?.issues` exists, render those issues, apply `err.details.config` to runtime config if present, and show `err.details.message || "配置未保存，请先修正高亮问题。"` with error tone.

**Step 4: Run tests to verify pass**

```bash
cd extension && npm test -- popup-api.test.ts popup-settings.test.ts
npm run typecheck
```

**Step 5: Commit**

```
fix(extension): render structured config validation errors in settings
```

---

## Workstream C — Mask + empty-value guards on chat-provider PUT

### Task C1: Failing tests for chat-provider guard

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_config_guards.py` (new file)

**Step 1: Write failing tests**

Use FastAPI `TestClient` against a freshly built app. Seed `config.toml` with a known `[llm.openai] api_key = "sk-real-key-1234567890abcdef"` and `model = "gpt-4o-mini"`. Cover:

- **Masked api_key echo ignored:** PUT `{"llm": {"openai": {"api_key": "sk-d****cdef"}}}` → response 200, `config.toml` `api_key` still `sk-real-key-1234567890abcdef`.
- **Empty api_key ignored:** PUT `{"llm": {"openai": {"api_key": ""}}}` → response 200, `api_key` unchanged.
- **Real new api_key written:** PUT `{"llm": {"openai": {"api_key": "sk-new-real-key-fedcba0987654321"}}}` → response 200, `api_key` updated.
- **Empty model ignored:** PUT `{"llm": {"openai": {"model": ""}}}` → response 200, `model` still `gpt-4o-mini`.
- **Real new model written:** PUT `{"llm": {"openai": {"model": "gpt-4.1-mini"}}}` → response 200, `model` updated.
- **Whitespace-only api_key ignored:** PUT `{"llm": {"openai": {"api_key": "   "}}}` → response 200, `api_key` unchanged.
- **Other providers behave identically:** Repeat one mask + one empty case each for `claude`, `deepseek`, `openrouter`, `openai_compatible`.
- **Explicit reset works:** PUT `{"reset_fields": ["llm.openai.api_key"]}` → response 200, `api_key` becomes `""`.
- **Unknown reset rejected:** PUT `{"reset_fields": ["storage.db_path"]}` → response 400; `config.toml` and in-memory config are unchanged.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_api_config_guards.py -q
```

Expected: empty / masked tests fail because current handler blindly overwrites; reset tests fail because `ConfigUpdateIn.reset_fields` does not exist yet.

**Step 3: Implement changes**

In `src/openbiliclaw/api/models.py`:
- Add `reset_fields: list[str] | None = None` to `ConfigUpdateIn`.

In `api/app.py` `update_config` chat-provider loop (`api/app.py:3413-3434`):

Replace the loop body so each `setattr` is guarded. Concretely:

```python
for provider_name in (
    "openai", "claude", "gemini", "deepseek",
    "ollama", "openrouter", "openai_compatible",
):
    if provider_name not in llm_data or not isinstance(llm_data[provider_name], dict):
        continue
    provider_cfg = getattr(cfg.llm, provider_name)
    pdata = llm_data[provider_name]
    skipped_fields: list[str] = []
    for field_name in (
        "api_key", "model", "base_url",
        "http_referer", "x_title", "reasoning_effort",
    ):
        if field_name not in pdata:
            continue
        new_value = str(pdata[field_name])
        # Masked-value echo guard (consistent with embedding handling).
        if field_name == "api_key" and "*" in new_value:
            skipped_fields.append(f"{field_name}=masked")
            continue
        existing = getattr(provider_cfg, field_name, "")
        # Empty-value guard: don't erase an existing non-empty value.
        if not new_value.strip() and existing and existing.strip():
            skipped_fields.append(f"{field_name}=empty_skip")
            continue
        setattr(provider_cfg, field_name, new_value)
    if skipped_fields:
        logger.debug(
            "PUT /api/config: provider %s skipped fields: %s",
            provider_name, ", ".join(skipped_fields),
        )
```

Apply the same empty-value guard to the embedding block (`api/app.py:3435-3451`) for `model` and `base_url` (api_key already has its mask guard there). The semantics now match across all provider fields.

After normal payload updates, apply explicit resets:

```python
_RESETTABLE_CONFIG_FIELDS = {
    "llm.openai.api_key": lambda cfg: setattr(cfg.llm.openai, "api_key", ""),
    "llm.claude.api_key": lambda cfg: setattr(cfg.llm.claude, "api_key", ""),
    "llm.gemini.api_key": lambda cfg: setattr(cfg.llm.gemini, "api_key", ""),
    "llm.deepseek.api_key": lambda cfg: setattr(cfg.llm.deepseek, "api_key", ""),
    "llm.openrouter.api_key": lambda cfg: setattr(cfg.llm.openrouter, "api_key", ""),
    "llm.openai_compatible.api_key": lambda cfg: setattr(cfg.llm.openai_compatible, "api_key", ""),
    "llm.embedding.api_key": lambda cfg: setattr(cfg.llm.embedding, "api_key", ""),
}
```

Reject unknown reset paths with HTTP 400 before saving. Keep the allowlist intentionally narrow; add more paths only when the UI actually needs them.

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_api_config_guards.py -q
pytest tests/test_api_app.py -k "config" -q
ruff check src/openbiliclaw/api/app.py
mypy src/openbiliclaw/api/app.py
```

**Step 5: Commit**

```
fix(api): guard chat-provider PUT against masked + empty values
```

---

## Workstream D — Degraded-mode boot + popup offline fallback

### Task D1: Failing tests for degraded-mode FastAPI app

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_degraded_mode.py` (new file)

**Step 1: Write failing tests**

Cover:
- **Strict direct runtime construction:** Build a `Config` with `default_provider="openai"` but all provider API keys empty. `build_runtime_context(cfg)` raises `RegistryBuildError`. This guards the spec boundary: only FastAPI startup gets degraded mode.
- **Degraded boot:** Patch `openbiliclaw.config.load_config` to return that same invalid config, then call `create_app()` in the production path. The app builds (no exception). `GET /api/health` returns 200 with body `{"status": "degraded", "reason": "llm_registry_unavailable", "issues": [...]}`.
- **Degraded `/api/config` GET:** Returns 200 with the config payload plus `"degraded": true`, `"degraded_reason": "llm_registry_unavailable"`, and the issues array. This requires explicit fields on `ConfigResponse`; extra ad-hoc fields would be filtered by `response_model=ConfigResponse`.
- **Degraded `/api/config` PUT (recovery save):** PUT a valid LLM config payload. Response 200 with `{"reloaded": false, "rollback_applied": false, "restart_required": true}` and a message instructing restart. `config.toml` on disk reflects the new payload.
- **Degraded non-config endpoints 503:** `GET /api/recommendations`, `GET /api/profile-summary`, `POST /api/events`, etc. return 503 with body `{"status": "degraded", "reason": "..."}`. (Pick a representative subset, not exhaustive — sample one per category: discovery / events / recommendations / source-task.)
- **Degraded `/api/runtime-stream`:** Connect via `TestClient.websocket_connect`; receive the single `{"type": "degraded", "issues": [...]}` event; the socket stays open until closed by the client.
- **Normal boot is unchanged:** Build a `Config` with a valid api_key. `GET /api/health` returns the existing normal payload (no `degraded` flag).
- **Restart-into-normal:** After a degraded-mode PUT writes a fixing config, rebuilding the app picks up the new config and boots into normal mode.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_api_degraded_mode.py -q
```

**Step 3: Implement changes**

In `src/openbiliclaw/api/runtime_context.py`:

- Add `degraded: bool = False`, `degraded_reason: str = ""`, and `degraded_issues: list[ConfigIssue] = field(default_factory=list)` to `RuntimeContext`, or add a tiny subclass with the same public attributes.
- Add `build_degraded_runtime_context(config, *, memory_manager=None, database=None, event_hub=None, exc=None) -> RuntimeContext` that builds only stable components and sets the degraded fields. Keep `build_runtime_context` strict; do not catch `RegistryBuildError` there by default.

In `src/openbiliclaw/api/models.py`:

- Add `degraded: bool = False` and `degraded_reason: str = ""` to `ConfigResponse`.

In `src/openbiliclaw/api/app.py`:

- In `create_app`, wrap only the production-path `build_runtime_context(...)` call in `try/except RegistryBuildError`. On exception, call `build_degraded_runtime_context(...)`; injection-path tests that pass fake components should remain unchanged.
- After `ctx` exists, detect `ctx.degraded` and:
  - Register a per-route guard (FastAPI dependency or middleware) that short-circuits with 503 for all routes EXCEPT `/api/health`, `/api/config` (GET + PUT), `/api/runtime-status`, `/api/runtime-stream`.
  - The `/api/health` handler returns the degraded payload when `ctx.degraded`.
  - The `/api/config` GET handler includes `degraded=True`, `degraded_reason`, and issues via `_config_to_response`.
  - The `/api/config` PUT handler in degraded mode skips `rebuild_from_config` and instead returns `restart_required=True` in the response.
  - The `/api/runtime-stream` WS handler in degraded mode sends a single degraded event then idle-waits on the inbound receive pump (so disconnects are still detected and connections don't immediately close).
- Add a startup log line: when degraded, log WARNING with the issues list at boot.

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_api_degraded_mode.py -q
pytest tests/ -q  # full suite, ensure no regression
ruff check src/openbiliclaw/api/ src/openbiliclaw/api/runtime_context.py
mypy src/openbiliclaw/api/app.py src/openbiliclaw/api/runtime_context.py
```

**Step 5: Commit**

```
feat(api): degraded-mode boot so popup can recover from RegistryBuildError
```

---

### Task D2: CLI panel + console WARN for degraded boot

**Files:**
- Modify: `src/openbiliclaw/cli.py` (`_run_api_server` and / or `start` / `serve_api` panels)
- Test: `tests/test_cli_start.py` (extend if exists; create otherwise)

**Step 1: Write failing tests**

In `tests/test_cli_start.py` (extend if exists; create otherwise):
- Mock `create_app` to return a degraded app instance. Capture rich console output. Assert the output contains a panel with title like "降级模式" or "Degraded mode" and lists the issues.
- Normal boot output unchanged: still shows the existing "正在启动本地后端" panel.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_cli_start.py -q
```

Expected: fail because `_run_api_server` currently passes `create_app()` directly to `uvicorn.run(...)` without inspecting app degraded state.

**Step 3: Implement**

After `create_app()` returns and before `uvicorn.run(...)`, inspect `app.state.degraded_issues` (or however we expose it) and print a Rich `Panel` with the issues list plus a one-line pointer: "Open the extension popup settings to fix the LLM credentials, then restart the daemon."

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_cli_start.py -q
ruff check src/openbiliclaw/cli.py tests/test_cli_start.py
mypy src/openbiliclaw/cli.py
```

**Step 5: Commit**

```
feat(cli): surface degraded-mode boot in the start/serve-api panel
```

---

### Task D3: Popup last-known-good cache + degraded-mode rendering

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-api.js`
- Modify: `extension/popup/popup.html` (banner element)
- Test: `extension/tests/popup-settings.test.ts` (extend with new scenarios)

**Step 1: Write failing tests**

In `extension/tests/popup-settings.test.ts`:
- **Cache write on successful fetch:** Mock `fetchConfig()` to return a known payload. Click gear. Assert `chrome.storage.local.set` was called with key `openbiliclaw.config_cache` and a value containing the payload plus a `cached_at` timestamp.
- **Cache fallback when fetch HTTP-errors:** Pre-seed `chrome.storage.local` with a cached payload. Mock `fetchConfig()` to throw. Click gear. Assert form populates from the cache; assert the offline banner is visible with text referencing the `cached_at` timestamp.
- **Degraded-mode rendering:** Mock `fetchConfig()` to return a payload with `degraded=true` and an `issues` array. Click gear. Assert the degraded banner is visible with text containing "restart" and the issues are rendered inline.
- **Cache miss + fetch error:** Empty `chrome.storage.local`, mock `fetchConfig()` to throw. Click gear. Assert the form remains empty AND a banner explains "backend unreachable and no cached config available — please start the daemon".

**Step 2: Run tests to verify failure**

```bash
cd extension && npm test
```

**Step 3: Implement**

In `extension/popup/popup-api.js`:
- Wrap `fetchConfig()` to also persist successful responses to `chrome.storage.local` via a helper `cacheConfigSnapshot(cfg)`.
- Add `readCachedConfigSnapshot()` returning the cached payload or null.

In `extension/popup/popup.js` gear-button handler:
- After `await populateBackendEndpoint()`, try `fetchConfig()`. On success: render form, write cache, hide all banners.
- On HTTP error: read cache; if present, render form from cache + show offline banner with cache timestamp + show save-button-amber state; if absent, show "no cache + backend unreachable" banner and leave form empty.
- On 200 with `degraded=true`: render form normally + show degraded banner with the issues array + change save-button label to "保存并提示重启" (or similar).
- After a save in degraded mode, render an explicit restart-required toast and link.

In `extension/popup/popup.html`:
- Add three banner `<div>` elements with stable IDs (`cfgBannerOffline`, `cfgBannerDegraded`, `cfgBannerNoCache`) and corresponding CSS classes (amber / red / red).

**Step 4: Run tests to verify pass**

```bash
cd extension && npm test
npm run typecheck
npm run build
```

**Step 5: Commit**

```
feat(extension): cache config snapshot and render offline + degraded settings UI
```

---

## Workstream E — Shared structured JSON coercion for mimo / non-OpenAI providers

### Task E1: Shared extraction helpers in `llm/json_utils.py`

**Files:**
- Modify: `src/openbiliclaw/llm/json_utils.py`
- Test: `tests/test_llm_json_utils.py`

**Step 1: Write failing tests**

Add tests for a new helper API:

```python
extract_llm_json_list(
    content: str,
    *,
    wrapper_keys: tuple[str, ...] = (),
    allow_singleton: bool = False,
    item_predicate: Callable[[dict[str, JSONValue]], bool] | None = None,
) -> list[dict[str, JSONValue]] | None
```

Cover:
- Root array: `'[{"score":0.8}]'` -> list.
- Wrapped arrays under `results`, `items`, `data`, `output`, `scores`, and caller-supplied aliases.
- Root single object when `allow_singleton=True` and predicate passes.
- Markdown fenced JSON.
- JSONL objects: `'{"score":0.8}\n{"score":0.7}'`.
- Echoed schema / prompt object(s) before a final fenced array; helper returns the final schema-valid array, not the echoed input.
- Malformed mimo root seen in logs: `'{\n  [\n    {"hypothesis":"h","evidence":["e"],"confidence":0.6}\n  ]\n}'` -> list.
- Predicate rejection: arrays without required semantic fields return `None`.

Also add `extract_llm_json_object(...)` tests for:
- Root object.
- Wrapped object under `result`, `item`, `data`, `output`.
- Echoed object before final object, with predicate selecting the final object.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_llm_json_utils.py -q
```

Expected: fail because the helpers do not exist yet.

**Step 3: Implement**

In `src/openbiliclaw/llm/json_utils.py`:
- Keep `parse_llm_json_tolerant` unchanged for callers that want the raw parsed container.
- Move the balanced-snippet extraction logic currently local to `discovery/engine.py` into json_utils as private helpers (`_extract_json_array_snippets`, `_extract_json_object_snippets`, `_extract_balanced_json_snippets`).
- Implement `extract_llm_json_list`:
  - Build candidate containers from `parse_llm_json_tolerant(content)`.
  - Add wrapped nested values from known wrapper keys plus caller keys.
  - Add JSON array snippets from the raw text, scanning from last to first so the real final answer wins over echoed prompt/schema.
  - Add JSON object snippets and JSONL lines, coercing single dicts when `allow_singleton=True`.
  - Return the first candidate list whose items are dicts and whose predicate passes for at least one item.
- Implement `extract_llm_json_object` with analogous wrapper and predicate logic.
- Keep all return values typed as JSON-compatible dicts/lists; do not return arbitrary objects.

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_llm_json_utils.py -q
ruff check src/openbiliclaw/llm/json_utils.py tests/test_llm_json_utils.py
mypy src/openbiliclaw/llm/json_utils.py
```

**Step 5: Commit**

```
feat(llm): add shared structured-output extraction helpers
```

---

### Task E2: Migrate recommendation parsers off raw `json.loads`

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Test: `tests/test_recommendation_engine.py`

**Step 1: Write failing tests**

Extend `tests/test_recommendation_engine.py`:
- `classify_pool_backlog` accepts JSONL classification output and classifies all rows.
- `classify_pool_backlog` accepts `{"results":[...]}` and `{"items":[...]}` classification output.
- Batch expression generation accepts `{"items":[{"expression":"...","topic_label":"..."}]}` without single-item fallback.
- Single expression generation accepts echoed schema before a final fenced object.
- Delight reason generation accepts `{"result":{"delight_reason":"...","delight_hook":"..."}}`.

Use `caplog` to assert these scenarios do not log `classify_pool_backlog: batch failed`, `Batch expression generation failed`, or `Failed to generate recommendation expression`.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_recommendation_engine.py -k "classify_pool_backlog or expression or delight_reason" -q
```

Expected: fail where the current code calls raw `json.loads(response.content.strip())`.

**Step 3: Implement**

In `recommendation/engine.py`:
- Import `extract_llm_json_list` and `extract_llm_json_object`.
- In `_classify_batch`, replace raw `json.loads(raw)` with `extract_llm_json_list(raw, wrapper_keys=("results", "items", "evaluations", "scores", "data"), allow_singleton=True, item_predicate=lambda item: "score" in item)`.
- In `_precompute_batch`, parse batch expression responses with `extract_llm_json_list(..., wrapper_keys=("results", "items", "expressions", "data"), allow_singleton=True, item_predicate=lambda item: "expression" in item or "topic_label" in item)`.
- In `_generate_expression`, parse with `extract_llm_json_object(..., wrapper_keys=("result", "item", "expression", "data", "output"), item_predicate=lambda item: "expression" in item or "topic_label" in item)`.
- In `_generate_delight_reason`, parse with `extract_llm_json_object(..., wrapper_keys=("result", "item", "data", "output"), item_predicate=lambda item: "delight_reason" in item or "delight_hook" in item)`.
- Keep existing fallback behavior when parsing returns `None`; do not fabricate missing `expression`, `topic_label`, `delight_reason`, or `delight_hook`.

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_recommendation_engine.py -k "classify_pool_backlog or expression or delight_reason" -q
ruff check src/openbiliclaw/recommendation/engine.py tests/test_recommendation_engine.py
mypy src/openbiliclaw/recommendation/engine.py
```

**Step 5: Commit**

```
fix(recommendation): tolerate mimo structured-output wrappers
```

---

### Task E3: Migrate soul / discovery / delight parser edges to the shared helper

**Files:**
- Modify: `src/openbiliclaw/soul/insight_analyzer.py`
- Modify: `src/openbiliclaw/soul/awareness_analyzer.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/recommendation/delight.py`
- Test: `tests/test_insight_analyzer.py`
- Test: `tests/test_awareness_analyzer.py`
- Test: `tests/test_discovery_engine.py`
- Test: `tests/test_delight_scorer.py`

**Step 1: Write failing / regression tests**

Add:
- `InsightAnalyzer` accepts `{"results":[{"hypothesis":"h","evidence":["e"],"confidence":0.6}]}`.
- `InsightAnalyzer` accepts JSONL hypotheses.
- `InsightAnalyzer` accepts echoed schema/rules objects before a final fenced array, matching `openbiliclaw (1).log:3947`.
- `InsightAnalyzer` accepts malformed `{ [ ... ] }` root, matching `openbiliclaw (1).log:2372`.
- `AwarenessAnalyzer` still accepts existing wrapper and singleton shapes after migration.
- `ContentDiscoveryEngine._evaluate_batch` still accepts fenced arrays, echoed prompt before final array, and NDJSON after the snippet helpers move.
- `recommendation.delight._extract_delight_entries` still accepts root arrays, wrappers, single dict, JSONL, and now `{"output":[...]}` / fenced wrappers.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_insight_analyzer.py tests/test_awareness_analyzer.py tests/test_discovery_engine.py tests/test_delight_scorer.py -q
```

Expected: new insight tests fail; regression tests should mostly pass before implementation and must remain green after migration.

**Step 3: Implement**

- In `InsightAnalyzer._parse_response`, call `extract_llm_json_list` with wrapper keys `("results", "items", "insights", "hypotheses", "data", "output", "list", "array")`, `allow_singleton=True`, and predicate requiring `"hypothesis"` or `"evidence"`.
- In `AwarenessAnalyzer._coerce_note_list`, reuse the shared helper or delegate to it while preserving current `_AWARENESS_WRAPPED_ARRAY_KEYS` and singleton-note behavior.
- In `discovery/engine.py`, import balanced-snippet helpers from `llm/json_utils.py` and remove local duplicate helpers.
- In `recommendation/delight.py`, replace local wrapper / JSONL parsing with the shared helper while preserving domain wrapper keys (`results`, `items`, `delights`, `scores`, `candidates`, etc.).

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_insight_analyzer.py tests/test_awareness_analyzer.py tests/test_discovery_engine.py tests/test_delight_scorer.py -q
ruff check src/openbiliclaw/llm/json_utils.py src/openbiliclaw/soul/ src/openbiliclaw/discovery/engine.py src/openbiliclaw/recommendation/delight.py
mypy src/openbiliclaw/llm/json_utils.py src/openbiliclaw/soul/insight_analyzer.py src/openbiliclaw/soul/awareness_analyzer.py
```

**Step 5: Commit**

```
fix(llm): reuse shared JSON coercion across insight discovery and delight
```

---

## Workstream F — Ollama embedding warning hygiene

### Task F1: Suppress misleading back-compat warning for local Ollama embedding

**Files:**
- Modify: `src/openbiliclaw/llm/registry.py`
- Test: `tests/test_llm_registry.py`

**Step 1: Write failing tests**

Add tests:
- `EmbeddingConfig(provider="ollama", model="bge-m3", api_key="", base_url="")` and empty `[llm.ollama]` block builds an Ollama-backed embedding service using `http://localhost:11434/v1`, and `caplog` contains no `back-compat` warning.
- Same but `[llm.ollama].base_url="http://localhost:11434"` -> service base URL ends with `/v1`, no `back-compat` warning.
- `EmbeddingConfig(provider="openai", api_key="", base_url="")` plus `[llm.openai].api_key="sk-test"` still emits exactly one `back-compat` warning.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_llm_registry.py -k "embedding" -q
```

Expected: the Ollama no-warning test fails because `_emit_embedding_compat_warning("ollama")` fires before the Ollama branch.

**Step 3: Implement**

In `_build_dedicated_embedding_provider`:
- Do not call `_emit_embedding_compat_warning` before candidate-specific handling.
- For `candidate == "ollama"`:
  - Treat empty `api_key` as valid.
  - Use `emb_cfg.base_url`, else `config.llm.ollama.base_url`, else `http://localhost:11434/v1`.
  - Normalize to `/v1`.
  - Never emit the back-compat warning for this path.
- For remote providers, keep the warning when all of these are true:
  - user explicitly requested that provider in `[llm.embedding]`;
  - embedding-specific `api_key/base_url` are empty;
  - construction borrows chat-side credentials.

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_llm_registry.py -k "embedding" -q
ruff check src/openbiliclaw/llm/registry.py tests/test_llm_registry.py
mypy src/openbiliclaw/llm/registry.py
```

**Step 5: Commit**

```
fix(llm): stop warning for credentialless ollama embedding
```

---

## Workstream G — File-handler traceback preservation

### Task G1: Regression tests for file tracebacks

**Files:**
- Modify: `src/openbiliclaw/logging_setup.py`
- Test: `tests/test_logging_setup.py`
- Test: `tests/test_api_config_transactional.py` (extend after Workstream B exists)

**Step 1: Write failing tests**

In `tests/test_logging_setup.py`:
- Configure logging with `RotatingFileHandler`, raise/catch `ValueError("sentinel")`, call `logging.getLogger("openbiliclaw.test").exception("sentinel exception")`, flush file handlers, assert file contains the message, `Traceback (most recent call last)`, and `ValueError: sentinel`.
- Repeat with `max_file_size_mb=0` so `_build_file_handler` uses plain `logging.FileHandler`.

In `tests/test_api_config_transactional.py`:
- Configure logging to a temp file.
- Monkeypatch `RuntimeContext.rebuild_from_config` to raise `RuntimeError("simulated hot reload crash")`.
- Trigger `PUT /api/config`, flush file handlers, assert the log file contains `Config hot-reload failed`, `Traceback (most recent call last)`, and `RuntimeError: simulated hot reload crash`.

**Step 2: Run tests to verify failure**

```bash
pytest tests/test_logging_setup.py tests/test_api_config_transactional.py -q
```

Expected: if current formatter already preserves tracebacks, the low-level logging tests may pass; the API integration test is the required guard for the raw-log gap.

**Step 3: Implement**

If any traceback assertion fails:
- Add `_ExceptionPreservingFormatter(logging.Formatter)` in `logging_setup.py`.
- Its `format()` calls `super().format(record)` and, when `record.exc_info` is present but the rendered string lacks `Traceback (most recent call last)`, appends `formatException(record.exc_info)`.
- Use it only for file handlers in `_build_file_handler`; keep `RichHandler` unchanged.

If the tests already pass, keep implementation changes minimal: add the tests and document that standard `logging.Formatter` is the contract.

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_logging_setup.py tests/test_api_config_transactional.py -q
ruff check src/openbiliclaw/logging_setup.py tests/test_logging_setup.py tests/test_api_config_transactional.py
mypy src/openbiliclaw/logging_setup.py
```

**Step 5: Commit**

```
test(logging): lock file traceback preservation for exception logs
```

---

## Workstream H — Documentation & Release

### Task H1: Docs sync

**Files:**
- Modify: `docs/modules/api.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/soul.md`
- Create or modify: `docs/modules/runtime.md`
- Modify: `docs/index.md` (only if `docs/modules/runtime.md` is created)
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md` (§3 system architecture)
- Modify: `README.md` top architecture diagram (if Workstream D lands in this PR)
- Modify: `README_EN.md` top architecture diagram (if Workstream D lands in this PR)
- Modify: `README.md` (📌 highlights callout — replace previous version's, ≤4 bullets)
- Modify: `README_EN.md` (mirror highlights — same bullet count, order, content)
- Modify: `docs/changelog.md` (new version entry)

**Step 1: Update each doc**

- `api.md`: new PUT response fields `rollback_applied`, `restart_required`; structured 400 response shape; degraded-mode endpoint contracts; `GET /api/config.degraded`; `config.toml.bak` semantics.
- `config.md`: empty-value preservation contract; mask-value contract; explicit `reset_fields`; degraded-mode boot fallback; validate-before-write guarantee.
- `extension.md`: popup config cache key; offline banner; degraded banner.
- `cli.md`: `openbiliclaw start` degraded-mode panel.
- `llm.md`: shared structured-output coercion helper contract; Ollama embedding warning semantics.
- `recommendation.md`: classify / expression / delight structured-output tolerance and fallback behavior.
- `discovery.md`: batch-evaluation structured-output tolerance if the shared helper changes parser internals.
- `soul.md`: awareness / insight structured-output tolerance.
- `runtime.md`: auto-updater tag contract (`backend-v*` tags only; extension releases ignored) and degraded-runtime overview.
- `architecture.md`: degraded-runtime branch in the boot data flow and transactional config-save flow.
- `docs/spec.md` §3: mirror the architecture/data-flow update.
- `README.md` / `README_EN.md` top architecture diagrams: mirror the degraded-mode branch if the PR includes Workstream D.
- `README.md` / `README_EN.md`: ≤4 bullets, CN/EN in sync, replace previous version's callout per CLAUDE.md hard rules. Cover "auto-update fix", "popup can now recover from bad LLM config", "transactional config save", and "mimo/Ollama/logging reliability"; fold any `docs/changelog.md` pointer into the final bullet instead of adding a fifth bullet.
- `changelog.md`: top entry `## v0.3.NN: <theme> (YYYY-MM-DD)` listing all remediation workstreams.

**Step 2: Verify links and version refs**

Grep for references to stale updater wording, singular `reset_field`, "model config dropped", old batch-JSON follow-up slugs, and old scope-exclusion promises — ensure docs reflect the new behaviour and do not document discarded designs or obsolete scope splits.

**Step 3: Commit**

```
docs: sync API + config + extension + CLI + README for config-deadlock fix
```

---

### Task H2: Pre-release smoke

**Files:** none (operational only)

**Step 1: Local smoke**

- `openbiliclaw start` with a valid config — confirm normal boot.
- Edit `config.toml` to set every provider's `api_key = ""`. Restart — confirm degraded-mode boot panel appears, `GET /api/health` returns degraded JSON.
- Open the extension popup gear, confirm form populates (from server in degraded mode; the issues list appears). Type a valid OpenAI api_key, click Save — confirm popup tells user to restart. Restart — confirm normal boot.
- Stop the daemon entirely. Open the popup gear again — confirm offline banner with cached snapshot timestamp.
- With daemon running, hit Save with a payload that wipes a key (sending `""` for `llm.openai.api_key`) — confirm key is preserved per Workstream C.
- Spy on `_fetch_latest_version` (or stub the GitHub API) — confirm no `Already up-to-date: ... remote=extension-v...` line; either a real backend tag or the new INFO `no_backend_tag_yet`.
- Run structured-output fixture smokes for mimo shapes: `classify_pool_backlog` JSONL, `InsightAnalyzer` echoed schema + fenced array, and delight `{"results":[...]}` all parse without error.
- Start with `[llm.embedding].provider="ollama"` and empty embedding credentials — confirm no back-compat warning appears while embedding service still constructs.
- Trigger a simulated hot-reload exception and confirm the file log contains a traceback.

**Step 2: Tag + release**

- Bump `pyproject.toml` `version` to the next backend version (e.g. `0.3.73`).
- Tag with `backend-v0.3.73`.
- Push tag; no release artifact needed (per source-only policy).

**Step 3: No commit needed** (operational).

---

## Order of Implementation

The remediation workstreams are mostly independent in code but have a recommended order:

1. **Workstream C (mask + empty guard + explicit reset)** — smallest blast radius, ships first; immediately stops new occurrences of "save with empty form wipes my keys" and gives B a deterministic way to test intentional clearing.
2. **Workstream B (transactional PUT + popup 400 rendering)** — depends on C's `reset_fields` test fixture and edits the same handler; ship right after.
3. **Workstream A (auto-updater)** — fully independent; can ship in parallel with B/C. Highest long-term value (prevents the original drift that triggered the whole incident).
4. **Workstream E (shared structured JSON coercion)** — independent of config writes and immediately cuts the repeated mimo background failures in the raw logs.
5. **Workstream F (Ollama embedding warning)** and **Workstream G (file traceback preservation)** — small, isolated reliability fixes; can ship in either order after tests are added.
6. **Workstream D (degraded mode + popup fallback)** — largest change; ships after the lower-risk fixes so the recovery surface is built on quieter logs and safer config writes. Each task (D1, D2, D3) ships as its own commit.
7. **Workstream H** — docs land alongside the version bump that includes all workstreams. If splitting into separate PRs, do not defer all docs to H: each PR still needs scoped module docs and a `docs/changelog.md` entry before merge.

## Verification Checklist

- [ ] `pytest tests/test_runtime_updater.py -q` passes (Workstream A).
- [ ] `pytest tests/test_api_config_transactional.py tests/test_api_config_guards.py tests/test_api_degraded_mode.py -q` passes (Workstreams B/C/D).
- [ ] `pytest tests/test_llm_json_utils.py tests/test_insight_analyzer.py tests/test_awareness_analyzer.py tests/test_discovery_engine.py tests/test_delight_scorer.py tests/test_recommendation_engine.py -q` passes (Workstream E).
- [ ] `pytest tests/test_llm_registry.py -k "embedding" -q` passes (Workstream F).
- [ ] `pytest tests/test_logging_setup.py tests/test_api_config_transactional.py -q` passes (Workstream G plus B transactional coverage).
- [ ] `cd extension && npm test -- popup-api.test.ts popup-settings.test.ts` passes (Workstreams B2/D3 targeted extension coverage).
- [ ] `pytest tests/ -q` full suite green.
- [ ] `cd extension && npm test && npm run typecheck && npm run build` green (Workstream D3).
- [ ] `ruff check src/ tests/` clean.
- [ ] `mypy src/` clean.
- [ ] Local smoke per Task H2 done end-to-end.
- [ ] README CN/EN highlights replaced (not appended), ≤4 bullets each, content in sync.
- [ ] `docs/changelog.md` top entry mentions all remediation workstreams.
- [ ] `docs/architecture.md`, `docs/spec.md` §3, and README CN/EN architecture diagrams agree if Workstream D is included.
- [ ] No references to "Already up-to-date: ... remote=extension-v" in fresh daemon log after a tick.
- [ ] `backend-v0.3.73` (or next) tag pushed.

## Out-of-Scope Reminders

The raw-log-adjacent issues are now covered by Workstreams E/F/G. Still out of scope for this plan:

- Fully offline popup write queue when the daemon is not running at all.
- Changing backend release policy away from source-only `backend-v*` git tags.
- Recovering API keys already erased before this release; users must re-enter those secrets once.
