# Pool Update Preserves Recommendations Fix Spec

## Problem

When the runtime emits `refresh.pool_updated`, both the browser extension side panel and the mobile Web recommendation view treat it as a full recommendation-list refresh. That refresh calls `GET /api/recommendations`, which returns the newest top window from the recommendation history. If a user has already appended older items by scrolling, the local list is replaced and those appended items disappear.

## Root Cause

- `src/openbiliclaw/runtime/refresh.py` emits `refresh.pool_updated` after periodic pool-copy precompute and refresh completion.
- `extension/popup/popup.js` handles that event by calling `scheduleRecommendationsRefresh()`, which eventually runs `initializeRecommendations()` and assigns `state.recommendations = recommendationResult.value`.
- `src/openbiliclaw/web/js/views/recommend.js` handles the same event by calling `scheduleRecommendationItemsRefresh()`, fetching `/api/recommendations`, and patching `state.recommendations`.
- `/api/recommendations` is a bootstrap/top-window endpoint, not a pagination endpoint. It reads latest rows ordered by `created_at DESC, id DESC`, so it cannot preserve locally appended history.

## Desired Behavior

`refresh.pool_updated` is a pool-status signal, not a list-replacement signal.

- Popup and mobile Web must merge the runtime payload into the pool/header state.
- Existing recommendation cards, including appended cards, must remain in place.
- User-initiated replacement flows still replace the list:
  - popup manual "换一批"
  - mobile Web "换一批" / pull refresh
  - initial page load
  - backend reconnect / init / config reload flows that already perform broader hydration
- Append behavior remains unchanged and continues to call `/api/recommendations/append` with displayed IDs excluded.

## Implementation Scope

- Modify `extension/tests/runtime-refresh-coalescing.test.ts` first so it fails while current code still schedules list refreshes for `refresh.pool_updated`.
- Modify `extension/popup/popup.js` to stop scheduling `initializeRecommendations()` from the `refresh.pool_updated` handler. Generic runtime-status merge/render at the top of the stream handler remains the update path.
- Modify `src/openbiliclaw/web/js/views/recommend.js` to remove the pool-update-triggered recommendation fetch. The event should only patch `runtimeStatus` and rerender the header.
- Update docs that currently say `refresh.pool_updated` refreshes the recommendation list.

## Acceptance Criteria

- A `refresh.pool_updated` event does not call `fetchRecommendations()`, `initializeRecommendations()`, `scheduleRecommendationsRefresh()`, or `scheduleRecommendationItemsRefresh()` in either frontend.
- Header/pool counts still update from the runtime payload.
- Manual "换一批" still replaces the list.
- Scroll append still preserves old cards and appends new cards.
- Targeted extension tests pass.
