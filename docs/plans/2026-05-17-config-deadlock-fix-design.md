# Config Deadlock Fix Design

## Goal

Eliminate the four-step failure chain that left a real user with "model config dropped, backend can't start, extension settings can't help" after running `git pull` past a schema-additive release. End state: ordinary bad config saves are rejected or rolled back without a restart; if the backend starts in degraded mode, the user can repair config through the extension popup without editing TOML, then perform one explicit daemon restart to leave degraded mode.

## Problem Statement

A user reported on 2026-05-16: 「前端管理后端的功能有点奇怪，我昨天更新之后，之前的模型配置掉了，后端启动不了，插件设置那里因为后端没启动不给我配置。」

Reading the raw attachments shows three directly observed config-deadlock failures, one code-derived recovery gap, and three adjacent reliability problems that surfaced in the same session:

### Evidence ledger

- `agent-bootstrap.log:49-53`, `:349-353`, and `:764-768` show three separate checks hitting `GET /releases/latest` successfully and logging `Already up-to-date: current=0.3.64, remote=extension-v0.3.20`.
- `copilot对话.txt:14-24` shows the later manual fast-forward to latest `main`, pulling tags including `backend-v0.3.69` and `extension-v0.3.24`, so the real backend source had moved past the running `0.3.64`.
- `copilot对话.txt:96-145` shows manual LLM repair outside the popup: the user moved to `openai_compatible` with `base_url=https://token-plan-sgp.xiaomimimo.com/v1`, `model=mimo-v2.5-pro`, then later supplied the API key and switched embedding to local Ollama.
- `openbiliclaw (1).log:619-627` shows a config save at `21:55:18`, immediately followed by hot-reload cancellation and `Config hot-reload failed — old components remain active`. There is no rollback line before or after it.
- `openbiliclaw (1).log:1242-1250` shows a later config save that hot-reloaded successfully enough to register `openai_compatible`, which matches the Copilot manual-repair sequence.
- `openbiliclaw (1).log:1722-1727` shows the same updater bug after the manual update: `/releases/latest` returned `extension-v0.3.24`, and the backend still logged "Already up-to-date" while running `0.3.64`.
- `openbiliclaw (1).log:8` shows noisy embedding fallback guidance even when the configured embedding provider is local Ollama: `[llm.embedding] api_key/base_url is empty — falling back to [llm.ollama] credentials`.
- `openbiliclaw (1).log:581`, `:3930`, `:6917`, and `:6967` show repeated `Delight LLM batch produced 0 parseable entries ... (provider response shape mismatch?)` against the mimo endpoint.
- `openbiliclaw (1).log:1234`, `:1699`, `:1778`, and many later lines show `classify_pool_backlog: batch failed`; `agent-bootstrap.log:70749-70799` and many later tracebacks narrow this to `JSONDecodeError: Extra data`.
- `openbiliclaw (1).log:2372`, `:3947`, `:4751`, `:5123`, `:5833`, `:6647`, and `:6989` show `soul.insight_analyzer` repeatedly failing to parse mimo responses that include wrapper objects, echoed schema/rules, malformed `{ [` roots, or newline-separated JSON objects before / around the real array.
- The attached `openbiliclaw (1).log` does **not** contain `RegistryBuildError`, `No LLM providers are available`, or a traceback for the `Config hot-reload failed` line. The degraded-mode requirement is therefore based on the user's symptom ("backend can't start; popup settings can't help") plus the current startup code path, not on a direct traceback in the file log.

1. **Auto-updater silently broken.** `runtime/updater.py:42 _parse_version("extension-v0.3.20")` returns `(0,)` because `lstrip("vV")` does not strip the `"extension-v"` prefix and the first split segment fails `int()`. The user's auto-update loop logged `Already up-to-date: current=0.3.64, remote=extension-v0.3.20` three times on 2026-05-15, comparing `(0,) <= (0, 3, 64) == True`. After manual update on 2026-05-16, it repeated the same false conclusion for `remote=extension-v0.3.24`. Compounded by the current release policy — backend source updates are git tags, while GitHub Releases are extension artifacts — `GET /releases/latest` returns an extension tag every time. The user was several backend tags stale by the time they manually `git pull`-ed, accumulating multiple additive config schema changes (`[soul.preference]`, `[sources.douyin]`, `[sources.youtube]`, `[scheduler.pool_source_shares]`) in one jump.

