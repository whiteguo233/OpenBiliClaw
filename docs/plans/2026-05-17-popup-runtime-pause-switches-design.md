# Popup Runtime Pause Switches Design

## Goal

Give users two prominent, one-click switches in the extension popup to control backend cost:

1. **省钱模式（暂停后台 LLM）** — when on, daemon-owned background work that issues LLM / embedding API calls stops ticking, but explicit user actions (events POST, recommendations GET, manual /refresh) still work. Reuses the existing `scheduler.enabled` config field, which is currently surfaced in the settings drawer but **not actually honored** by most runtime background work.
2. **关闭浏览器后停止后台** — when on, the daemon pauses the same daemon-owned background LLM / embedding work once the extension has been disconnected for longer than a grace period. Reusing the existing `/api/runtime-stream` WebSocket as the presence signal — no separate heartbeat channel needed.

Both switches are user-facing cost controls, so they live at the top of the popup main card (not buried in the settings drawer) and persist via `PUT /api/config`.

## Current Gaps

- `SchedulerConfig.enabled` exists (`src/openbiliclaw/config.py:153`), is exposed through `GET/PUT /api/config` (`src/openbiliclaw/api/app.py:3292`, 3483), and has a checkbox in the settings drawer (`extension/popup/popup.html:3883`), but **no loop in `ContinuousRefreshController` checks it**. A user toggling it off today changes a TOML field with zero runtime effect on most LLM spend — the same six refresh-controller loops keep ticking.
- There is no mechanism for the backend to know whether the extension / browser is currently running. `service-worker.ts` opens a WebSocket to `/api/runtime-stream` at startup and reconnects with backoff, but the backend treats every client connection independently and never aggregates presence. The existing backend WS handler also only waits on outbound event queues; without a reader task, a client that disconnects during an idle period may not be detected until the next event send.
- The popup has no prominent runtime control. The only existing cost-related switch (`cfgSchedulerEnabled`) is six clicks deep inside the settings drawer, scheduler section.
- `_loop_proactive_push`, `_loop_xhs_producer`, and `_loop_douyin_producer` consume LLM tokens (delight prompts, keyword generation, soul-driven search) but only `douyin_producer` has any opt-out gating today, and even that ignores `scheduler.enabled` for the loop tick itself.
- `AccountSyncService.run_forever()` is also daemon-owned background work and can call `soul_engine.analyze_events(events)` after fetching account-side history / favorites / following. It is not part of `ContinuousRefreshController`, so gating only the six refresh loops would still allow periodic LLM calls.
- `RuntimeContext.restart_background_tasks()` fires startup / hot-reload one-shots (`speculator.force_tick()` and detached `prewarm_pool_mmr_embeddings()`) that can spend LLM / embedding tokens before the first refresh tick. Those also need the same gate.

## Chosen Approach

Treat `scheduler.enabled` as the authoritative master gate for daemon-owned **background LLM / embedding work** and wire the same predicate into:

- all six loops in `ContinuousRefreshController`;
- the profile-ready classify hook inside `_loop_refresh`;
- `AccountSyncService.run_forever()` / `sync_if_due()` before it fetches and analyzes account events;
- startup / hot-reload one-shots in `RuntimeContext.restart_background_tasks()`;
- OpenClaw's direct `ContinuousRefreshController` construction path.

Add a new `scheduler.pause_on_extension_disconnect` flag plus `scheduler.extension_disconnect_grace_seconds` to drive presence-based pausing. Add a `PresenceTracker` singleton owned by `RuntimeContext` that the `/api/runtime-stream` WebSocket handler updates on connect/disconnect. Background LLM work is "active" iff:

```
scheduler.enabled AND (
    NOT scheduler.pause_on_extension_disconnect
    OR PresenceTracker.is_present(grace_seconds=scheduler.extension_disconnect_grace_seconds)
)
```

Only daemon-owned background work consults this gate. Explicit API endpoints (POST /events, GET /recommendations, manual /refresh trigger, config endpoints, `/api/runtime-stream` itself) remain unconditional so the extension can always interact with the daemon, observe state, and re-enable work.

Two new toggles are added to the top of the popup main card. The toggles call `PUT /api/config` immediately on change; the existing `cfgSchedulerEnabled` checkbox in the settings drawer continues to work and stays in sync via the same fetch-on-open flow. A new `cfgPauseOnDisconnect` checkbox is added to the settings drawer's scheduler section for parity.

Presence is **shared across all clients**, not per-client: if any client (popup, content script tab, side panel) has an open `/api/runtime-stream` connection, the daemon counts as present. The tracker stores `active_count` and `last_disconnect_at`; on the transition `1 → 0` it records the timestamp; on `0 → 1` it clears it. `is_present()` returns `True` when `active_count > 0` OR `last_disconnect_at` is within the grace window.

