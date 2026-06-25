# Zhihu Multi-Source Discovery Design

## Goal

Extend Zhihu discovery beyond keyword search by adding plugin-backed hot, feed, creator, and related sources. All paths run through the installed browser extension so requests use the user's active Zhihu login state.

## Sources

| Source | Task type | Strategy tag | Seed |
| --- | --- | --- | --- |
| Keyword search | `search` | `zhihu-search` | unified keyword planner or profile interests |
| Hot board | `hot` | `zhihu-hot` | Zhihu hot list |
| Home feed | `feed` | `zhihu-feed` | logged-in Zhihu home recommendation feed |
| Creator timeline | `creator` | `zhihu-creator` | authors seen in recent Zhihu bootstrap/search results |
| Related expansion | `related` | `zhihu-related` | URLs/content IDs from recent Zhihu candidates |

## Architecture

The backend keeps a single `zhihu_tasks` queue and extends task payloads with source-specific fields. The extension dispatcher accepts the new task types and sends them to the Zhihu content script. The content script normalizes every result to the existing Zhihu item shape, setting `source_strategy` so the backend can map items to `DiscoveredContent` and enqueue them into `discovery_candidates`.

Runtime discovery stays fetch-only. It does not write profile memory or trigger profile generation. The `ZhihuDiscoveryProducer` decides which task types to run based on configured source modes, per-strategy budgets, due intervals, and available seeds. Keyword claims remain dedicated to search; hot/feed/creator/related do not burn keyword planner claims.

## Error Handling

Login-required redirects or 401/403 responses return `login_required`/`failed` task results. Empty source surfaces return an empty task result and do not mark unrelated keyword claims as failed. Creator and related sources skip invalid seeds instead of failing the whole cycle.

## End-to-End Verification

The real E2E path builds the extension, syncs it to the installed unpacked extension directory, calls the existing extension hot-reload endpoint, and then uses CLI commands to enqueue each Zhihu source task. Verification checks task completion and `discovery_candidates` rows with `source_platform='zhihu'` and the expected `source_strategy` values.
