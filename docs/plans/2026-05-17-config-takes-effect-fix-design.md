# Config Takes Effect Fix Design

## Goal

When a user clicks Save in the popup settings page, the request must (1) return promptly so the "保存中..." button always recovers, and (2) actually apply every persisted field to the live runtime. Today both guarantees are violated by two independent bugs that both surface as "前端配置修改没生效". This spec fixes both in one release package.

## Problem Statement

A user reported on 2026-05-17 that "前端配置修改没生效". Investigation surfaced two distinct failures, both observable from the popup, both originating in different layers, both currently uncovered by tests that only assert "config was persisted to TOML".

### Bug A — Save hangs because PUT /api/config blocks on background LLM work

`PUT /api/config` does more than save the file. After `save_config(cfg)` it synchronously awaits `ctx.rebuild_from_config(cfg)` followed by `ctx.restart_background_tasks(app)`. The second call runs a startup-style one-shot speculator tick inline:

- `src/openbiliclaw/api/app.py:3843` — `await ctx.rebuild_from_config(cfg)`
- `src/openbiliclaw/api/app.py:3844` — `await ctx.restart_background_tasks(app)`
- `src/openbiliclaw/api/runtime_context.py:434-460` — `await speculator.force_tick(profile, feedback_history=...)` inline, no `task_registry.track` wrapper

`speculator.force_tick` may invoke a structured LLM call (`src/openbiliclaw/soul/speculator.py:790` → `_generate` → line 1049 `complete_structured_task`). When the LLM is slow or unreachable (rate limit, network stall, Ollama warming up, mimo wrapper timeout), `force_tick` never returns and the PUT response is never sent.

The frontend amplifies the symptom because `requestJson` (`extension/popup/popup-api.js:6-22`) is a plain `fetch()` with no `AbortController`, and `updateConfig` (`extension/popup/popup-api.js:319`) just delegates to it. The save handler in `extension/popup/popup.js:4398-4463` `await`s `updateConfig(data)` with no timeout. Result: while the backend is waiting on speculator, the popup button stays at "保存中..." indefinitely.

Note: `prewarm_pool_mmr_embeddings` already follows the right pattern — it is scheduled via `task_registry.track` and runs detached. `speculator.force_tick` is the lone holdout.

### Bug B — Per-module LLM overrides are dead config

`docs/modules/config.md:151` documents `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]` as per-module LLM overrides. The popup settings drawer exposes provider+model fields for all four modules (`extension/popup/popup.html:3806+`). The full PUT → save → hot-reload chain persists them correctly:

- `extension/popup/popup.js:4239-4254` — `collectForm()` includes all four module blocks
- `src/openbiliclaw/api/app.py:3651-3658` — `update_config` writes `cfg.llm.<module>.provider/model`
- `src/openbiliclaw/config.py:463-478` — round-trips through `save_config` / `load_config`
- `src/openbiliclaw/api/models.py:548-551` — exposed in `GET /api/config` schema

But **no runtime code path reads them**. `grep -rn "config.llm.soul\|config.llm.discovery\|config.llm.recommendation\|config.llm.evaluation" src/openbiliclaw/{llm,soul,discovery,recommendation}/` returns zero matches. `LLMRegistry.complete()` (`src/openbiliclaw/llm/base.py:216`) always uses `self._default`. `LLMService.complete_with_core_memory()` (`src/openbiliclaw/llm/service.py:181`) accepts a `caller` tag but only uses it for priority gating and usage-recorder attribution, not for provider routing.

Reproduction (script run on this branch, claude provider registered, default=openai, `[llm.soul].provider="claude"`):

```
caller='soul.preference' resolved to provider: 'openai'
openai.complete called: 1
claude.complete called: 0
AssertionError: per-module override is dead config: soul.provider='claude' but call went to 'openai'
```

The user changes `[llm.soul].provider="claude"` in the popup, sees "热重载成功，新配置立即生效", and every soul-tagged LLM call still goes to openai.

