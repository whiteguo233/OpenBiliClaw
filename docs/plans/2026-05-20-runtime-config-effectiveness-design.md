# Runtime Config Effectiveness Design

## Goal

Make the scheduler-related configuration surface truthful: every setting shown in the popup either affects the live runtime or is removed from the UI, and every important runtime tuning value that users need for cost/frequency control is configurable, persisted, documented, and hot-reloaded.

## Problem Statement

The current config surface has drifted from the runtime implementation in three ways.

First, `discovery_cron` is exposed by `config.toml`, `/api/config`, docs, and the popup settings page, but `ContinuousRefreshController` does not read it. Discovery runs from a `check_interval_seconds=60` loop and then applies internal conditions:

- `signal_event_threshold=6` triggers `search + related_chain`
- `trending_refresh_hours=3` triggers `trending`
- `explore_refresh_hours=12` triggers `explore`
- `discovery_limit=30` caps each runtime discovery wave

Editing the cron expression cannot change any of those values.

Second, the popup exposes seven speculation settings and `/api/config` persists all seven:

- `speculation_interval_minutes`
- `speculation_ttl_days`
- `speculation_cooldown_days`
- `speculation_confirmation_threshold`
- `speculation_max_active`
- `speculation_max_primary_interests`
- `speculation_max_secondary_interests`

But `SoulEngine` constructs `InterestSpeculator(llm_service=..., data_dir=...)` with no config values, so the live runtime always uses the class defaults. These fields are therefore cosmetic in the daemon and OpenClaw paths.

Third, `extension_disconnect_grace_seconds` is a real config value consumed by `background_llm_work_allowed()`, but the popup settings page does not expose it. Users can toggle "关闭浏览器后停止后台" but cannot tune the grace window without editing TOML by hand.

There are also important LLM/cost-adjacent runtime cadences that remain hardcoded:

- `check_interval_seconds=60`
- `signal_event_threshold=6`
- `trending_refresh_hours=3`
- `explore_refresh_hours=12`
- `discovery_limit=30`
- `proactive_push_interval_seconds=120`
- `ProfileUpdatePipeline` idle speculator tick interval = 30 minutes

## Chosen Approach

Use `Config.scheduler` as the source of truth for the actual runtime knobs, and make the popup settings page display those knobs instead of the dead cron field.

### 1. Keep `discovery_cron` as legacy config, remove it from the popup

Do not add a cron scheduler. A cron expression would conflict with the existing pool-refill and event-driven model: the runtime must keep topping up the candidate pool when it falls below `pool_target_count`, and it must still respond quickly to strong user signals. Translating that behavior into a single cron field would be misleading.

Instead:

- keep `scheduler.discovery_cron` load/save/API support for backward compatibility;
- stop showing `cfgDiscoveryCron` in the popup;
- update docs to mark `discovery_cron` as legacy / currently ignored by the runtime;
- add explicit runtime fields for the values the daemon actually uses.

### 2. Add explicit runtime tuning fields

Add scheduler fields with the current hardcoded defaults:

| Field | Default | Runtime target |
|---|---:|---|
| `refresh_check_interval_seconds` | `60` | `ContinuousRefreshController.check_interval_seconds` |
| `signal_event_threshold` | `6` | `ContinuousRefreshController.signal_event_threshold` |
| `trending_refresh_hours` | `3` | `ContinuousRefreshController.trending_refresh_hours` |
| `explore_refresh_hours` | `12` | `ContinuousRefreshController.explore_refresh_hours` |
| `discovery_limit` | `30` | `ContinuousRefreshController.discovery_limit` |
| `proactive_push_interval_seconds` | `120` | `ContinuousRefreshController.proactive_push_interval_seconds` |
| `speculator_idle_interval_minutes` | `30` | `ProfileUpdatePipeline` idle speculation cadence |

These values should round-trip through `load_config()` / `save_config()`, appear in `GET /api/config`, be accepted by `PUT /api/config`, and be passed into newly constructed runtime services during hot reload.

Use conservative normalization to avoid accidental runaway cost:

- `refresh_check_interval_seconds`: minimum `15`, default `60`
- `signal_event_threshold`: minimum `1`, default `6`
- `trending_refresh_hours`: minimum `1`, default `3`
- `explore_refresh_hours`: minimum `1`, default `12`
- `discovery_limit`: range `1..60`, default `30`
- `proactive_push_interval_seconds`: minimum `30`, default `120`
- `speculator_idle_interval_minutes`: minimum `5`, default `30`

Invalid values should fall back to defaults rather than crashing config load.

### 3. Wire all speculation settings into `InterestSpeculator`

Add a small runtime config object or explicit keyword arguments so `SoulEngine` can construct `InterestSpeculator` with the scheduler values. The internal class defaults remain as fallback for tests and direct construction, but config-backed builders must pass the live values.

Production wiring points:

- `RuntimeContext.rebuild_from_config()`
- CLI `_build_soul_engine()`
- `build_openclaw_adapter_services()`

The `ProfileUpdatePipeline` also needs a constructor argument for `speculator_idle_interval_minutes`, because the speculator's own `generation_interval_minutes` is only one gate. Without wiring the pipeline idle interval, a very low speculation interval still only gets checked every 30 minutes during idle periods.

