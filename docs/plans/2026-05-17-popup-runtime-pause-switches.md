# Popup Runtime Pause Switches Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add two prominent popup toggles — "省钱模式（暂停后台 LLM）" and "关闭浏览器后停止后台" — backed by an authoritative `scheduler.enabled` gate and a new `pause_on_extension_disconnect` gate driven by `/api/runtime-stream` presence tracking. Wire the gates into daemon-owned background LLM / embedding work so cost-consuming background work actually stops when toggled, while explicit user-triggered API calls remain available.

**Architecture:** Add two scheduler config fields (`pause_on_extension_disconnect`, `extension_disconnect_grace_seconds`); introduce a `PresenceTracker` singleton on `RuntimeContext` updated by the `/api/runtime-stream` WS hook; centralize the background-LLM predicate and use it from `ContinuousRefreshController`, `AccountSyncService`, startup / hot-reload one-shots, and OpenClaw's direct runtime bootstrap; surface two top-card toggles in the popup plus a parity checkbox in the settings drawer.

**Tech Stack:** Python (dataclasses / FastAPI / pytest / asyncio) for backend; existing extension popup JavaScript + node test runner for the extension.

---

### Task 1: Extend SchedulerConfig and Persistence

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Test: `tests/test_config.py`

**Step 1: Write failing tests**

Add tests proving:
- `SchedulerConfig` defaults: `pause_on_extension_disconnect=False`, `extension_disconnect_grace_seconds=90`.
- `load_config` reads both keys from a TOML fixture under `[scheduler]`.
- `load_config` returns defaults when keys are absent (backward compatible).
- `save_config` round-trips both keys.
- Invalid grace value (negative, zero, non-int) is coerced to default with no exception raised.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_config.py -k "scheduler and (pause_on_extension or grace)" -q
```

Expected: fail because new fields do not exist.

**Step 3: Implement config changes**

- Add `pause_on_extension_disconnect: bool = False` to `SchedulerConfig`.
- Add `extension_disconnect_grace_seconds: int = 90` to `SchedulerConfig`.
- Update `_load_scheduler_config` (or equivalent loader) to read both keys with safe coercion and default fallback.
- Update `save_config` TOML serialization to write both keys.
- Add a one-line module docstring explaining that `enabled` is the authoritative LLM-loop gate (no longer cosmetic).

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/config.py tests/test_config.py
git commit -m "feat: add scheduler pause-on-disconnect config fields"
```

---

### Task 2: Add PresenceTracker and the Shared Gate Predicate

**Files:**
- Create: `src/openbiliclaw/runtime/presence.py`
- Test: `tests/test_presence.py`

**Step 1: Write failing tests**

Add tests proving:
- New tracker reports `is_present(grace_seconds=90) == True` for `grace_seconds` after construction (startup grace).
- After `grace_seconds` elapse with no connections, `is_present` returns False.
- `on_connect` increments active count; `is_present` is True immediately.
- `on_disconnect` decrements; when count returns to 0, `last_disconnect_at` is recorded.
- Two concurrent `on_connect` then one `on_disconnect` leaves `is_present == True`.
- `is_present(grace_seconds=N)` returns True for N seconds after the final disconnect, then False.
- Tracker is concurrency-safe under `asyncio.gather` of many connect/disconnect pairs (final count is 0, no negative count).
- Calling `on_disconnect` when active count is already 0 does not go negative (logs warning, no-op).
- A shared helper / small gate object returns the correct background-LLM boolean for:
  - `scheduler.enabled=False` -> blocked.
  - `scheduler.enabled=True`, `pause_on_extension_disconnect=False` -> allowed regardless of presence.
  - `pause_on_extension_disconnect=True` + stale presence -> blocked.
  - `pause_on_extension_disconnect=True` + active or grace-window presence -> allowed.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_presence.py -q
```

Expected: fail because `openbiliclaw.runtime.presence` does not exist.

**Step 3: Implement tracker**

Create `presence.py` with a `PresenceTracker` class exposing:
- `__init__(self, *, now: Callable[[], float] = time.monotonic)` — inject clock for tests.
- `on_connect() -> None`
- `on_disconnect() -> None`
- `is_present(grace_seconds: int) -> bool`
- `snapshot() -> dict` for runtime logging / diagnostics (active count, last_disconnect_at, seconds_since_disconnect).

Also expose a tiny shared predicate (function or dataclass) used by every background worker:

```python
def background_llm_work_allowed(scheduler: object, presence: PresenceTracker) -> bool:
    if not bool(getattr(scheduler, "enabled", True)):
        return False
    if not bool(getattr(scheduler, "pause_on_extension_disconnect", False)):
        return True
    try:
        grace = int(getattr(scheduler, "extension_disconnect_grace_seconds", 90) or 90)
    except (TypeError, ValueError):
        grace = 90
    if grace <= 0:
        grace = 90
    return presence.is_present(grace_seconds=grace)