Bug A and Bug B are independent — fixing one does not affect the other. They share only one structural theme: both existing test suites (`tests/test_api_config_*.py`, `tests/test_api_app.py::*config*`) assert that values are persisted to `config.toml`, neither asserts that the **runtime actually uses the value**.

## Current Gaps

- `RuntimeContext.restart_background_tasks` distinguishes between detached work (`prewarm_pool_mmr_embeddings` via `task_registry.track`) and inline work (`speculator.force_tick`) for no documented reason. The inline path predates `task_registry`.
- `requestJson` has no shared timeout primitive. Other popup endpoints use ad-hoc per-call `AbortController + setTimeout` blocks (see `extension/popup/popup-api.js:285-298` for the delight respond pattern), but `updateConfig` does not.
- `LLMService` has no knowledge of which provider should serve a given caller tag. The `caller` parameter is a one-way breadcrumb for cost ledger writes; the dispatch is hardcoded to `self.registry.complete(...)` which always uses `_default`.
- `LLMProvider.complete()` in `src/openbiliclaw/llm/base.py:82` accepts no per-call `model` override. Each provider hardcodes `self._model` at construction time. There is no way to make a single registered provider serve two modules with different models.
- `SoulEngine` constructs its own internal `LLMService` (`src/openbiliclaw/soul/engine.py:94`) — wiring module overrides means threading them into SoulEngine's constructor too, not just the top-level `LLMService` built by `RuntimeContext`.
- Existing PUT-config tests stub `database` and `memory_manager` as plain `object()` so `restart_background_tasks` short-circuits the speculator path entirely (`soul_engine.get_profile()` would fail). The "save stuck" path is therefore invisible to the current test suite.
- Existing hot-reload tests (`tests/test_api_app.py::test_put_triggers_runtime_hot_reload`) assert `response.json()["reloaded"] is True` and stop there. They do not exercise the actual runtime components after the hot-reload, so a dead-config override is undetectable.

## Chosen Approach

Two independent workstreams plus a docs/release workstream. A and B are behaviorally independent and separately shippable, but they both touch `RuntimeContext` during implementation; land or rebase the backend detach before the module-override wiring to avoid a small merge conflict. Both must land before claiming the user complaint is fully addressed.

### A. Save responds promptly, regardless of background LLM state (P0)

1. **Backend** — Move `await speculator.force_tick(...)` out of the inline path in `RuntimeContext.restart_background_tasks` and into the same `task_registry.track` detached pattern already used by `prewarm_pool_mmr_embeddings`. The PUT handler's synchronous responsibility ends once `rebuild_from_config` returns (runtime components atomically swapped) and the background-task slots have been re-spawned. The post-reload speculator tick becomes a fire-and-forget background task; failures are swallowed silently (current behavior — `except Exception: pass`), and a new test asserts that a hanging `force_tick` does not block PUT response.

