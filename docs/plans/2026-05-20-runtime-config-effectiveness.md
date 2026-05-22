# Runtime Config Effectiveness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make scheduler settings shown in the popup actually affect the live runtime, expose the useful hidden disconnect-grace setting, and replace the dead `discovery_cron` UI field with the runtime controls the daemon really uses.

**Architecture:** Keep `Config.scheduler` as the source of truth. Add explicit scheduler fields for the currently hardcoded runtime cadences and limits, wire those values into `ContinuousRefreshController`, `SoulEngine`, `InterestSpeculator`, and `ProfileUpdatePipeline` at construction time, then update the config API and popup settings form to round-trip the same fields. Keep `discovery_cron` as legacy load/save/API data for backward compatibility, but remove it from the popup and document that it is not consumed by runtime scheduling.

**Tech Stack:** Python dataclasses / FastAPI / pytest for backend and config; existing Chrome extension popup HTML + vanilla JS + Node test runner for settings UI; Ruff and MyPy for verification.

---

### Task 1: Add Scheduler Runtime Fields To Config

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

**Step 1: Write failing config tests**

Add tests in `tests/test_config.py` that verify:

- `Config().scheduler.refresh_check_interval_seconds == 60`
- `Config().scheduler.signal_event_threshold == 6`
- `Config().scheduler.trending_refresh_hours == 3`
- `Config().scheduler.explore_refresh_hours == 12`
- `Config().scheduler.discovery_limit == 30`
- `Config().scheduler.proactive_push_interval_seconds == 120`
- `Config().scheduler.speculator_idle_interval_minutes == 30`
- `load_config()` reads all seven fields from `[scheduler]`
- `save_config()` writes all seven fields and they survive reload
- invalid values fall back to defaults:
  - non-int values
  - zero or negative values
  - `discovery_limit > 60`

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_config.py -k "scheduler_runtime_fields or runtime_scheduler_fields" -q
```

Expected: fail because the fields do not exist.

**Step 3: Implement config fields**

In `SchedulerConfig`, add:

```python
refresh_check_interval_seconds: int = 60
signal_event_threshold: int = 6
trending_refresh_hours: int = 3
explore_refresh_hours: int = 12
discovery_limit: int = 30
proactive_push_interval_seconds: int = 120
speculator_idle_interval_minutes: int = 30
```

Add normalization helpers or extend the existing scheduler load path:

- `refresh_check_interval_seconds`: default if `< 15`
- `signal_event_threshold`: default if `< 1`
- `trending_refresh_hours`: default if `< 1`
- `explore_refresh_hours`: default if `< 1`
- `discovery_limit`: default if `< 1`; clamp or default if `> 60`
- `proactive_push_interval_seconds`: default if `< 30`
- `speculator_idle_interval_minutes`: default if `< 5`

Update `_render_config_toml()` so all fields are written under `[scheduler]`.

Update `config.example.toml`:

- add the new fields with comments;
- mark `discovery_cron` as legacy / currently ignored by runtime scheduling.

**Step 4: Run config tests**

```bash
uv run --extra dev python -m pytest tests/test_config.py -k "scheduler_runtime_fields or runtime_scheduler_fields" -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/config.py config.example.toml tests/test_config.py
git commit -m "feat: add runtime scheduler config fields"
```

---

### Task 2: Expose Runtime Fields In The Config API

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing API tests**

Add or extend config API tests to verify:

- `GET /api/config` includes:
  - `scheduler.extension_disconnect_grace_seconds`
  - the seven new runtime scheduler fields
  - the existing seven `speculation_*` fields
- `PUT /api/config` updates the seven new runtime scheduler fields.
- Invalid API payloads are normalized the same way as TOML load.
- `discovery_cron` is still accepted and persisted for backward compatibility.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -k "config and scheduler and runtime" -q
```

Expected: fail because the new response fields and update handling do not exist.

**Step 3: Implement API shape and update handling**

Update `SchedulerConfigOut` with the seven new fields.

Update `_config_to_response()` so it copies each value from `cfg.scheduler`.

Update `PUT /api/config` scheduler update handling so all seven fields are accepted. Reuse the same normalization helpers from `config.py` rather than duplicating independent coercion rules.

Keep `discovery_cron` in the model and update path, but do not treat it as a runtime control.