```

Use a plain `threading.Lock` or simple guarded integer state. Do **not** use `asyncio.Lock` unless the public methods become async; the planned `on_connect() -> None` / `on_disconnect() -> None` API is synchronous.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/presence.py tests/test_presence.py
git commit -m "feat: add extension presence tracker"
```

---

### Task 3: Wire Presence Into RuntimeContext and the WS Endpoint

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing tests**

Add tests proving:
- `RuntimeContext` exposes `presence: PresenceTracker` and the same instance survives `rebuild_from_config()`.
- Opening a `/api/runtime-stream` WebSocket increments presence; closing decrements it.
- A second concurrent client keeps presence True after the first disconnects.
- A client that closes while no events are being published still decrements presence promptly.
- An exception in either the WS writer or receive loop still triggers `on_disconnect` exactly once.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -k "presence or runtime_stream" -q
```

Expected: fail because presence is not exposed and the WS handler does not call the tracker.

**Step 3: Implement wiring**

- Add `presence: PresenceTracker = field(default_factory=PresenceTracker)` to the stable section of the `RuntimeContext` dataclass.
- In `rebuild_from_config`, **do not** recreate the tracker — preserve the existing one so toggling settings does not lose state.
- In the `/api/runtime-stream` endpoint:
  - Accept the socket and subscribe to the event hub first.
  - Call `ctx.presence.on_connect()` only after the stream is usable.
  - Run the outbound queue writer and a receive-side disconnect detector concurrently. The receive side can ignore client messages; its job is to wake up on `WebSocketDisconnect` even when there are no outbound runtime events.
  - Wrap the whole connected lifetime in one `try/finally` and call `ctx.presence.on_disconnect()` plus `unsubscribe(queue)` in that `finally`.
- Defer passing the tracker to runtime workers until Task 4, where their constructors are updated together.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/runtime_context.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: track extension presence via runtime-stream WS"
```

---

### Task 4: Gate Background LLM Work on Both Switches

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/runtime/account_sync.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_account_sync.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write failing tests**

Add tests proving:
- All six loops (`_loop_refresh`, `_loop_pool_precompute`, `_loop_soul_pipeline`, `_loop_xhs_producer`, `_loop_douyin_producer`, `_loop_proactive_push`) skip their body when `scheduler.enabled=False` (use a fake clock / counter to verify body was not called within N ticks).
- All six loops skip when `pause_on_extension_disconnect=True` AND presence is stale.
- All six loops run normally when `pause_on_extension_disconnect=True` AND presence is fresh (within grace).
- All six loops run normally when `pause_on_extension_disconnect=False` regardless of presence.
- `_loop_refresh` does not call `_on_profile_ready_if_first_time()` / `classify_pool_backlog()` while blocked.
- `AccountSyncService.sync_if_due()` skips without fetching account network data, without calling `analyze_events()`, and without updating `last_account_sync_at` while blocked.
- `RuntimeContext.restart_background_tasks()` does not call startup `speculator.force_tick()` or detached `prewarm_pool_mmr_embeddings()` while blocked.
- OpenClaw bootstrap passes scheduler config / presence into its directly constructed runtime controller so non-FastAPI runtime construction honors the same config semantics.
- An in-flight `refresh_if_needed` is NOT preempted when `scheduler.enabled` flips False mid-call (call still completes; next tick is gated).
- `_llm_work_allowed()` delegates to the shared predicate and returns the correct boolean for the matrix covered in `tests/test_presence.py`.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_refresh_runtime.py tests/test_account_sync.py tests/test_api_app.py tests/test_openclaw_adapter.py -k "llm_work_allowed or paused or scheduler_disabled or presence_gate or account_sync_gate or startup_one_shot_gate or openclaw_presence" -q
```

Expected: fail because no gating exists.

**Step 3: Implement gating**

- Add optional `scheduler_config` and `presence` arguments to `ContinuousRefreshController.__init__` and store them. Provide conservative defaults (`enabled=True`, `pause_on_extension_disconnect=False`, fresh startup-grace presence) so the many direct unit-test constructors do not all need unrelated churn.
- Update production `RuntimeContext._rebuild_components()` and OpenClaw bootstrap to pass the real `new_config.scheduler` and the correct `PresenceTracker`.
- Add the helper:

  ```python
  def _llm_work_allowed(self) -> bool:
      return background_llm_work_allowed(self._scheduler_config, self._presence)
  ```

- At the top of each of the six loops, before the first LLM / embedding touching body, add:

  ```python
  if not self._llm_work_allowed():
      await asyncio.sleep(self.check_interval_seconds)
      continue
  ```

  Place the gate before `_on_profile_ready_if_first_time()`, `_drain_pool_precompute_backlog()`, `_tick_soul_pipeline()`, `_tick_xhs_producer()`, `_tick_douyin_producer()`, and `prepare_delight_candidates()`. Do not treat `_on_profile_ready_if_first_time()` as harmless housekeeping; it can call `classify_pool_backlog()`.
- Log a one-line INFO when transitioning gate state (`allowed → blocked` or `blocked → allowed`) per controller, not per loop, to avoid log spam. Use a single state variable on the controller and emit on transitions.
- Do NOT gate `_loop_refresh`'s init grace period (`_init_grace_consumed`) — that runs once before any LLM call.
- Add an optional `llm_work_allowed: Callable[[], bool] | None = None` to `AccountSyncService`. If present and False, `sync_if_due()` returns `{"synced": False, "new_event_count": 0, "reason": "llm_paused"}` before network fetches and without updating due timestamps.
- In `RuntimeContext.restart_background_tasks()`, check the same predicate before startup `speculator.force_tick()` and before scheduling `_safe_prewarm_pool_mmr_embeddings()`.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py src/openbiliclaw/runtime/account_sync.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py tests/test_refresh_runtime.py tests/test_account_sync.py tests/test_api_app.py tests/test_openclaw_adapter.py
git commit -m "feat: gate background LLM work on scheduler + presence"
```