2. **`PUT /api/config` writes before validating / reloading.** `src/openbiliclaw/api/app.py:3580-3604` order is `save_config(cfg)` -> `ctx.rebuild_from_config(cfg)`. When `rebuild_from_config` raises, the on-disk `config.toml` has already been overwritten; the runtime keeps old in-memory components, so the user sees "looks fine" but the next process restart loads the broken config. The raw log proves the bad ordering at `openbiliclaw (1).log:619-627`: "Configuration saved" is logged before "Config hot-reload failed", and no rollback is attempted.

3. **`PUT /api/config` for chat providers has no masked-value / empty-value guard.** Embedding handling at `src/openbiliclaw/api/app.py:3441-3449` skips API-key writes containing `*` (correct: masked-key echo from a masked GET). The chat-provider loop at lines `3413-3434` blindly `setattr`s every present field including empty strings. This exact bad payload is not visible in the raw logs, but it is the code path that can turn the user's "model config dropped" complaint into an on-disk credential wipe if the popup saves an unpopulated or stale form.

4. **Backend startup has no recovery surface for an unbuildable LLM registry.** `src/openbiliclaw/llm/registry.py:79` raises `RegistryBuildError("No LLM providers are available...")` when zero providers are constructible. In the current production path, that propagates through `RuntimeContext._rebuild_components` -> `build_runtime_context` -> `create_app`, so uvicorn exits before `/api/health` or `/api/config` can serve. The raw attachments do not include this traceback; this is the code-level explanation for the user's reported "backend can't start, so extension settings can't help" state. The popup's settings drawer (`extension/popup/popup.js:4272-4277`) currently calls `fetchConfig()` on gear-open and falls back to a toast `"无法加载配置，请确认后端已启动。"` without a recovery form.

5. **mimo-style structured JSON still breaks multiple downstream tasks.** Current code already has partial tolerance: `AwarenessAnalyzer._coerce_note_list`, `recommendation.delight._extract_delight_entries`, and `discovery._parse_batch_evaluation_payload` cover some wrapper / NDJSON shapes. The logs prove the coverage is still incomplete. `recommendation.engine._classify_batch` and expression / reason helpers still call raw `json.loads(response.content.strip())`, and `InsightAnalyzer._parse_response` accepts only a root list. This turns valid-enough model output into repeated background failures instead of best-effort structured results.

6. **Local Ollama embedding emits misleading back-compat warnings.** Ollama does not require an API key, and an empty `[llm.embedding].base_url` can be safely defaulted to `http://localhost:11434/v1` or borrowed from `[llm.ollama].base_url`. The current `_build_dedicated_embedding_provider` calls `_emit_embedding_compat_warning("ollama")` before the Ollama-specific logic, so a correct local embedding setup looks like a deprecated credential fallback.

7. **File logs must prove exception tracebacks are retained.** `openbiliclaw (1).log:627` has the `logger.exception("Config hot-reload failed ...")` line but no nearby traceback, while `agent-bootstrap.log` contains Rich-rendered tracebacks. Python's `logging.Formatter` should append `exc_info` to file handlers, so this may be handler configuration, capture/truncation, or a Windows path. This spec includes a regression test and a formatter guard so future file logs always contain `Traceback (most recent call last)` for `logger.exception` events.

## Current Gaps

- `_parse_version("extension-v0.3.20")` silently returns `(0,)` and is never reached as a code path during release cuts because backend bumps don't trigger CI release publication; the bug is invisible to maintainers.
- `_fetch_latest_version` calls `/releases/latest` first and only falls back to `/tags` when the HTTP call itself fails. With backend-source-only release policy, `/releases/latest` always succeeds and returns the extension tag, so `/tags` fallback is dead code.
- `update_config` has no transactional discipline: no dry-run validation against the candidate config, no backup of the prior file, no rollback path. The hot-reload exception block at `api/app.py:3602-3604` just logs and appends a message; the file is already gone.
- The chat-provider PUT loop and the embedding PUT block are structurally divergent (different field-by-field handling, different masked-value semantics). The embedding logic is correct; the chat logic is not.
- `build_runtime_context` is the only constructor for the FastAPI app. If it raises, uvicorn never gets a chance to serve `/api/health` or `/api/config`, so there is no UI-reachable recovery channel.
- The popup gear button has only one code path: HTTP fetch from the backend. There is no local cache, no offline edit mode, no read of a last-known-good config snapshot, no separation between "backend is unreachable" and "backend rejected this PUT".
- Structured LLM parsing is inconsistent across modules. `llm/json_utils.py` already centralizes tolerant JSON parsing, but callers still reimplement local unwrapping or use raw `json.loads`, so fixes do not propagate evenly.
- Embedding warning semantics conflate "remote provider is borrowing old chat credentials" with "Ollama is local and credentialless by design".
- File logging has tests for basic writes and rotation, but not for `logger.exception(..., exc_info=True)` traceback preservation.