**Step 4: Run API tests**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -k "config and scheduler and runtime" -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: expose runtime scheduler fields in config api"
```

---

### Task 3: Wire Runtime Scheduler Fields Into ContinuousRefreshController

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write failing wiring tests**

Add tests proving `RuntimeContext.rebuild_from_config()` constructs `ContinuousRefreshController` with configured values:

```python
cfg.scheduler.refresh_check_interval_seconds = 77
cfg.scheduler.signal_event_threshold = 9
cfg.scheduler.trending_refresh_hours = 5
cfg.scheduler.explore_refresh_hours = 18
cfg.scheduler.discovery_limit = 17
cfg.scheduler.proactive_push_interval_seconds = 155
```

Assert the new controller has:

```python
controller.check_interval_seconds == 77
controller.signal_event_threshold == 9
controller.trending_refresh_hours == 5
controller.explore_refresh_hours == 18
controller.discovery_limit == 17
controller.proactive_push_interval_seconds == 155
```

Add an OpenClaw bootstrap test with the same assertions for the direct adapter path.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py tests/test_openclaw_adapter.py -k "runtime_scheduler_fields or refresh_controller_config" -q
```

Expected: fail because the controller still uses dataclass defaults.

**Step 3: Pass values into the controller**

In `RuntimeContext.rebuild_from_config()`, pass:

```python
signal_event_threshold=new_config.scheduler.signal_event_threshold,
trending_refresh_hours=new_config.scheduler.trending_refresh_hours,
explore_refresh_hours=new_config.scheduler.explore_refresh_hours,
check_interval_seconds=new_config.scheduler.refresh_check_interval_seconds,
proactive_push_interval_seconds=new_config.scheduler.proactive_push_interval_seconds,
discovery_limit=new_config.scheduler.discovery_limit,
```

In `build_openclaw_adapter_services()`, pass the same values when constructing `ContinuousRefreshController`.

No `ContinuousRefreshController` dataclass changes are needed for these fields because they already exist as constructor parameters.

**Step 4: Run wiring tests**

```bash
uv run --extra dev python -m pytest tests/test_api_app.py tests/test_openclaw_adapter.py -k "runtime_scheduler_fields or refresh_controller_config" -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py tests/test_api_app.py tests/test_openclaw_adapter.py
git commit -m "feat: apply runtime scheduler fields to refresh controller"
```

---

### Task 4: Wire Speculation Config Into SoulEngine And InterestSpeculator

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/soul/pipeline.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Test: `tests/test_soul_engine.py`
- Test: `tests/test_pipeline_advanced.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write failing unit tests**

In `tests/test_soul_engine.py`, construct:

```python
engine = SoulEngine(
    llm=registry,
    memory=memory,
    speculation_interval_minutes=22,
    speculation_ttl_days=8,
    speculation_cooldown_days=9,
    speculation_confirmation_threshold=4,
    speculation_max_active=6,
    speculation_max_primary_interests=17,
    speculation_max_secondary_interests=66,
    speculator_idle_interval_minutes=11,
)
```

Assert the internal speculator and pipeline received those values. Prefer behavior-level assertions where practical:

- a generated `SpeculativeInterest` gets `ttl_days == 8`;
- new specs get `confirmation_threshold == 4`;
- `generation_interval_minutes=22` blocks a second generation before 22 minutes;
- `_max_active == 6` can be asserted directly if no behavior test is practical;
- pipeline idle tick does not run before 11 minutes but runs after 11 minutes.

In `tests/test_pipeline_advanced.py`, add a direct `ProfileUpdatePipeline(..., speculator_idle_interval_minutes=11)` test proving the idle interval changes tick behavior.

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_soul_engine.py tests/test_pipeline_advanced.py -k "speculation_config or speculator_idle_interval" -q
```

Expected: fail because `SoulEngine` and `ProfileUpdatePipeline` do not accept the new arguments.

**Step 3: Implement soul wiring**

Add optional keyword arguments to `SoulEngine.__init__` with defaults matching `InterestSpeculator`:

```python
speculation_interval_minutes: int = 10
speculation_ttl_days: int = 3
speculation_cooldown_days: int = 7
speculation_confirmation_threshold: int = 3
speculation_max_active: int = 5
speculation_max_primary_interests: int = 15
speculation_max_secondary_interests: int = 60
speculator_idle_interval_minutes: int = 30
```

Use them when constructing `InterestSpeculator`:

```python
self._speculator = InterestSpeculator(
    llm_service=self._llm_service,
    data_dir=data_dir,
    generation_interval_minutes=speculation_interval_minutes,
    default_ttl_days=speculation_ttl_days,
    cooldown_days=speculation_cooldown_days,
    confirmation_threshold=speculation_confirmation_threshold,
    max_active=speculation_max_active,
    max_primary_interests=speculation_max_primary_interests,
    max_secondary_interests=speculation_max_secondary_interests,
)
```

Add `speculator_idle_interval_minutes: int = 30` to `ProfileUpdatePipeline.__init__` and set:

```python
self._speculator_idle_min_interval = timedelta(minutes=speculator_idle_interval_minutes)
```