### 4. Expose useful controls in the popup

Add a settings input for `extension_disconnect_grace_seconds` next to `cfgPauseOnDisconnect`.

Replace the dead `cfgDiscoveryCron` field with actual runtime controls:

- refresh loop interval seconds
- signal event threshold
- trending refresh hours
- explore refresh hours
- discovery batch limit
- proactive push interval seconds
- speculator idle interval minutes

Keep the existing seven speculation fields, but after backend wiring they become real controls.

### 5. Preserve hot reload semantics

`PUT /api/config` already rebuilds runtime components. This change should keep that behavior:

1. User edits scheduler values in the popup.
2. Popup sends the expanded `scheduler` payload.
3. Backend writes `config.toml`.
4. `RuntimeContext.rebuild_from_config()` creates a new `SoulEngine`, `ContinuousRefreshController`, and `AccountSyncService`.
5. The new controller receives the configured refresh intervals and limits.
6. The new soul engine receives the configured speculator values and pipeline idle interval.

No in-place mutation of existing controllers is required.

## Alternatives Considered

### A. Make `discovery_cron` real

This would require adding cron parsing and deciding how cron interacts with pool backfill, event-triggered refresh, and source quota deficits. It would also make the default `"0 */8 * * *"` misleading because the daemon currently checks every minute and refills whenever the pool is below target. This is more complexity for a worse mental model.

### B. Hide all advanced frequency controls

This would remove the dead cron field and reduce UI clutter, but it would not solve the original user need: users want to understand and tune LLM frequency/cost. The runtime would still have important hardcoded spend controls.

### C. Recommended: remove dead cron from UI and expose real runtime controls

This is the clearest contract. The config page only shows values that affect runtime behavior, and the docs can describe the actual event/pool driven scheduler instead of pretending a cron expression controls it.

## Data Flow

### Runtime refresh controls

1. `load_config()` reads scheduler runtime fields with defaults and normalization.
2. `/api/config` exposes those fields.
3. Popup `populateForm()` fills the scheduler settings form.
4. Popup `collectForm()` submits the same fields.
5. `PUT /api/config` validates/coerces, saves TOML, and rebuilds runtime services.
6. `ContinuousRefreshController(...)` receives:
   - `check_interval_seconds`
   - `signal_event_threshold`
   - `trending_refresh_hours`
   - `explore_refresh_hours`
   - `discovery_limit`
   - `proactive_push_interval_seconds`
7. The next loop tick uses the new values.

### Speculation controls

1. `load_config()` reads all seven `speculation_*` fields plus `speculator_idle_interval_minutes`.
2. `RuntimeContext.rebuild_from_config()` passes those values to `SoulEngine`.
3. `SoulEngine` passes the seven speculation fields to `InterestSpeculator`.
4. `SoulEngine` passes `speculator_idle_interval_minutes` to `ProfileUpdatePipeline`.
5. `ProfileUpdatePipeline.tick()` decides when to call `InterestSpeculator.tick()`.
6. `InterestSpeculator` uses the configured generation interval, TTL, cooldown, confirmation threshold, and caps.

### Extension disconnect grace

1. Popup reads `scheduler.extension_disconnect_grace_seconds` from `/api/config`.
2. User edits the value.
3. Popup submits it in `PUT /api/config`.
4. The runtime config is rebuilt.
5. `background_llm_work_allowed()` reads the updated grace seconds through the existing scheduler object.

## Error Handling

- Invalid numeric values from TOML or API are normalized to safe defaults.
- Values below minimums are clamped or defaulted consistently; prefer defaulting for invalid input and clamping only where existing config code already does so.
- `discovery_cron` remains accepted in `PUT /api/config` for backward compatibility, but it should not be shown in the popup and should be documented as legacy.
- Direct `SoulEngine(...)`, `ProfileUpdatePipeline(...)`, and `ContinuousRefreshController(...)` test construction should keep current defaults when new arguments are omitted.
- Hot reload failures should keep the existing rollback behavior in `PUT /api/config`.

## Testing

- Config tests for defaults, TOML load, TOML save, invalid numeric fallback, and API round-trip for every new scheduler field.
- Runtime construction tests proving `RuntimeContext.rebuild_from_config()` passes configured values into `ContinuousRefreshController`.
- Soul tests proving configured speculation values reach `InterestSpeculator` and affect generated spec TTL / confirmation threshold / generation interval / caps.
- Pipeline tests proving `speculator_idle_interval_minutes` changes idle tick cadence.
- OpenClaw bootstrap tests proving the direct adapter path receives the same runtime scheduler values.
- Extension tests proving:
  - `cfgDiscoveryCron` is no longer present;
  - new scheduler field IDs exist;
  - `populateForm()` reads them;
  - `collectForm()` submits them.
- Documentation checks through `ruff`, targeted `pytest`, extension node tests, and `git diff --check`.

## Documentation

Update:

- `config.example.toml`
- `docs/modules/config.md`
- `docs/modules/runtime.md`
- `docs/modules/discovery.md`
- `docs/modules/soul.md`
- `docs/modules/extension.md`
- `docs/changelog.md`

Architecture diagrams do not need structural changes because this is config wiring rather than a new module or data flow. If the diagrams mention `discovery_cron`, replace it with the real runtime fields.
