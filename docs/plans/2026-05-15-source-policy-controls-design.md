# Source Policy Controls Design

## Goal

Unify discovery source switches, pool share ratios, init decisions, runtime quota math, and popup configuration so disabled platforms never strand pool quota or run background discovery work, while enabled platforms can be weighted by user-confirmed ratios.

## Current Gaps

- `RuntimeContext` drops disabled optional source shares for the main API runtime, but OpenClaw bootstrap still passes raw `scheduler.pool_source_shares`.
- `sources.xiaohongshu.enabled` and `sources.youtube.enabled` exist in the config layer but are not exposed by `/api/config` or the popup settings page.
- `init` persists the Xiaohongshu decision but does not persist Douyin or YouTube source switches, and it cannot ask the user to accept or override source share ratios.
- YouTube is recognized as a pool source family, but enabled YouTube shares do not create a replenishment plan, so a configured YouTube quota can remain unfilled.
- The popup can edit Bilibili/XHS/Douyin ratios, but not YouTube, and cannot generate a recommended ratio from observed events.

## Chosen Approach

Use a single source policy helper as the shared authority for optional source state and effective pool shares. Bilibili remains the core source and is always enabled in this iteration; users can edit its share ratio but cannot disable it. Xiaohongshu, Douyin, and YouTube each use `sources.<platform>.enabled` as their switch. `scheduler.pool_source_shares` continues to store user defaults for all platforms, including disabled ones, but runtime quota calculations ignore disabled optional sources so their shares are redistributed to enabled sources.

`init` will persist the platform switches from the user's onboarding choices. After event collection it will compute a deterministic suggested ratio from observed platform event counts, show the suggestion, and let the user accept it or enter manual ratios. The suggestion avoids LLM cost and uses dampening/clamping so large imports do not dominate the pool.

The popup settings page will expose optional source switches, all four source ratios, and a backend-powered "suggest ratios from events" action. The suggestion endpoint returns event counts plus proposed shares; the UI fills the ratio inputs and leaves final persistence to the existing Save button.

## Data Flow

1. `config.toml` stores `sources.xiaohongshu.enabled`, `sources.douyin.enabled`, `sources.youtube.enabled`, and `scheduler.pool_source_shares`.
2. A shared helper builds:
   - `source_enabled_map`: Bilibili true, optional sources from config.
   - `effective_pool_source_shares`: configured shares with disabled optional sources removed.
   - `suggested_pool_source_shares`: deterministic ratios from event counts and enabled sources.
3. Main API runtime and OpenClaw bootstrap pass effective shares to `ContinuousRefreshController`.
4. `ContinuousRefreshController` uses effective shares to calculate source targets, warn for missing producers, and build replenishment plans.
5. `/api/config` exposes and updates optional source switches and all source shares.
6. `/api/config/source-share-suggestion` returns event counts and suggested shares for the current enabled sources.
7. Popup settings lets users edit switches/ratios and request a suggestion before saving.

## Error Handling

- Invalid or missing ratio values fall back to existing defaults through `_normalize_pool_source_shares`.
- Disabled optional sources remain in the saved ratio map but are excluded from effective runtime shares.
- If an enabled source has quota but no producer/replenishment path, runtime logs a warning instead of silently stranding quota.
- If event counts are empty, suggested shares fall back to defaults for enabled sources.

## Testing

- Unit tests for source policy helpers: disabled-source filtering, YouTube inclusion, suggestion from event counts, and fallback behavior.
- Runtime tests for source target counts and YouTube replenishment planning.
- OpenClaw bootstrap test proving effective shares are used.
- API tests for exposing/updating XHS/YouTube switches and source share suggestions.
- CLI init tests for persisting Douyin/YouTube enabled flags and accepting/manual ratio prompts.
- Extension tests for popup field IDs and request payloads.

## Documentation

Update `docs/modules/config.md`, `docs/modules/discovery.md`, `docs/modules/extension.md`, `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`, and `docs/changelog.md` because this changes config fields, source data flow, adapter behavior, and user-visible settings.