## Chosen Approach

Seven remediation workstreams plus docs/release. A-D close the config-deadlock chain; E-G close the adjacent raw-log failures so the same session's LLM, embedding, and logging problems do not survive as follow-up specs. Each workstream is independently shippable, but this combined spec treats them as one release package.

### A. Auto-updater fix (P0)

`_parse_version` must stop treating extension-tag inputs as version zero. `_fetch_latest_version` must ignore `/releases/latest` and filter paginated `/tags` results for backend tags. Document the release-policy -> updater contract in repo docs and in `runtime/updater.py`; local Claude memory can mention it, but it must not be the only source of truth.

Concretely:
- Add `_BACKEND_TAG_PREFIX = "backend-v"`. Add `_parse_backend_version(tag) -> tuple[int, ...] | None` that returns `None` for non-backend tags.
- `_fetch_latest_version` returns the highest backend version from `/tags` instead of trusting `/releases/latest`. Query `per_page=100` and paginate up to a small fixed budget (for example 5 pages) so a run of extension tags cannot hide the newest backend tag.
- If no backend tag is found in `/tags`, return `""` and log INFO; do not raise.

Out of scope: changing the backend release policy itself. That stays source-only by user-stated convention.

### B. Transactional `PUT /api/config` (P0)

Re-order `update_config` to: validate → snapshot → write → reload → on-failure-rollback. Concretely:

1. `_collect_config_issues(cfg)` already exists. Tighten it so issues that would cause `build_llm_registry` to fail are classified as `blocking` and short-circuit the save with a 400 response carrying a full `ConfigUpdateResponse`-shaped JSON body (`ok=false`, candidate config snapshot, issues, message, `reloaded=false`). The popup must parse non-2xx JSON bodies and render the issues instead of only showing `request failed: 400`.
2. Before `save_config(cfg)`, copy the existing `config.toml` to `config.toml.bak` (overwriting any older bak). This is the rollback fixture.
3. After `save_config(cfg)`, attempt `ctx.rebuild_from_config(cfg)`. On exception: restore `config.toml` from `.bak`, log with the full exception (`logger.exception`), return the response with `reloaded=False` and an explicit `rollback_applied=True` flag so the popup can render an actionable error and instruct the user that the on-disk config has been restored.
4. The response schema for `ConfigUpdateResponse` gains `rollback_applied: bool = False` and `restart_required: bool = False`. Popup toast switches color: green = saved + reloaded; amber = saved but not reloaded and restart required; red = save rejected (validation failure) OR save attempted and rolled back.

Tightly scoped: no new dry-run sandbox runtime, no double-construction of swappable components. The pre-save validation is the existing `_collect_config_issues` plus a new `_validate_llm_buildable(cfg)` that exercises `build_llm_registry(cfg)` in a try/except without binding the result to anything. `build_llm_registry` is already side-effect-free except for log lines.

### C. Mask + empty-value guards for chat-provider PUT (P0)

Mirror the embedding block's logic onto the chat-provider loop. Pseudocode replacement for `api/app.py:3386-3397`:

```python
for provider_name in (...):
    if provider_name not in llm_data or not isinstance(llm_data[provider_name], dict):
        continue
    provider_cfg = getattr(cfg.llm, provider_name)
    pdata = llm_data[provider_name]
    for field_name in ("api_key", "model", "base_url", "http_referer", "x_title", "reasoning_effort"):
        if field_name not in pdata:
            continue
        new_value = str(pdata[field_name])
        if field_name == "api_key" and "*" in new_value:
            # Masked-value echo; ignore so we don't overwrite the real key.
            continue
        existing = getattr(provider_cfg, field_name, "")
        if not new_value.strip() and existing.strip():
            # Empty payload field should not erase an existing populated value.
            # Use explicit unset endpoint if the user actually wants to clear it.
            continue
        setattr(provider_cfg, field_name, new_value)
```