---

### Task 5: Expose Both Fields in the Config API

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing tests**

Add API tests proving:
- `GET /api/config` includes `scheduler.pause_on_extension_disconnect` and `scheduler.extension_disconnect_grace_seconds`.
- `PUT /api/config` with `{"scheduler": {"pause_on_extension_disconnect": true}}` persists and is reflected on the next GET.
- `PUT /api/config` with a string `"true"` / `"on"` for the boolean is coerced (consistent with existing `_as_bool` helper used for other scheduler booleans).
- `PUT /api/config` with invalid `extension_disconnect_grace_seconds` values (negative, zero, non-int) falls back to 90 instead of raising.
- `PUT /api/config` rebuilds the runtime so the controller sees the new value on its next gate check (verify via a snapshot endpoint or by checking `runtime_controller._scheduler_config` after the PUT).

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -k "config and (pause_on_extension or grace)" -q
```

Expected: fail because the new fields are absent from the response model and the update loop.

**Step 3: Implement API**

- Add `pause_on_extension_disconnect: bool = False` and `extension_disconnect_grace_seconds: int = 90` to `SchedulerConfigOut` in `models.py`.
- In `app.py::_config_to_response`, populate both fields from `cfg.scheduler`.
- In `app.py::update_config`, extend the `for key in (...):` tuple under the `"scheduler"` branch (around line 3485) to include the two new keys.
- Do not rely on raw `int(...)` for the grace field. Add explicit normalization so invalid values fall back to 90 and valid positive integers persist.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: expose pause-on-disconnect via /api/config"
```

---

### Task 6: Add Popup Top-Card Toggles and Settings-Drawer Parity

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-api.js`
- Test: `extension/tests/popup-runtime-toggles.test.ts` (new)

**Step 1: Write failing tests**

Add a node `--test` static check that:
- Popup HTML contains the two top-card toggle IDs: `cfgRuntimePauseLlm` and `cfgRuntimePauseOnDisconnect`.
- Popup HTML contains the new settings-drawer checkbox ID: `cfgPauseOnDisconnect`, in the existing scheduler section.
- A `collectForm` (or equivalent settings serializer) sends `scheduler.pause_on_extension_disconnect` in the PUT payload.
- A new `updateRuntimeToggle(name, value)` helper in `popup-api.js` calls `PUT /api/config` with the matching scheduler patch and returns the parsed response.
- On boot, popup reads `scheduler.enabled` and `scheduler.pause_on_extension_disconnect` from `GET /api/config` and reflects them in both the top-card toggles and the settings checkboxes.

**Step 2: Run tests to verify failure**

```bash
cd extension && node --test --experimental-strip-types tests/popup-runtime-toggles.test.ts
```

Expected: fail because the markup, helper, and wiring do not exist yet.

**Step 3: Implement popup changes**

- In `popup.html`, add a new control row directly under the main card header (next to or above the existing backend-status pill). Two pill-style toggles, each with a label, a tooltip, and an inline state hint ("✓ 已启用" / "✗ 已暂停"). Reuse the existing `--brand` / `--brand-soft` palette so it visually matches the popup's pink/blue tokens.
- In `popup.html` settings drawer, add `cfgPauseOnDisconnect` checkbox + label "关闭浏览器后停止后台" inside the existing "调度" section, immediately below `cfgSchedulerEnabled`. Add a hint `<p class="settings-hint">` explaining the grace period.
- Relabel the existing `cfgSchedulerEnabled` row from "启用定时发现" to "后台 LLM 总开关（关闭=省钱模式）" so the meaning matches the new behavior.
- In `popup.js`, hook the two top-card toggles to a new `updateRuntimeToggle` API call; on success, refresh the relevant state slice and reflect in both the top card and the settings drawer (single source of truth via the existing popup state object).
- In `popup-api.js`, add `updateRuntimeToggle(name: 'pause_llm' | 'pause_on_disconnect', value: boolean)` that maps to the right `PUT /api/config` body shape (`{scheduler: {enabled: !value}}` for `pause_llm`, `{scheduler: {pause_on_extension_disconnect: value}}` for the other).
- Make sure flipping the top-card toggle while the daemon is unreachable surfaces the existing error toast pattern — do not silently leave the UI in an inconsistent state.

**Step 4: Run tests to verify pass**

Run the same extension test command and confirm pass.

**Step 5: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/popup/popup-api.js extension/tests/popup-runtime-toggles.test.ts
git commit -m "feat: add popup runtime pause toggles"
```