Use a minimum of 5 minutes through config normalization, so pipeline does not need independent clamping.

**Step 4: Wire production builders**

Pass scheduler values into `SoulEngine(...)` in:

- `RuntimeContext.rebuild_from_config()`
- `src/openbiliclaw/cli.py` `_build_soul_engine()`
- `build_openclaw_adapter_services()`

Add tests in `tests/test_api_app.py`, `tests/test_cli.py`, and `tests/test_openclaw_adapter.py` proving the values are forwarded from `Config.scheduler`.

**Step 5: Run targeted tests**

```bash
uv run --extra dev python -m pytest tests/test_soul_engine.py tests/test_pipeline_advanced.py tests/test_api_app.py tests/test_cli.py tests/test_openclaw_adapter.py -k "speculation_config or speculator_idle_interval or scheduler_speculation" -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/pipeline.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py src/openbiliclaw/integrations/openclaw/bootstrap.py tests/test_soul_engine.py tests/test_pipeline_advanced.py tests/test_api_app.py tests/test_cli.py tests/test_openclaw_adapter.py
git commit -m "feat: apply scheduler speculation settings at runtime"
```

---

### Task 5: Update Popup Settings UI

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-settings.test.ts`

**Step 1: Write failing extension tests**

Update `extension/tests/popup-settings.test.ts`:

- remove the expectation that `cfgDiscoveryCron` exists;
- assert these IDs exist:
  - `cfgExtensionDisconnectGrace`
  - `cfgRefreshCheckInterval`
  - `cfgSignalEventThreshold`
  - `cfgTrendingRefreshHours`
  - `cfgExploreRefreshHours`
  - `cfgDiscoveryLimit`
  - `cfgProactivePushInterval`
  - `cfgSpeculatorIdleInterval`
- assert `populateForm()` reads all new scheduler fields from `cfg.scheduler`;
- assert `collectForm()` sends all new scheduler fields;
- assert `collectForm()` no longer sends `discovery_cron`.

**Step 2: Run tests to verify failure**

```bash
cd extension && npm test -- popup-settings.test.ts
```

If the extension package does not support filtered test runs, run the repository's existing extension test command from the project root.

Expected: fail because the fields do not exist and `cfgDiscoveryCron` is still present.

**Step 3: Implement popup HTML**

In the scheduler settings panel:

- add an input for `cfgExtensionDisconnectGrace` immediately after `cfgPauseOnDisconnect`;
- remove the visible `cfgDiscoveryCron` field;
- add runtime controls with concise labels:
  - "刷新轮询秒数"
  - "行为触发阈值"
  - "热门刷新小时"
  - "探索刷新小时"
  - "单轮发现上限"
  - "主动推送轮询秒数"
  - "猜测兴趣空闲检查分钟"

Use numeric inputs with conservative min/max attributes matching config normalization where possible.

**Step 4: Implement popup JS**

Update `populateForm()`:

```javascript
setVal("cfgExtensionDisconnectGrace", cfg.scheduler?.extension_disconnect_grace_seconds);
setVal("cfgRefreshCheckInterval", cfg.scheduler?.refresh_check_interval_seconds);
setVal("cfgSignalEventThreshold", cfg.scheduler?.signal_event_threshold);
setVal("cfgTrendingRefreshHours", cfg.scheduler?.trending_refresh_hours);
setVal("cfgExploreRefreshHours", cfg.scheduler?.explore_refresh_hours);
setVal("cfgDiscoveryLimit", cfg.scheduler?.discovery_limit);
setVal("cfgProactivePushInterval", cfg.scheduler?.proactive_push_interval_seconds);
setVal("cfgSpeculatorIdleInterval", cfg.scheduler?.speculator_idle_interval_minutes);
```

Update `collectForm()`:

```javascript
extension_disconnect_grace_seconds: getInt("cfgExtensionDisconnectGrace", 90),
refresh_check_interval_seconds: getInt("cfgRefreshCheckInterval", 60),
signal_event_threshold: getInt("cfgSignalEventThreshold", 6),
trending_refresh_hours: getInt("cfgTrendingRefreshHours", 3),
explore_refresh_hours: getInt("cfgExploreRefreshHours", 12),
discovery_limit: getInt("cfgDiscoveryLimit", 30),
proactive_push_interval_seconds: getInt("cfgProactivePushInterval", 120),
speculator_idle_interval_minutes: getInt("cfgSpeculatorIdleInterval", 30),
```

Remove `discovery_cron: getVal("cfgDiscoveryCron")` from the submitted popup payload.

**Step 5: Run extension tests**

```bash
cd extension && npm test
```

Expected: pass.

**Step 6: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/tests/popup-settings.test.ts
git commit -m "feat: align popup scheduler settings with runtime"
```

