---
name: bilibili_search
description: Search for videos on Bilibili using keyword queries generated from user interests.
user-invocable: true
version: "0.1.0"
author: OpenBiliClaw
tags:
  - bilibili
  - search
  - discovery
---

# Bilibili Search Skill

Search for videos on Bilibili based on the user's interests and soul profile. Supports both LLM-driven automatic query generation (for discovery cycles) and direct keyword search (for explicit user requests).

## When to Use

- During content discovery cycles
- When the user explicitly asks to find content on a topic
- When generating exploratory searches for new interest domains

## How It Works

1. **Query generation** — If no explicit `keywords` are provided, the skill uses an LLM to generate multiple search queries from the user's soul profile (top interests, cognitive style, deep needs). If `keywords` are provided, they are used directly.
2. **Concurrent search** — Each query is sent to the Bilibili WBI-signed search endpoint concurrently. A dedicated API client is used per strategy to isolate rate-limiting.
3. **Resilience** — Individual query failures (including Bilibili `412 Precondition Failed`) degrade gracefully and do not interrupt the overall flow.
4. **Scoring** — Results are evaluated against the soul profile via LLM and assigned a `relevance_score` (0.0–1.0). Items below the threshold are discarded.
5. **Output** — Returns scored `DiscoveredContent` items.

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `keywords` | `string` | No | `""` | Search query string. If empty, the skill auto-generates queries from the soul profile. |
| `page` | `integer` | No | `1` | Page number for paginated results. |
| `limit` | `integer` | No | `20` | Maximum number of results to return. |
| `order` | `string` | No | `"totalrank"` | Sort order. One of: `"totalrank"` (relevance), `"pubdate"` (newest), `"click"` (most viewed), `"dm"` (most commented). |

## Input Schema (JSON Schema)

```json
{
  "type": "object",
  "properties": {
    "keywords": {
      "type": "string",
      "description": "Search query string. Auto-generated from profile if empty."
    },
    "page": {
      "type": "integer",
      "minimum": 1,
      "default": 1
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 20
    },
    "order": {
      "type": "string",
      "enum": ["totalrank", "pubdate", "click", "dm"],
      "default": "totalrank"
    }
  }
}
```

## Output

Each result is a `DiscoveredContent` object with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `bvid` | `string` | Bilibili video BV ID |
| `title` | `string` | Video title |
| `up_name` | `string` | Creator's display name |
| `up_mid` | `integer` | Creator's user ID |
| `cover_url` | `string` | Video cover image URL |
| `duration` | `integer` | Video length in seconds |
| `view_count` | `integer` | Play count |
| `like_count` | `integer` | Like count |
| `description` | `string` | Video description |
| `tags` | `string[]` | Video tags |
| `relevance_score` | `number` | LLM-evaluated relevance (0.0–1.0) |
| `relevance_reason` | `string` | Human-readable reason for the score |
| `source_strategy` | `string` | Always `"search"` for this skill |
| `topic_key` | `string` | Semantic topic classification |
| `style_key` | `string` | Content style classification |

## Requirements

- Bilibili Cookie authentication is required for search to function. Without it, Bilibili may return `412 Precondition Failed` and the skill will return empty results.
- The skill connects to Bilibili's public API at `https://api.bilibili.com`; no additional API keys are needed beyond the B站 Cookie.

## Limitations

- **Rate Limiting**: Bilibili applies rate-limiting and anti-bot protections. Under heavy usage, search requests may return `412 Precondition Failed` and degrade to empty results.
- **Platform Restriction**: The skill returns content from Bilibili only. Cross-platform search is not supported.
- **Cookie Dependency**: Search functionality depends on valid Bilibili Cookie authentication.

## Special Case Handling

### Empty Keywords
When `keywords` is empty or not provided:
1. The skill automatically generates search queries based on the user's soul profile (top interests, cognitive style, deep needs)
2. Multiple queries are generated and executed concurrently
3. Results are aggregated and deduplicated

### 412 Precondition Failed
When Bilibili returns `412 Precondition Failed`:
1. The skill treats this as a temporary failure for that query
2. Other concurrent queries continue execution
3. The skill returns results from successful queries only
4. If all queries fail, returns an empty result set with appropriate logging

### Network Errors & Timeouts
- **Connection Timeout**: Retries once before marking the query as failed
- **DNS Failure**: Falls back gracefully without breaking other queries
- **SSL Errors**: Logs the error and skips to next query

### Empty Search Results
- If no results are found for a query, returns an empty list for that query
- If all queries return empty, the skill returns an empty result set
- The discovery engine will try alternative strategies

### Invalid Parameters
- **Invalid `page`**: Clamped to minimum value of 1
- **Invalid `limit`**: Clamped to range [1, 50]
- **Invalid `order`**: Falls back to `"totalrank"`
- **Empty string keywords**: Treated as auto-generate mode

### Special Characters in Keywords
- Keywords are URL-encoded before sending to Bilibili API
- Unicode characters are supported
- Long keywords (>100 characters) are truncated with a warning

### API Response Edge Cases
- **Unexpected response format**: Gracefully parses what it can, logs warnings for missing fields
- **Partial results**: Returns valid items even if some fields are missing
- **Rate limiting headers**: Respects `Retry-After` headers when present