On daemon **startup** the tracker is initialized as "never connected, grace period running from startup time". This avoids a hard pause the moment the daemon boots before the extension has a chance to dial in. After the first connection, normal disconnect → grace → pause behavior applies.

## Data Flow

1. `config.toml` stores `scheduler.enabled`, `scheduler.pause_on_extension_disconnect`, and `scheduler.extension_disconnect_grace_seconds`.
2. `RuntimeContext` owns a `PresenceTracker` instance and exposes it to `ContinuousRefreshController`, `AccountSyncService`, and startup / hot-reload one-shot guards at build time. OpenClaw bootstrap creates its own tracker so direct runtime construction follows the same config semantics.
3. `/api/runtime-stream` WebSocket handler calls `presence.on_connect()` after accept + successful subscription and `presence.on_disconnect()` in a single finally block. The handler must run a reader task (or equivalent receive pump) alongside the outbound event writer so disconnect is detected even when no runtime events are being published.
4. Each background loop in `ContinuousRefreshController` (`_loop_refresh`, `_loop_pool_precompute`, `_loop_soul_pipeline`, `_loop_xhs_producer`, `_loop_douyin_producer`, `_loop_proactive_push`) starts each LLM-touching iteration by calling `self._llm_work_allowed()` and skips the body (sleeps and continues) when it returns False. `_on_profile_ready_if_first_time()` is also behind the gate because it can call `classify_pool_backlog()`.
5. `AccountSyncService.sync_if_due()` checks the same gate before fetching account data and before any `analyze_events()` call. When blocked, it returns a skipped result without updating `last_account_sync_at`, so the next allowed tick can still sync normally.
6. `GET /api/config` exposes the two new scheduler fields. `PUT /api/config` accepts updates and triggers `rebuild_from_config()`, which constructs new swappable services with the updated values. The `PresenceTracker` survives rebuilds (lives on `RuntimeContext`, not the controller/service) so toggling settings does not lose presence state.
7. Popup renders two prominent toggles bound to the same state used by the settings drawer; flipping either issues `PUT /api/config` and re-fetches to confirm.

## Error Handling

- Invalid grace values (negative, zero, or non-numeric) fall back to the default (90s) through explicit loader / update-config validation. Do not rely on dataclass or Pydantic coercion for this field.
- WebSocket exceptions (connect failures, abrupt closes, idle disconnects) MUST still trigger `on_disconnect()` via a `try/finally` and receive-side disconnect detection so presence cannot get stuck at `active_count > 0` after a crash or quiet close.
- If `scheduler.enabled=False` is set while a long-running discovery refresh is mid-flight, the in-flight work completes (no preemption) — the gate prevents the **next** tick. This matches existing scheduler semantics and avoids leaving partial state in the pool.
- If both switches are toggled while the daemon is briefly offline (e.g. user restarts daemon), TOML persistence ensures the new state is honoured on next boot; the popup retries the PUT through the existing error toast UI.
- If `pause_on_extension_disconnect=True` but the user is using OpenClaw / CLI directly (no extension), the daemon will pause LLM work after grace expires. The CLI `config-show` output must surface this clearly so headless users don't get bitten. CLI `start` and `serve-api` print/log a one-line WARN at startup if the policy is on.

## Testing

- Unit tests for `PresenceTracker`: connect/disconnect counting, multiple concurrent clients, grace-window expiry, startup grace, transition logging.
- Runtime tests for `ContinuousRefreshController` loop gating: loops skip body when `scheduler.enabled=False`; loops skip body when `pause_on_extension_disconnect=True` and presence is stale; loops run normally when presence is fresh; in-flight work is not preempted.
- Runtime tests for `AccountSyncService` and `RuntimeContext.restart_background_tasks`: account sync and startup one-shots skip LLM / embedding work while blocked and do not stamp due-state timestamps.
- API tests: `GET /api/config` includes both new fields; `PUT /api/config` round-trips both fields; `/api/runtime-stream` connect/disconnect updates presence; concurrent connections do not double-decrement; disconnect during an idle stream (no published events) decrements presence promptly.
- Config persistence tests: `save_config` writes the two new keys; `load_config` reads them with correct defaults when absent.
- Extension static tests: popup HTML contains the two top-card toggle IDs and the new settings drawer checkbox; `collectForm()` sends `pause_on_extension_disconnect`; toggle click handlers issue `PUT /api/config` with the right payload.
- CLI smoke: `openbiliclaw config-show` displays both new flags in the scheduler section.

## Documentation

Update `docs/modules/config.md` (new fields), `docs/modules/api.md` (presence semantics + WS hook), `docs/modules/extension.md` (popup top-card controls), `docs/modules/cli.md` (config-show new rows + WARN), `docs/architecture.md` (new runtime presence singleton + background gate data flow), `docs/spec.md` (runtime gate in the data-flow diagram), `README.md` and `README_EN.md` (the 📌 highlights callout plus top architecture diagram/text if it mentions runtime flow), and `docs/changelog.md`.