---

### Task 6: Update Runtime And Module Documentation

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Optional Modify: `docs/diagrams/discovery-architecture.html`
- Optional Modify: `docs/diagrams/soul-architecture.html`

**Step 1: Update config docs**

In `docs/modules/config.md`:

- add all seven new scheduler runtime fields to the scheduler table;
- keep `discovery_cron`, but label it legacy and state runtime does not currently consume it;
- update `extension_disconnect_grace_seconds` to mention the popup now exposes it;
- update the seven `speculation_*` rows to state they are applied to `InterestSpeculator`.

**Step 2: Update runtime/discovery/soul/extension docs**

In `docs/modules/runtime.md`, document the scheduler runtime fields consumed by `ContinuousRefreshController`.

In `docs/modules/discovery.md`, replace any implication that cron controls discovery with the pool/event/time-triggered runtime model.

In `docs/modules/soul.md`, document:

- `speculator_idle_interval_minutes`
- all seven `speculation_*` settings now taking effect at runtime

In `docs/modules/extension.md`, document the new scheduler settings controls and removal of cron from the popup.

**Step 3: Update changelog**

Add a top entry to `docs/changelog.md`:

- popup no longer exposes dead `discovery_cron`;
- scheduler runtime controls now round-trip and hot reload;
- seven speculation settings now configure `InterestSpeculator`;
- extension disconnect grace seconds is editable in the popup.

**Step 4: Update diagrams only if necessary**

Search for stale mentions:

```bash
rg -n "discovery_cron|speculation_interval|check_interval|signal_event_threshold|trending_refresh|explore_refresh" docs README.md README_EN.md
```

If diagrams or architecture docs claim cron drives runtime scheduling, update them. If they only list runtime parameters generically, no architecture diagram change is required.

**Step 5: Commit**

```bash
git add docs/modules/config.md docs/modules/runtime.md docs/modules/discovery.md docs/modules/soul.md docs/modules/extension.md docs/changelog.md docs/diagrams/discovery-architecture.html docs/diagrams/soul-architecture.html
git commit -m "docs: document effective runtime scheduler config"
```

If the diagram files did not need changes, omit them from `git add`.

---

### Task 7: Final Verification

**Files:**
- Verify only; no planned edits.

**Step 1: Run targeted Python tests**

```bash
uv run --extra dev python -m pytest \
  tests/test_config.py \
  tests/test_api_app.py \
  tests/test_soul_engine.py \
  tests/test_pipeline_advanced.py \
  tests/test_cli.py \
  tests/test_openclaw_adapter.py \
  -k "scheduler or speculation or runtime_config or config" \
  -q
```

Expected: pass.

**Step 2: Run extension tests**

```bash
cd extension && npm test
```

Expected: pass.

**Step 3: Run lint/type checks for touched backend files**

```bash
uv run --extra dev ruff check \
  src/openbiliclaw/config.py \
  src/openbiliclaw/api/models.py \
  src/openbiliclaw/api/app.py \
  src/openbiliclaw/api/runtime_context.py \
  src/openbiliclaw/soul/engine.py \
  src/openbiliclaw/soul/pipeline.py \
  src/openbiliclaw/cli.py \
  src/openbiliclaw/integrations/openclaw/bootstrap.py \
  tests/test_config.py \
  tests/test_api_app.py \
  tests/test_soul_engine.py \
  tests/test_pipeline_advanced.py \
  tests/test_cli.py \
  tests/test_openclaw_adapter.py
```

Expected: pass.

```bash
uv run --extra dev mypy src/openbiliclaw
```

Expected: pass, or report existing unrelated failures separately with exact output.

**Step 4: Check docs and whitespace**

```bash
git diff --check
rg -n "cfgDiscoveryCron|发现 Cron 表达式" extension docs
rg -n "discovery_cron" docs/modules/config.md config.example.toml src/openbiliclaw
```

Expected:

- `git diff --check` passes.
- `cfgDiscoveryCron` and "发现 Cron 表达式" are absent from popup code/tests.
- `discovery_cron` remains only in backend compatibility paths and docs that label it legacy.

**Step 5: Manual smoke**

Run the backend and open the popup settings page:

```bash
openbiliclaw start
```

Manual checks:

- scheduler settings show disconnect grace and runtime controls;
- no cron field is visible;
- changing refresh interval / speculation TTL saves successfully;
- `openbiliclaw config-show` shows the saved values;
- after daemon restart, values remain in `config.toml`.

**Step 6: Final status**

Report:

- files changed;
- tests run and results;
- any tests not run;
- any remaining known limitation, especially that `discovery_cron` remains a legacy persisted field but is intentionally not a runtime scheduler.