---

### Task 7: CLI Surfacing + Docs + Final Verification

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/api.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`

**Step 1: CLI surfacing**

- Add two rows to `openbiliclaw config-show` scheduler section: "暂停后台 LLM (省钱模式)" and "关浏览器后停止后台" with grace seconds suffix. `config-show` only shows static config, not live presence counts.
- On both `openbiliclaw start` and `openbiliclaw serve-api`, if `scheduler.pause_on_extension_disconnect=True`, print/log a one-line WARN before uvicorn starts: "extension presence required; backend will pause background LLM work after grace period if no extension client connects".
- Test in `tests/test_cli.py` proving both rows render and the WARN fires only when the flag is True for both server entrypoints.

**Step 2: Docs**

Document in:
- `docs/modules/config.md`: the two new scheduler fields, defaults, semantic of "enabled is authoritative".
- `docs/modules/api.md`: presence semantics, `/api/runtime-stream` connect/disconnect updates the tracker, gate evaluation order in the controller.
- `docs/modules/extension.md`: two top-card toggles + drawer parity, mapping to `PUT /api/config` payloads.
- `docs/modules/cli.md`: new `config-show` rows + startup WARN.
- `docs/architecture.md`: add the runtime presence singleton and background gate data flow.
- `docs/spec.md`: gate-in-the-loop addendum under §3 system architecture and any diagram text needed to reflect the new data flow.
- `README.md` / `README_EN.md`: swap the 📌 vX.Y.Z highlights callout for the new version per AGENTS.md rule (≤4 bullets, ≤1 sentence each, CN/EN in sync, replace not append); update top architecture diagram/text if it mentions runtime flow.
- `docs/changelog.md`: add `## v0.3.73: 插件后台暂停开关 (YYYY-MM-DD)` (or whatever the next version is) at the top with two bullets covering the user-facing toggles.

**Step 3: Lint + targeted tests**

```bash
uv run --extra dev ruff check src/openbiliclaw/config.py src/openbiliclaw/runtime/presence.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/runtime/account_sync.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/cli.py tests/test_config.py tests/test_presence.py tests/test_refresh_runtime.py tests/test_account_sync.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py
uv run --extra dev python -m pytest tests/test_config.py tests/test_presence.py tests/test_refresh_runtime.py tests/test_account_sync.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py -q
cd extension && npm run typecheck && npm run test
```

Expected: targeted lint passes and all referenced tests pass. If broader repo lint fails on pre-existing unrelated debt, record the exact failure scope.

**Step 4: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py docs/modules/config.md docs/modules/api.md docs/modules/extension.md docs/modules/cli.md docs/architecture.md docs/spec.md README.md README_EN.md docs/changelog.md
git commit -m "docs: describe popup runtime pause switches"
```

**Step 5: Manual UAT**

- Start daemon + load extension.
- Confirm top-card toggles render, fetch state on open.
- Flip "省钱模式" → observe logs show one INFO "blocked"; refresh tick logs go silent.
- Flip back → INFO "allowed"; refresh tick resumes.
- Flip "关浏览器后停止后台" → close all browser windows; after grace expires, observe INFO "blocked"; reopen browser → extension reconnects WS → observe INFO "allowed".
- Verify cost dashboard (`openbiliclaw cost --by caller`) does not show new LLM calls during paused window.

**Step 6: Finish branch**

Run `superpowers:verification-before-completion`. If clean, stop with a short status summary and wait for explicit maintainer confirmation before merging, pushing, or tagging `extension-vX.Y.Z`. The version should be chosen from the current changelog/package state at implementation time, not hard-coded in this plan.