Add an explicit reset channel for the rare case where a user genuinely wants to clear a value. Use `reset_fields: list[str]` in the JSON body (preferred over a query parameter because tests and popup saves already use JSON) and allowlist exact paths such as `llm.openai.api_key`, `llm.openai.model`, `llm.openai_compatible.api_key`, and `llm.embedding.api_key`. The popup does not need to use it initially; backend just stops silently destroying state. Workstream B uses this explicit reset in its validation tests so the C -> B ordering remains deterministic.

### D. Backend degraded mode + popup offline-recovery (P1)

If `build_llm_registry` raises during FastAPI initial app construction, `create_app` catches the `RegistryBuildError` and falls through to a **minimal-mode** RuntimeContext that exposes only:

- `GET /api/health` — returns `{"status": "degraded", "reason": "llm_registry_unavailable", "issues": [...]}`
- `GET /api/config` and `PUT /api/config` — backed by `load_config()` / `save_config()` directly, **not** through a runtime context (no hot-reload). `GET /api/config` includes explicit `degraded=true`, `degraded_reason`, and `issues` fields in `ConfigResponse`. After a successful PUT in degraded mode, the response message tells the user to restart the daemon to leave degraded mode. (Triggering a full rebuild from inside a degraded mode is possible but adds rebuild complexity; restart instruction is the minimum-viable path.)
- `GET /api/runtime-status` — returns degraded with the same issues array.
- The `/api/runtime-stream` WebSocket accepts connections but only publishes a single `{"type": "degraded", "issues": [...]}` event then idle-waits (so the popup's existing reconnect logic doesn't spin).

All other endpoints return 503 with a JSON body pointing to the degraded reason.

Popup-side: `popup.js` gear-button handler currently calls `fetchConfig()` and toasts on failure. Add a fallback path: when `fetchConfig()` returns a degraded-mode response (HTTP 200 but `issues` present and `degraded=true` flag), or when it returns a non-2xx, render the settings form populated from a **last-known-good cached config** stored in `chrome.storage.local` after every successful fetch. On save in this state, the PUT can still succeed (degraded mode honors PUT), and the popup surfaces the "restart the daemon" message returned by the backend.

Out of scope for this workstream: a fully offline-first popup that queues edits when the backend is wholly unreachable. The degraded mode covers the common case (process is running but registry is bad). A wholly-down backend remains a "start the daemon" problem.

### E. Shared structured-JSON coercion for mimo / non-OpenAI providers (P1)

Extend `llm/json_utils.py` from "parse tolerant JSON container" to "extract the intended list/object from messy LLM output":

- Add shared helpers for wrapped arrays (`results`, `items`, `data`, `output`, domain aliases), wrapped objects, fenced JSON, echoed prompt/schema before the real payload, malformed `{ [ ... ] }` roots, and newline-separated JSON objects.
- Migrate `recommendation.engine._classify_batch`, `_precompute_batch`, `_generate_expression`, and `_generate_delight_reason` off raw `json.loads`.
- Migrate `InsightAnalyzer._parse_response` to accept wrapper arrays, single hypothesis dicts, echoed schema plus final fenced array, malformed `{ [ ... ] }`, and JSONL objects.
- Keep existing `recommendation.delight._extract_delight_entries`, `discovery._parse_batch_evaluation_payload`, and `AwarenessAnalyzer` behavior, but move common shape coercion into `llm/json_utils.py` so future fixes are single-source.

Acceptance target: no raw `JSONDecodeError: Extra data` / `provider response shape mismatch` loop for the shapes seen in the attachments. Invalid or schema-empty output still fails explicitly; we are not guessing semantic fields that are absent.

### F. Ollama embedding warning hygiene (P2)

When `[llm.embedding].provider == "ollama"`:

- Empty `api_key` is normal and must not emit the back-compat warning.
- Empty `[llm.embedding].base_url` defaults to `http://localhost:11434/v1`, unless `[llm.ollama].base_url` is set, in which case that value is borrowed silently and normalized to `/v1`.
- The back-compat warning remains for remote providers (`openai`, `gemini`, `openai_compatible`) when embedding-specific credentials are empty and chat-side credentials are borrowed.

### G. File-handler traceback preservation (P2)

Add regression coverage that configures file logging, emits `logger.exception(...)`, flushes the file handler, and asserts the log file contains:

- the application log message;
- `Traceback (most recent call last)`;
- the exception class and message.