2. **Frontend** — Wrap `updateConfig` in a bounded `AbortController` (60s default, configurable via const). On `AbortError`:
   - Show an amber toast: "后端处理超时，保存请求可能已写入；热重载可能仍在后台进行。请稍后刷新设置确认。"
   - Re-enable the save button.
   - Do NOT touch `state.runtimeConfig` (we don't know if the save actually committed).
   - Do NOT clear the form (user may want to retry).

   On other failures, keep the existing `renderStructuredConfigError` path. The 60s budget covers normal hot-reload (`build_llm_registry` + embedding service init are the slowest steps, both typically <30s) plus headroom; this is a safety net for unforeseen blockers, not the primary fix.

Out of scope: making `rebuild_from_config` itself preemptable, adding a separate "save without reload" mode, adding a progress indicator. The detach + timeout pair gives the popup a deterministic ceiling on save time; everything else is downstream optimization.

### B. Per-module LLM overrides actually route (P0)

Make `[llm.<module>].provider` and `[llm.<module>].model` (for module ∈ {soul, discovery, recommendation, evaluation}) take effect at LLM call time. The contract: when a call carries a known `caller` tag, `LLMService` maps that tag to a **route bucket** (`soul`, `discovery`, `recommendation`, or `evaluation`) and dispatches to that bucket's configured override provider/model. Empty, unset, unregistered, or non-chat-capable overrides fall back silently to `default_provider` with one INFO log for non-empty unusable provider names.

Routing is not a raw `caller.split(".", 1)[0]` lookup because the current codebase already uses `eval.*` for evaluator jobs and mixes scoring and expression-generation under `recommendation.*`. The built-in route buckets are:

| Caller prefix | Route bucket | Why |
|---|---|---|
| `soul` | `soul` | profile, preference, awareness, insight, speculator, dialogue |
| `discovery.search`, `discovery.explore`, `discovery.trending`, `discovery.related`, `yt_search`, `sources.xhs` | `discovery` | query/source expansion and candidate discovery |
| `discovery.evaluate`, `recommendation.evaluate_batch`, `recommendation.delight_score`, `eval` | `evaluation` | scoring, relevance, specificity, query-quality evaluation |
| `recommendation` | `recommendation` | user-facing expression / delight copy after evaluation-specific prefixes above |

The route table is longest-prefix-first so `recommendation.delight_score` maps to `evaluation` before the broader `recommendation` bucket. This preserves the documented `[llm.evaluation]` meaning ("scoring / relevance") without adding user-configurable per-task overrides.

Three changes:

1. **Provider per-call model override.** Add an optional `model: str | None = None` parameter to `LLMProvider.complete()` in `src/openbiliclaw/llm/base.py:82` and each concrete provider (`openai_provider.py`, `claude_provider.py`, `gemini_provider.py`, `ollama_provider.py`, `openrouter_provider.py`, plus the OpenAI-compatible / DeepSeek subclasses where they exist). When `model` is `None`, behavior is unchanged (use `self._model`). When `model` is a non-empty string, use it for this single call only — do not mutate provider state.

2. **`LLMService` route-bucket routing.** Add a new constructor argument `module_overrides: dict[str, ModuleOverride]` where `ModuleOverride` is a small dataclass holding `(provider: str, model: str)` and either field may be empty. `complete_with_core_memory` and its three wrappers (`complete_structured_task`, `complete_with_tools`, `complete_socratic_dialogue`) resolve the route bucket before calling the registry:

   ```python
   def _resolve_route(self, caller: str) -> tuple[str, str | None]:
       """Return (provider_name, per_call_model). Empty provider_name = use registry default."""
       if not caller:
           return ("", None)
       route_bucket = _route_bucket_for_caller(caller)
       if route_bucket is None:
           return ("", None)
       override = self.module_overrides.get(route_bucket)
       if override is None:
           return ("", None)
       provider_name = override.provider.strip().lower()
       if not provider_name:
           return ("", None)
       if not self.registry.is_chat_capable(provider_name):
           # Unknown or embedding-only override → INFO once, then default.
           self._log_unknown_override_once(route_bucket, provider_name)
           return ("", None)
       model = override.model.strip() or None
       return (provider_name, model)
   ```

   Dispatch: when `provider_name` is non-empty, call a new `LLMRegistry.complete_provider(provider_name, messages, model=per_call_model, ...)` helper instead of `self.registry.complete(messages, ...)`. `complete_provider` executes exactly one chat-capable provider, honors the registry's rate-limit cooldown bookkeeping, and does **not** walk the fallback chain. An override provider that fails just fails, and the caller's existing error handling kicks in. This mirrors what users expect: "I picked claude for soul; if claude breaks, surface the error, don't silently smear cost onto openai".

3. **Wire overrides through every config-backed `LLMService` construction site.** Add a shared helper `collect_module_overrides(config)` so all builders use the same normalization rules (`provider.strip().lower()`, empty provider skipped, model preserved). Wire it into:
   - `RuntimeContext._rebuild_components` top-level `LLMService(...)`.
   - `SoulEngine(...)` via a new constructor parameter so the internal `LLMService` built at `src/openbiliclaw/soul/engine.py:94` receives the same overrides.
   - CLI builders that construct `LLMService` / `SoulEngine` from `load_config()` (`src/openbiliclaw/cli.py`).
   - `build_openclaw_adapter_services()` so OpenClaw runs with the same routing semantics as the daemon.
   - `SocraticDialogue._build_service()` only when it is constructed with explicit `module_overrides`; the FastAPI popup path already injects the top-level `LLMService`.

   In `RuntimeContext._rebuild_components` specifically:
   - Build a single `module_overrides` dict from `new_config.llm.{soul,discovery,recommendation,evaluation}`.
   - Pass it to the top-level `LLMService(...)` constructor.
   - Pass it into `SoulEngine(...)` (new constructor parameter) so the internal `LLMService` built at `src/openbiliclaw/soul/engine.py:94` receives the same overrides.

   Direct callers in tests (`tests/test_llm_service.py::FakeRegistry` users, `SoulEngine` test fixtures) get a default of `{}`, preserving current behavior.

Backwards-compatible failure modes:
- Empty `[llm.soul].provider` → no routing change, default_provider used.
- `[llm.soul].provider = "claude"` but claude not registered (no api_key) → log INFO once per process per module ("module override 'soul' → 'claude' is not registered; falling back to default_provider"), then route as default.
- `[llm.soul].provider = "ollama"` but Ollama is registered for embedding only (`chat_capable=False`) → log INFO once and route as default, matching the registry fallback chain's existing chat-capability rules.
- `[llm.soul].provider = "deepseek"`, `[llm.soul].model = ""` → use deepseek's configured `[llm.deepseek].model`.
- `[llm.soul].provider = "deepseek"`, `[llm.soul].model = "deepseek-v4-flash"` → use deepseek provider with `deepseek-v4-flash` for this call only.

Out of scope: routing for embedding (the embedding pipeline already resolves provider from `[llm.embedding]`), user-configurable per-task overrides finer-grained than the built-in route buckets (e.g. `caller="soul.preference"` getting a different provider than `caller="soul.insight"`), provider-instance pooling.

### C. Documentation + release (P1)

- Update `docs/modules/config.md` §`[llm.soul]` / etc. to note that overrides now route at runtime (remove any caveats implying they're cosmetic).
- Add `docs/modules/llm.md` (or extend if it exists) with the route-bucket routing contract.
- `docs/changelog.md` entry describing both fixes under the next version bump.
- `README.md` / `README_EN.md` 📌 highlights callout: replace the current v0.3.74 callout with v0.3.75 (or whichever version this lands on), per CLAUDE.md rules (≤4 bullets, CN/EN in sync, no internal smokes).

## Data Flow

### Normal save flow (post-fix)

1. User edits popup settings, clicks Save.
2. Popup `PUT /api/config` with 60s `AbortController` budget.
3. Backend handler validates → snapshots `.bak` → `save_config(cfg)` → `await ctx.rebuild_from_config(cfg)` → `await ctx.restart_background_tasks(app)`. The `restart_background_tasks` step re-spawns the long-running loops (`refresh_loop`, `account_sync_loop`, `auto_update_loop`) and schedules the post-reload speculator tick as a detached task via `task_registry.track("post_reload_speculate", ...)`. Returns 200 within seconds.
4. Popup receives `reloaded=True, restart_required=False`, updates `state.runtimeConfig`, shows green toast.
5. Detached speculator tick runs to completion (or fails silently) in the background; its outcome reaches the popup over the existing WS only if it publishes a `profile_updated` event (current behavior, unchanged).

### Routed LLM call flow (post-fix)

1. Caller (e.g. `PreferenceAnalyzer`) calls `llm_service.complete_with_core_memory(..., caller="soul.preference")`.
2. `LLMService._resolve_route("soul.preference")` returns `("claude", "claude-3-opus")` because `_route_bucket_for_caller("soul.preference") == "soul"` and `module_overrides["soul"] = ModuleOverride("claude", "claude-3-opus")`.
3. `LLMService` dispatches `self.registry.complete_provider("claude", messages, model="claude-3-opus", ...)`.
4. Claude provider uses the per-call model override for this single request, leaves `self._model` untouched.
5. Response includes `response.provider == "claude"`, `response.model == "claude-3-opus"`; usage recorder writes the row with the routed model so `openbiliclaw cost --by caller` reports `caller=soul.preference model=claude-3-opus`.

### Save timeout flow (post-fix)

1. Backend hot-reload hangs (e.g. embedding service init stuck on cold Ollama).
2. 60s elapses; popup `AbortController` fires.
3. `updateConfig` rejects with `AbortError`.
4. Popup save handler catches it, shows the amber timeout toast ("保存请求可能已写入"), re-enables save button.
5. Backend eventually completes the hot-reload (or rolls back per existing logic at `app.py:3863-3906`). The next popup config fetch reflects whichever final state landed.

## Error Handling

- **Detached speculator tick failure:** unchanged from current — the outer `try/except Exception: pass` in `restart_background_tasks` already swallows speculator errors. After the detach, errors surface only in logs, not in the PUT response. Add a single DEBUG log line at the schedule site (`"post-reload speculate scheduled as background task"`) so the change is visible during debugging.
- **PUT timeout when save did commit:** the backend wrote `config.toml` and rebuilt the runtime; only the `restart_background_tasks` step is still running. Next popup config fetch will show the new values. The amber toast uses "可能已写入" because the frontend cannot observe the exact commit point after abort. A retry is safe (idempotent PUT) — the second save just re-applies the same values.
- **PUT timeout when save did NOT commit:** the timeout fires during `save_config` or before `.bak` snapshot completes. In this case the file is unchanged (or `.bak` machinery already rolled it back). User retry will replay the save. We do not attempt to detect "did it commit" from the frontend — the next fetch is authoritative.
- **Module override provider not registered or not chat-capable:** silently fall back to default_provider. Log INFO once per (route_bucket, attempted_provider) per process — not per call — to avoid log spam during hot reloads.
- **Module override provider rate-limited:** `LLMRegistry.complete_provider` marks the provider cooldown exactly like `LLMRegistry.complete`, then the error surfaces to caller via the existing `LLMService` error handling; no automatic fallback to default_provider. Users picked an override because they want that provider; smearing cost onto default silently would defeat the purpose.
- **Module override model rejected by provider (e.g. unknown model name):** error surfaces as `LLMProviderError`; caller's existing try/except handles it. We do not validate model names against a static list — providers serve as ground truth.
- **Empty module override `model`:** provider uses its configured `self._model` for this call. This is the documented fallback behavior.
- **Hot-reload still failing for an unrelated reason:** existing rollback path at `app.py:3863-3906` is unchanged. The frontend timeout is independent — it triggers only if the whole PUT exceeds 60s, which the rollback path normally does not because rollback is fast.

## Testing

### Workstream A — Save responds promptly

**Backend unit tests** (new file `tests/test_runtime_context_restart_tasks.py` or extend existing):

- `restart_background_tasks` returns within 1s when a fake speculator's `force_tick` is monkeypatched to `await asyncio.sleep(60)`. Assert the returned coroutine completes; assert `task_registry.stats()["post_reload_speculate"] == 1`; call `cancel_all()` in teardown.
- Detached speculator failure does not propagate: monkeypatch `force_tick` to raise `RuntimeError("boom")`; `restart_background_tasks` returns normally; capture the scheduled task via a monkeypatched `track()` wrapper and assert it completes with no task exception because the helper swallows the error.
- Backwards compatibility: when soul_engine has no `_speculator` attribute or `llm_work_allowed` returns False, the schedule call is skipped (current behavior preserved).

**Backend integration test** (extend `tests/test_api_app.py`):

- New test `test_put_config_does_not_block_on_speculator`: wire a fake SoulEngine whose `_speculator.force_tick` awaits an `asyncio.Event` that the test never sets. Send `PUT /api/config` with a minimal valid payload; assert the response is received within 5s (real wall-clock). Assert `response.json()["reloaded"] is True`.

**Frontend tests** (extend `extension/tests/`):

- Exported `requestJson` honors an optional `timeoutMs` parameter and aborts after the budget elapses (`extension/tests/popup-api.test.ts`, run with `node --test --experimental-strip-types`).
- `updateConfig` defaults to a 60s `timeoutMs` and surfaces `AbortError` to its caller without retry.
- Existing source-level popup settings tests assert the save handler renders the amber timeout toast on `AbortError`, returns before the success branch can call `applyRuntimeConfig`, and still resets the save button in the shared `finally`.
- Popup save handler renders the existing structured error path on a non-abort error (unchanged regression).

### Workstream B — Per-module routing

**LLMService unit tests** (extend `tests/test_llm_service.py`):

- `_resolve_route` with no overrides returns `("", None)` for any caller.
- `_resolve_route("soul.preference")` with `module_overrides={"soul": ModuleOverride("claude", "")}` returns `("claude", None)`.
- `_resolve_route("soul")` (caller without dot) routes the same as `"soul.x"`.
- `_resolve_route("soul.preference")` with override provider that is not registered or not chat-capable returns `("", None)` and logs INFO once.
- `_resolve_route("recommendation.delight_score")` with `module_overrides={"evaluation": ModuleOverride("deepseek", "deepseek-v4-flash")}` returns `("deepseek", "deepseek-v4-flash")` because evaluation-specific prefixes beat the broader recommendation bucket.
- `_resolve_route("eval.relevance")` with `module_overrides={"evaluation": ModuleOverride("deepseek", "")}` routes to `deepseek`.
- `complete_with_core_memory(caller="soul.x")` with the routing target above dispatches `registry.complete_provider("claude", messages, model=None, ...)`, not `registry.complete(...)`. Verified via fake providers per-name.
- `complete_with_core_memory` with empty `module_overrides` falls back to `registry.complete(...)` (regression — existing tests must stay green).
- `complete_with_core_memory(caller="")` skips routing entirely (no `caller` = no module).

**Provider unit tests** (extend each `tests/test_llm_*.py`):

- Each provider's `complete()` accepts `model=None` (default) and uses `self._model` — existing behavior unchanged.
- Each provider's `complete()` accepts `model="some-other-model"` and uses it for the call kwargs (not `self._model`); `self._model` remains unchanged after the call.

**End-to-end integration test** (new test in `tests/test_api_app.py` config-update class):

- Build a config with `default_provider="openai"`, both openai and claude api_keys set, `[llm.soul].provider="claude"`. Build app via `create_app` with real `RuntimeContext._rebuild_components` and fake provider overrides; make a soul-tagged call through `ctx.soul_engine._llm_service`; assert the call hit the claude provider.

  This test proves the wiring end-to-end (config → runtime_context._rebuild_components → SoulEngine internal LLMService → caller routing → claude provider). It is the missing assertion that today's tests skip.

**Reproduction script promotion**: convert the bash one-liner I used to expose the bug into a formal `tests/test_llm_module_override_routing.py` regression suite covering the four route-bucket routing scenarios above.

### Workstream C — Documentation

- `docs/changelog.md` has an entry describing both fixes.
- `docs/modules/config.md` `[llm.<module>]` section asserts (in prose) that the override routes at runtime.
- `README.md` / `README_EN.md` 📌 highlights callout updated per CLAUDE.md rules.

## Documentation

Update:
- `docs/modules/config.md` — clarify `[llm.soul]` / etc. now route at runtime (remove any "TODO" / "planned" language if present).
- `docs/modules/llm.md` (extend or create) — route-bucket mapping table, override resolution order, chat-capable provider eligibility, fallback semantics.
- `docs/modules/api.md` — note the PUT response timing guarantee (returns once components are atomically swapped; post-reload speculator runs detached).
- `docs/modules/extension.md` — popup save flow now uses a 60s timeout; document the amber-toast UX.
- `docs/architecture.md` — if the runtime data flow diagram mentions per-module providers as planned, update to "active".
- `docs/changelog.md` — version entry covering both fixes.
- `README.md` / `README_EN.md` — 📌 highlights callout, replacing the current callout per CLAUDE.md ≤4-bullet, CN/EN-in-sync rule.

## Backwards Compatibility

- **Workstream A — backend detach:** changes only when the post-reload speculator runs (now detached, was inline). Any caller that depended on observing speculation results synchronously via the PUT response was already broken: the WS publish path (`profile_updated`) is the existing surface for speculation results, and it stays unchanged.
- **Workstream A — frontend timeout:** popup users on the old build see no behavior change (request continues to hang there). Users on the new build see saves complete within seconds; the only new UI surface is the amber timeout toast in the rare slow-rebuild case.
- **Workstream B — `LLMProvider.complete(model=None)`:** purely additive parameter with a default that preserves existing behavior. All five concrete providers gain the keyword arg; all existing call sites still compile.
- **Workstream B — `LLMService(module_overrides={})`:** new constructor argument with a default empty dict. All existing call sites continue to work; routing is a no-op when overrides are empty.
- **Workstream B — config semantics:** `[llm.<module>].provider` / `.model` previously were silently inert. Users with existing values in `config.toml` will now have them honored on first restart after upgrade. For users who had configured an override expecting it to work, this is "the bug they were already complaining about, now fixed". For users who set an override casually and forgot, the change is silent — same provider gets used, just with possibly higher cost. The release notes call this out explicitly so casual override users can audit their settings.
- **Workstream B — `SoulEngine` constructor:** new keyword-only `module_overrides: dict[str, ModuleOverride] | None = None` parameter with a default that preserves existing behavior. Existing tests that construct `SoulEngine(llm=..., memory=..., usage_recorder=...)` continue to work.

## Risk

- **Detached speculator hides slow profile-warmup feedback.** Before: a slow speculator tick blocks the PUT, so the popup at least shows "saving..." while it happens. After: PUT returns fast, the user sees "saved", and the speculator finishes later. If speculation matters to the user's next action, this is a perceived regression. Mitigation: the existing `profile_updated` WS event already publishes when speculation lands; the popup already listens. The flow becomes "save returns immediately; profile_updated arrives seconds later" which is consistent with every other long-running runtime operation in the app.
- **60s frontend timeout might fire on legitimately-slow first-time setups.** Cold Ollama warmup, large embedding cache build, or first-ever discovery prewarm on a fresh DB can push hot-reload past 60s on weak hardware. Mitigation: the amber toast tells the user "热重载仍在后台进行"; the next popup open re-fetches the (now-applied) config. Tune the budget by const if real-world reports come in. Optionally surface as a settings advanced field later.
- **Module override silent fallback could mask user errors.** If a user types `[llm.soul].provider = "claud"` (typo), they get the silent INFO log fallback to default. Mitigation: the once-per-process INFO log is sufficient for debugging; we don't want to fail-loud on every popup save and frustrate users. A future enhancement is to validate override provider names against the registry's chat-capable provider set in `_collect_config_issues` and surface as a non-blocking issue in the popup's issues panel.
- **Per-call model override could surprise providers with model-specific kwargs.** Some providers send model-tied extras (e.g. DeepSeek `reasoning_effort` is only valid for `deepseek-reasoner`). Mitigation: the model parameter only changes the `model=` kwarg passed to the SDK; per-provider extras (`reasoning_effort`, `extra_headers`, `extra_body`) remain driven by the provider's own configuration. If a user routes `caller="soul.x"` to a deepseek provider configured with `reasoning_effort="high"` but specifies `model="deepseek-chat"` (non-reasoner), the provider will pass `reasoning_effort` to a model that doesn't accept it. This is a pre-existing risk for any deepseek configuration and is not a routing-specific regression. Documented under `docs/modules/llm.md`.
- **Cost-tracking attribution remains caller-tagged, not provider-routed.** Existing rows already capture `(caller, provider, model)` per call (`usage_recorder.record`), so per-module spend reporting works correctly with the new routing. No schema change required.
