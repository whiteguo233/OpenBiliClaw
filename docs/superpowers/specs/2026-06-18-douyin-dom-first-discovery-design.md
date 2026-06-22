# Douyin DOM-First Discovery Design

## Goal

Reduce Douyin risk-control triggers by making `search`, `hot`, and `feed` discovery start from real browser DOM interactions. The extension may still passively observe responses caused by those interactions, but it must not use shortcut URL jumps or主动构造 discovery API requests as the primary path.

## Current Behavior

- `search` opens `https://www.douyin.com/`, then jumps directly to `/search/<keyword>?type=video`.
- `hot` opens `https://www.douyin.com/`, then jumps directly to `/hot/<sentence_id>`.
- `feed` opens the homepage, then asks the MAIN-world harvester to actively call `/aweme/v1/web/tab/feed/`.
- `DouyinPluginSearchClient` falls back to backend direct-cookie calls when plugin `search`, `hot`, or `feed` returns empty.

## Target Behavior

- `search` opens the Douyin homepage, finds the search input through DOM selectors, types the keyword, submits it, then collects rendered DOM items and passively observed page responses.
- `hot` opens the Douyin homepage, enters the hot surface through DOM selectors and clicks a matching hot item when possible, then collects rendered DOM items and passively observed page responses.
- `feed` opens the Douyin homepage, scrolls the recommendation feed, then collects rendered DOM items and passively observed page responses.
- MAIN-world fetch/XHR tapping remains installed, but only as passive observation of requests made by Douyin's own page after DOM operations.
- Active API bridge request types for `search`, `hot`, and `feed` are no longer used by the content script discovery path.
- Plugin-empty results do not fall back to backend direct-cookie `search`, `hot`, or `feed` by default.

## Components

- `extension/src/background/dy-task-dispatcher.ts`
  - Stop navigating search/hot tasks to shortcut URLs.
  - Keep all discovery task tabs starting at `https://www.douyin.com/`.
  - Send execution messages after homepage load and fetch-tap injection.

- `extension/src/content/douyin.ts`
  - Use DOM helpers to trigger search and hot flows.
  - Use DOM scrolling for feed.
  - Keep passive fetch-tap listeners for page-originated responses.
  - Remove active calls to `harvestSearchViaApiBridge`, `harvestHotRelatedViaApiBridge`, and `harvestFeedViaApiBridge` from discovery runs.

- `extension/src/main/dy-fetch-tap.ts`
  - Keep passive fetch/XHR response parsing.
  - Leave explicit API harvester code in place only if still used by bootstrap scope APIs; discovery code should not call search/hot/feed bridge request types.

- `src/openbiliclaw/sources/douyin_plugin_search.py`
  - Add a default-off direct fallback switch for plugin search/hot/feed.
  - Preserve creator direct delegation because it is separate from the three requested channels and not exposed by current CLI/runtime.

## Error Handling

- If DOM search input, hot entry, or hot item cannot be found, return an empty or failed plugin task result without backend direct-cookie fallback.
- If feed scrolling yields no rendered DOM or passive response items, return an empty feed task result.
- Budget-exhaustion sentinel behavior remains unchanged for keyword planner rollback.

## Testing

- Extension dispatcher tests verify search/hot/feed no longer expose shortcut URL navigation.
- Content-script tests verify discovery execution is DOM-first by asserting exported helper metadata does not allow active bridge usage for `search`, `hot`, or `feed`.
- Python tests verify plugin-empty `search`, `hot`, and `feed` no longer call direct-cookie fallback by default, while an explicit compatibility flag can still enable old fallback if needed.

## Documentation

- Update Douyin-related module documentation and changelog to state that discovery is DOM-first with passive observation, not direct API crawling.