Run it for both `RotatingFileHandler` and plain `FileHandler` (`max_file_size_mb=0`). If the standard formatter ever stops appending exception text, wrap it with a small `ExceptionPreservingFormatter` guard in `logging_setup.py`.

## Data Flow

### Normal save flow (post-fix)

1. User edits settings in popup, clicks Save.
2. Popup `PUT /api/config` with the form payload.
3. `update_config` handler builds candidate `cfg`, applies any explicit `reset_fields`, then calls `_validate_llm_buildable(cfg)`. On blocking error, returns HTTP 400 with a `ConfigUpdateResponse`-shaped JSON body so the popup can render field-level issues.
4. Handler writes `config.toml.bak`, then `save_config(cfg)`.
5. Handler calls `ctx.rebuild_from_config(cfg)`. On exception, restores `.bak` over `config.toml`, returns 200 with `reloaded=False, rollback_applied=True`.
6. On success, returns 200 with `reloaded=True, rollback_applied=False`, `issues=[...]` (non-blocking diagnostics still pass through).
7. Popup updates `chrome.storage.local["openbiliclaw.config_cache"]` with the returned `result.config` snapshot for future degraded-mode use.

### Auto-update flow (post-fix)

1. `AutoUpdateService.check_and_update_now` calls `_fetch_latest_version`.
2. `_fetch_latest_version` queries `/tags` (paginated, capped), filters for backend tags (including legacy bare `v0.3.x` / `0.3.x`), returns the highest by `_parse_backend_version`.
3. Caller compares parsed remote vs `openbiliclaw.__version__`. If higher, applies update; if same/lower, logs `Already up-to-date`. If `_fetch_latest_version` returned `""`, logs INFO `no_backend_tag_yet` instead of falsely claiming up-to-date.

### Degraded-mode boot (post-fix)

1. `create_app` calls `build_runtime_context(cfg)`.
2. If `build_runtime_context` raises `RegistryBuildError`, `create_app` catches it and builds a `DegradedRuntimeContext` with only the database + memory_manager + event_hub. Direct `build_runtime_context` callers remain strict by default.
3. FastAPI app registers the degraded route handlers (or the regular handlers detect `ctx.degraded == True` and short-circuit appropriately).
4. uvicorn serves `/api/health`, `/api/config`, `/api/runtime-status`, `/api/runtime-stream`.
5. Popup opens, fetches config, renders form populated from server (or last-known-good cache), user fixes credentials, clicks Save.
6. `PUT /api/config` in degraded mode writes file and tells user to restart. User restarts `openbiliclaw start`; new boot constructs normal runtime context successfully.

### Structured LLM output flow (post-fix)

1. Each structured task still asks the provider for the narrow schema it expects.
2. The caller passes raw text to a shared `llm/json_utils.py` extraction helper with expected root type and wrapper keys.
3. Helper normalizes fences, echoed prompt/schema, wrapper objects, malformed `{ [ ... ] }` roots, and JSONL roots into the caller's expected list/object.
4. Caller validates per-item required fields (`score`, `expression`, `hypothesis`, `bvid`, etc.) and applies its existing fallback only when no schema-valid item can be recovered.

### Ollama embedding flow (post-fix)

1. `build_embedding_service` sees `requested_name == "ollama"`.
2. It builds a dedicated `OllamaProvider` using `[llm.embedding].base_url`, else `[llm.ollama].base_url`, else `http://localhost:11434/v1`.
3. It never asks for or warns about missing API keys on this path.
4. Remote-provider back-compat warnings are unchanged.

## Error Handling

- **Auto-updater:** unknown tag formats (anything not `backend-v\d+\.\d+\.\d+`, legacy `v\d+\.\d+\.\d+`, or legacy bare `\d+\.\d+\.\d+`) are skipped and logged at DEBUG. `/tags` HTTP failure logs WARN once per check cycle; do not retry within the cycle.
- **Transactional PUT:** blocking validation failure -> abort the save, return HTTP 400 with parseable JSON details; popup renders issues inline. `.bak` write failure -> abort the save, return 500 with explicit "couldn't snapshot config, refusing to risk overwrite". `.bak` restore failure after a hot-reload exception -> log CRITICAL, return 500 with "manual recovery required" pointing at `config.toml.bak`. `.bak` cleanup never deletes the file (it's the next save's snapshot fixture).
- **Mask/empty guard:** the per-field continue is silent (no log spam); the popup's existing "saved" toast already gives feedback. Add a single DEBUG log line listing which fields were skipped due to mask/empty to aid future debugging.
- **Degraded mode:** the degraded `/api/config` PUT path explicitly does NOT call `rebuild_from_config` (the registry can't be built — that's why we're degraded). The response message instructs restart. The degraded `/api/runtime-stream` emits the degraded event once; if it tried to publish further events during degraded mode, the popup would interpret state changes that don't reflect any actual runtime. Stay quiet.
- **OpenBiliClaw non-server / direct `build_runtime_context` callers:** continue to raise on `RegistryBuildError` unless they explicitly opt into `build_degraded_runtime_context`. The `openbiliclaw start` server path is the exception: it calls `create_app`, so it can boot the degraded FastAPI app and print a panel pointing the user at `openbiliclaw config-show` and the popup-fix-flow.
- **Structured JSON coercion:** tolerate transport/format wrappers only; keep schema validation strict. If a recovered list item is not a dict or lacks all required fields, skip that item and log a compact parse diagnostic with head/tail. Do not silently fabricate scores, expressions, or hypotheses.
- **Embedding warnings:** suppress only the Ollama credentialless path. If `openai` / `openai_compatible` / `gemini` borrows chat-side credentials for embedding, keep the once-per-process WARNING.
- **File tracebacks:** if the file formatter cannot prove a traceback was emitted in tests, the formatter guard appends `formatException(record.exc_info)` exactly once.

## Testing

### Workstream A — Auto-updater

- Unit: `_parse_backend_version("backend-v0.3.71") == (0, 3, 71)`.
- Unit: `_parse_backend_version("extension-v0.3.24") is None`.
- Unit: `_parse_backend_version("v0.3.71") == (0, 3, 71)` (legacy bare-version support).
- Unit: `_parse_backend_version("backend-vfoo") is None`.
- Integration with stubbed httpx: `_fetch_latest_version` against a `/tags` response containing mixed backend / extension tags returns the highest backend tag.
- Integration with stubbed httpx: backend tag appears on page 2 after page 1 contains only extension tags -> `_fetch_latest_version` still returns the backend tag.
- Integration: `/tags` returns only extension tags → returns `""`.
- Integration: `/tags` returns empty list → returns `""`, no crash.
- Integration: HTTP error from `/tags` → returns `""`, logs WARN.
- Regression: `check_and_update_now` with current=`"0.3.64"` and remote tag `"extension-v0.3.20"` does NOT log "Already up-to-date" but instead logs the no-backend-tag-yet INFO.

### Workstream B — Transactional PUT

- Unit: `_validate_llm_buildable(cfg)` returns `True` for a valid cfg; raises `ConfigValidationError` carrying the registry-build error for a cfg with all api_keys empty.
- Integration with TestClient: `PUT /api/config` with `reset_fields=["llm.openai.api_key"]` against a config where OpenAI is the only constructible provider -> 400 with parseable issues array; `config.toml` on disk unchanged.
- Extension/API unit: `requestJson` preserves non-2xx JSON bodies so the settings save handler can render `issues` from a 400 response instead of reducing it to `request failed: 400`.
- Integration: `PUT /api/config` with a valid payload but `ctx.rebuild_from_config` is monkeypatched to raise → response is 200 with `reloaded=False, rollback_applied=True`; `config.toml` on disk reverted to the pre-PUT content; `config.toml.bak` exists.
- Integration: successful save → `reloaded=True, rollback_applied=False`, `config.toml` updated, `config.toml.bak` is the previous content.
- Concurrency: two parallel `PUT /api/config` calls — the second one sees the first one's snapshot as its `.bak` (no .bak file corruption from interleaved writes). Use an asyncio lock around save+reload to guarantee.

### Workstream C — Mask / empty guard

- Unit: `update_config` payload `{"llm": {"openai": {"api_key": "sk-d****a826"}}}` against an existing real key → real key preserved.
- Unit: payload `{"llm": {"openai": {"api_key": ""}}}` against an existing real key → real key preserved.
- Unit: payload `{"llm": {"openai": {"api_key": "sk-new-real-key"}}}` → real key written.
- Unit: payload `{"llm": {"openai": {"model": ""}}}` against existing `"gpt-4o-mini"` → model preserved.
- Unit: payload `{"llm": {"openai": {"model": "gpt-4.1-mini"}}}` → model written.
- Unit: payload with `reset_fields=["llm.openai.api_key"]` explicitly clears the allowlisted field.
- Unit: unknown reset path (for example `storage.db_path`) is rejected with 400 and does not mutate config.

### Workstream D — Degraded mode + popup fallback

- Integration: `build_runtime_context(cfg_without_providers)` still raises `RegistryBuildError` by default, proving direct callers remain strict.
- Integration: `create_app()` with a cfg that has no constructible providers catches that startup failure and serves a degraded app. `GET /api/health` returns `{"status": "degraded", "reason": "llm_registry_unavailable"}` with 200.
- Integration: `GET /api/config` in degraded mode returns the config + degraded flag.
- Integration: `PUT /api/config` in degraded mode with a valid LLM config writes the file, returns 200 with `reloaded=False, restart_required=True`. After process restart (in-test: rebuild app), the new app boots into normal mode.
- Integration: `GET /api/recommendations` in degraded mode → 503 with degraded payload.
- Extension static: popup writes a config snapshot to `chrome.storage.local["openbiliclaw.config_cache"]` after every successful fetch.
- Extension static: gear-open path: on `fetchConfig()` HTTP error, falls back to the cached snapshot; renders form; banner explains "backend unreachable; editing from last-known-good cache".
- Extension static: gear-open path: on degraded-mode 200 response, renders the form with a different banner ("backend running in degraded mode; restart required after save") and shows the issues list inline.

### Workstream E — Structured JSON coercion

- Unit: shared helper extracts a list from `{"results":[...]}`, `{"items":[...]}`, `{"data":[...]}`, a root list, a single dict when the caller allows singleton, fenced JSON, JSONL objects, and "echoed schema object(s) + final fenced array".
- Unit: helper recovers the real array from malformed `{ [ ... ] }` roots seen in `soul.insight`.
- Recommendation integration: `classify_pool_backlog` accepts JSONL classification output and wrapped `{"results":[...]}` without logging `classify_pool_backlog: batch failed`.
- Recommendation integration: batch expression generation accepts `{"items":[{"expression":...}]}` and a single `{"expression":...}` object when batch size is 1.
- Soul integration: `InsightAnalyzer` accepts wrapped arrays, JSONL hypotheses, and echoed schema before final fenced array.
- Regression: existing `AwarenessAnalyzer`, `DelightLLMScorer`, and `ContentDiscoveryEngine._evaluate_batch` tests still pass; add one test each to ensure they now call the shared helper or at least cover the shared shapes.

### Workstream F — Ollama embedding warning hygiene

- Unit: `[llm.embedding].provider="ollama"`, empty embedding `api_key/base_url`, empty `[llm.ollama]` block -> `build_embedding_service` returns an Ollama-backed service with default base URL and emits no `back-compat` warning.
- Unit: same but `[llm.ollama].base_url="http://localhost:11434"` -> borrowed and normalized to `/v1`, no warning.
- Unit: remote provider `openai` with empty `[llm.embedding]` credentials and populated `[llm.openai].api_key` still emits exactly one back-compat warning.

### Workstream G — File traceback preservation

- Unit: `configure_logging` with rotating file handler, then `logger.exception("sentinel")`; log file contains sentinel message, `Traceback (most recent call last)`, and `ValueError: sentinel`.
- Unit: same with rotation disabled (`max_file_size_mb=0`) and plain `FileHandler`.
- Integration: hot-reload failure test configures a temp log file, monkeypatches `RuntimeContext.rebuild_from_config` to raise, triggers `PUT /api/config`, flushes handlers, and confirms the file log includes the hot-reload error traceback.

## Documentation

Update:
- `docs/modules/api.md` — `PUT /api/config` schema (`ok=false` 400 body, `rollback_applied`, `restart_required`), degraded-mode endpoint contracts, `GET /api/config` degraded fields.
- `docs/modules/config.md` — call out that empty payload values don't erase existing values; document explicit `reset_fields`; document `config.toml.bak` semantics; document the validate-before-write contract.
- `docs/modules/extension.md` — popup config-cache and degraded-mode rendering.
- `docs/modules/cli.md` — `openbiliclaw start` panel update for degraded-mode boot (prints a one-line WARN with the issues).
- `docs/modules/llm.md` — shared structured-output coercion contract; remote-provider vs Ollama embedding warning semantics.
- `docs/modules/recommendation.md` — classification/expression/delight structured-output tolerance and fallback behavior.
- `docs/modules/discovery.md` — batch evaluation structured-output tolerance if the shared helper changes parser internals.
- `docs/modules/soul.md` — awareness/insight structured-output tolerance.
- `docs/architecture.md` — degraded-runtime branch in the boot flow.
- `docs/spec.md` §3 system architecture — same degraded-runtime branch and config-save data flow, kept in sync with `docs/architecture.md`.
- `README.md` / `README_EN.md` top architecture diagrams — update only if this PR changes the visible cross-module flow (Workstream D does).
- `docs/changelog.md` — version entry covering all remediation workstreams.
- `README.md` / `README_EN.md` — 📌 highlights callout (≤4 bullets, CN/EN in sync, per CLAUDE.md rules) — replace the v0.3.72 highlights with the v0.3.73 (or next-version) ones.
- `docs/modules/runtime.md` (create if absent) or `docs/architecture.md` — add a repo-visible "release policy implies updater tag filtering" note. The local `.claude/.../feedback_release_policy.md` memory can be updated separately, but it is not PR-reviewable and must not be the only documentation.

If workstreams are split into separate PRs, each PR still needs its scoped module docs and a `docs/changelog.md` entry; the docs/release workstream is only valid for a single combined release branch.

## Included Follow-up Fixes

These were previously separated into follow-up specs, but are now in this spec at the user's request:

- **mimo / non-OpenAI JSON-shape mismatch** is Workstream E.
- **Embedding-ollama warning when provider=ollama but api_key/base_url unset** is Workstream F.
- **File-handler traceback gap** is Workstream G.

Still out of scope: a wholly offline popup that queues config writes while the daemon is not running, changing backend release policy away from source-only git tags, and recovering API keys that were already erased before this release.

## Backwards Compatibility

- Auto-updater fix is backward-compatible: pre-fix installs running the new code will start correctly identifying backend tags. No config migration.
- Transactional PUT adds two response fields (`rollback_applied`, `restart_required`) and starts returning structured 400 bodies for blocking validation failures. Pre-fix popup would only show a generic error; the new popup renders the issue list from the error body.
- Mask/empty guard is strictly more conservative — any prior payload that successfully erased a value will now keep it unless the caller uses explicit `reset_fields`. The only realistic regressions are scripts that intentionally sent `api_key=""`; they must migrate to `reset_fields`.
- Degraded mode is additive for FastAPI startup only: the normal boot path is unchanged when LLM config is valid, and direct `build_runtime_context` callers still raise `RegistryBuildError` unless they explicitly opt into a degraded helper.
- Structured-output coercion is additive for the supported wrapper shapes. Existing strict-schema failures remain failures when required semantic fields are absent.
- Ollama embedding warning suppression is limited to local Ollama. Remote-provider migration warnings remain.
- File traceback preservation only affects log file content; no runtime behavior change.

## Risk

- **Auto-updater filter mis-classifies legacy tags.** Mitigation: support bare `v0.3.x` and `0.3.x` tags as backend tags (legacy convention). Test fixtures exercise both.
- **Transactional PUT lock contention.** A long `rebuild_from_config` (the typical case: tens of seconds for fresh embedding service init) holds the save lock and blocks concurrent saves. Acceptable — single-user product, popup saves are rare. Don't introduce per-section locking unless real contention shows up.
- **Degraded mode masks real registry build failures during normal upgrades.** If a future refactor causes `build_llm_registry` to raise on perfectly-valid configs, users see a degraded-mode boot instead of an immediate hard failure. Mitigation: `openbiliclaw start` panel prints a prominent WARN with the issues list and a "this is unexpected, please file an issue" pointer. CLI `--strict-llm` flag (off by default) can be added if needed to opt back into hard-fail behaviour, but defer until proven necessary.
- **Popup cache staleness.** `chrome.storage.local` cache could be from a much older session; rendering it labelled as authoritative would confuse the user. Mitigation: the cache fallback banner is loud ("editing from cached snapshot from <timestamp>; backend is unreachable") and the save button is amber.
- **Structured-output coercion could accept the wrong echoed JSON.** Mitigation: caller passes required-field predicates; helper chooses the last schema-valid array/object after prompt echoes, not the first JSON container.
- **Ollama warning suppression could hide real missing local service.** Mitigation: construction stays credentialless, but actual embedding call failures still log request errors; this workstream only removes the misleading migration warning.
- **Traceback formatter guard could duplicate exception text.** Mitigation: guard checks for `Traceback (most recent call last)` before appending.
