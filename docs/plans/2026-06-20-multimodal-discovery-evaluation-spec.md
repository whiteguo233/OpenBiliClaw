# 2026-06-20 — Multimodal Discovery Evaluation Spec

## 0. Scope

This spec upgrades the discovery evaluator input contract in two steps:

1. Feed richer source metrics into the text evaluator.
2. Add an optional multimodal evaluator path that can inspect compressed cover
   images when the configured evaluation model supports vision input.

Affected paths:

- `DiscoveredContent` and `DiscoveryCandidateWrite` metadata.
- Source normalizers for Bilibili, Xiaohongshu, Douyin, YouTube, and X/Twitter.
- `discovery_candidates` and `content_cache` persistence.
- `ContentDiscoveryEngine.evaluate_content_batch()` and prompt construction.
- `/api/config`, desktop Web settings, and docs for the new discovery toggle.

Out of scope:

- Fetching full detail pages for every candidate.
- OCR/transcription of video frames.
- Replacing recommendation ranking or source quota logic.
- Making non-vision LLM providers accept images.

## 1. Current State

The evaluator already receives a normalized JSON item per candidate:

```json
{
  "bvid": "...",
  "content_id": "...",
  "content_url": "...",
  "source_platform": "...",
  "source_strategy": "...",
  "source_context": "...",
  "content_type": "video|note|tweet|thread",
  "body_text": "...",
  "title": "...",
  "up_name": "...",
  "author_name": "...",
  "description": "...",
  "duration": 123,
  "view_count": 456
}
```

The evaluator does not currently see `cover_url`, `tags`, `like_count`, or
source-specific social metrics. Those fields may be stored for display, but
they do not affect relevance scoring.

Source metadata quality is uneven:

| Source | Current useful fields | Main gap |
|--------|-----------------------|----------|
| Bilibili | title, description, author, cover, duration, view_count, like_count | tags and engagement metrics are not prompt-visible |
| Xiaohongshu | title, author, cover from extension card DOM | note stats are not collected or evaluated |
| Douyin | title/desc, author, cover, duration, view_count, like_count when available | collect/comment/share metrics are dropped |
| YouTube | title, channel, cover, duration, view_count, description snippet | like/comment metrics usually unavailable from scraper |
| X/Twitter | title, body_text, author, cover, view_count, like_count, hashtags | tags are stored but not evaluated |

## 2. Goals

1. Preserve the current source-agnostic evaluator contract.
2. Add common engagement fields so the LLM can distinguish low-signal titles
   from content with stronger platform traction.
3. Collect platform metrics opportunistically from existing list/API surfaces.
4. Keep sources robust: missing metrics must be represented as zero/empty, not
   treated as an error.
5. Add a user-facing `multimodal_evaluation_enabled` switch in settings.
6. When multimodal evaluation is enabled, feed compressed cover images to the
   evaluator for candidates with valid covers.
7. Automatically fall back to text-only evaluation when the provider/model does
   not support image input, and surface that fallback in the settings/status UI.
8. Reduce batch size when image input is active so token/image cost and provider
   limits stay bounded.

## 3. Non-Goals

- Do not require every platform to provide every metric.
- Do not block discovery when images fail to download.
- Do not persist base64 image blobs in SQLite.
- Do not run image analysis as a separate LLM pass before relevance scoring.
- Do not change recommendation serving semantics in this phase.

## 4. Data Model

Extend `DiscoveredContent` and `DiscoveryCandidateWrite` with optional
platform-neutral metrics:

```python
# Canonical watch/view count across video and feed platforms.
# "浏览量" and "观看量" both map here.
view_count: int = 0
like_count: int = 0
favorite_count: int = 0      # Bilibili favorites / X bookmarks when available
collect_count: int = 0       # Xiaohongshu/Douyin collect/save count
comment_count: int = 0
share_count: int = 0
danmaku_count: int = 0       # Bilibili-specific but useful enough to retain
reply_count: int = 0         # Alias-style metric for X/Twitter replies
retweet_count: int = 0       # X/Twitter reposts
bookmark_count: int = 0      # X/Twitter bookmarks
```

Persistence changes:

- Add matching nullable/default-zero columns to `discovery_candidates`.
- Add matching default-zero columns to `content_cache`.
- Keep existing `view_count` and `like_count` columns as the canonical names for
  watch/view and like metrics.
- Keep backwards compatibility for existing rows by defaulting all metrics to
  `0`.
- Include the fields in `to_cache_kwargs()`, candidate enqueue, candidate row
  hydration, and recommendation API output only where already useful for UI.

Prompt-visible fields:

```json
{
  "view_count": 123,
  "like_count": 45,
  "favorite_count": 6,
  "collect_count": 7,
  "comment_count": 8,
  "share_count": 9,
  "danmaku_count": 10,
  "reply_count": 11,
  "retweet_count": 12,
  "bookmark_count": 13,
  "tags": ["optional", "short", "list"]
}
```

`cover_url` remains outside the text JSON when multimodal is off. When
multimodal is on, the JSON carries a small image reference such as
`cover_image_id` rather than the raw image payload.

## 5. Source Collection Requirements

### Bilibili

Map all available API/search/ranking fields:

- `view_count`: existing `play` / `stat.view`
- `like_count`: existing `like` / `stat.like`
- `favorite_count`: `favorites` / `stat.favorite`
- `danmaku_count`: `video_review` / `stat.danmaku`
- `comment_count`: `review` / `stat.reply`
- `share_count`: `stat.share`
- `tags`: existing extension-provided tags, plus API tags only when already
  present in the list result

No additional detail request is required for this phase.

### Xiaohongshu

Extend the extension card metadata shape from:

```ts
{ url, title, author, cover_url }
```

to:

```ts
{
  url,
  title,
  author,
  cover_url,
  like_count?,
  collect_count?,
  comment_count?,
  view_count?
}
```

Collection rules:

- Parse visible count chips from the card DOM only.
- Use tolerant compact-number parsing: `1.2万`, `3k`, `1,234`, `赞 42`.
- Missing or ambiguous values become `0`.
- Do not open note detail pages just to obtain metrics.

### Douyin

Map direct/plugin fields when present:

- `view_count`: `play_count`
- `like_count`: `digg_count`
- `collect_count`: `collect_count`
- `comment_count`: `comment_count`
- `share_count`: `share_count`

For plugin DOM items, parse visible count chips if present; otherwise keep
zeros. Existing `desc`/title behavior stays unchanged.

### YouTube

Map what the current scraper can reliably provide:

- `view_count`: existing
- `like_count`, `comment_count`: only when present in scraper/yt-dlp payload
- `favorite_count`, `collect_count`, `share_count`: default `0`

Do not add extra watch-page fetches for likes/comments in this phase.

### X/Twitter

Map metrics from `tweet_to_dict()["metrics"]`:

- `view_count`: `views`
- `like_count`: `likes`
- `reply_count` and `comment_count`: `replies`
- `retweet_count`: `retweets`
- `share_count`: `retweets + quotes` when no dedicated share count exists
- `bookmark_count` and `favorite_count`: `bookmarks`
- `tags`: extracted hashtags, now prompt-visible as a short list

## 6. Text Evaluation Prompt Changes

The batch evaluator must include the new metrics in each `content_batch` item.

Prompt rules should explain:

- Engagement metrics are supporting context, not a popularity override.
- A high view/like count should not rescue content that conflicts with the Soul
  profile.
- Low metrics should not punish niche content when the topic/style fit is high.
- Platform semantics differ; e.g. Xiaohongshu collect count is stronger intent
  than passive views.
- For `tweet`/`thread`, `body_text` remains the primary content signal.
- For visual platforms, cover image input may add tone/style/context only when
  multimodal mode is active.

Single-item fallback evaluation should also receive `body_text`, `tags`, and the
new metric fields so X/Twitter and multimodal fallback behavior stay consistent.

## 7. Multimodal Evaluation

### Configuration

Add fields to `[discovery]`:

```toml
[discovery]
multimodal_evaluation_enabled = false
multimodal_batch_size = 8
multimodal_image_max_px = 384
multimodal_image_quality = 72
multimodal_image_timeout_seconds = 6
```

Defaults:

- Off by default.
- Batch size `8`: small enough for image-aware APIs, still efficient.
- Max edge `384px`: enough to see composition/text style while controlling
  payload size.
- JPEG/WebP quality `72`.
- Timeout `6s` per image download, with bounded concurrency.

Validation:

- `multimodal_batch_size`: `1..12`
- `multimodal_image_max_px`: `128..768`
- `multimodal_image_quality`: `40..90`
- `multimodal_image_timeout_seconds`: `1..20`

### Image Pipeline

Add a small evaluator-side image helper:

```text
cover_url
  -> download through existing image proxy/cache-compatible HTTP client
  -> verify content-type and max bytes
  -> resize longest edge to config max
  -> convert to JPEG/WebP
  -> attach as image input for this evaluation call
```

Rules:

- Do not store resized image bytes in SQLite.
- Reuse in-memory or disk cache if an existing image cache helper is suitable.
- Skip SVG/GIF/video thumbnails that cannot be converted safely.
- Skip empty/error covers.
- Treat per-image failures as text-only for that item.

### Provider Capability Detection

The evaluator must ask the active evaluation LLM route whether image input is
supported. If the provider/model cannot support images:

- Keep running text-only evaluation.
- Add a runtime/config issue visible to settings:
  `multimodal_unavailable: current evaluation model does not support image input`.
- Log once per process or per config change, not once per candidate.

This is the chosen fallback behavior. The setting may remain enabled so users
can switch models later without re-toggling the feature.

### LLM Request Shape

The prompt still contains the JSON `content_batch`, but items with images include
a stable local reference:

```json
{
  "content_id": "BV...",
  "title": "...",
  "cover_image_id": "image_0",
  "cover_image_note": "compressed cover image attached"
}
```

The LLM message should attach images in the same order as these references.
The system prompt must state that the image is only one signal and should be
used for visual style, topic cues, thumbnail clickbait, product/person/place
recognition, and obvious mismatch detection.

If a provider API requires per-content mixed text/image blocks rather than a
single JSON plus attachments, the service layer should expose a structured
multimodal task method rather than embedding provider-specific shapes inside
`ContentDiscoveryEngine`.

## 8. Batch Sizing

Effective batch size:

```text
if multimodal enabled and provider supports vision and at least one item has an image:
    min(requested_batch_size, discovery.multimodal_batch_size)
else:
    requested_batch_size
```

Existing caps still apply:

- Text hard cap remains unchanged.
- Multimodal hard cap is `12`, independent of text hard cap.
- Runtime producers that call `drain_pending(batch_size=requested_limit)` should
  let the pipeline/evaluator reduce the effective batch size internally.

If a mixed batch contains only some covers, keep the whole batch multimodal only
when at least one image is successfully attached. Items without images remain
in the same JSON batch and are evaluated text-only.

## 9. API and Settings UX

Expose the new fields through `GET /api/config` and `PUT /api/config` under
`discovery`.

Desktop Web settings:

- Add a toggle: `封面多模态评估`.
- Add compact helper status:
  - Off: `关闭`
  - On + vision available: `已启用，批量评估会使用压缩封面`
  - On + unavailable: `当前评估模型不支持图片，已自动按文本评估`
- Add advanced numeric controls only if the page already shows advanced
  discovery settings; otherwise keep them hidden behind existing advanced mode.

The extension settings page should mirror the same toggle only if it already
edits discovery/runtime settings in the current build. If not, desktop Web is
the required settings surface for this phase.

## 10. Error Handling

- Missing metrics: default to `0`.
- Malformed metric strings: default to `0` and continue.
- Image download timeout: skip image, continue text-only for that item.
- Image conversion error: skip image, continue text-only.
- Provider rejects multimodal payload: retry the same batch once as text-only,
  record `multimodal_provider_rejected`, then continue.
- LLM returns count mismatch: preserve current fallback logic, but fallback
  single-item evaluation must include the expanded text fields.

## 11. Testing

Unit tests:

- `DiscoveredContent` and `DiscoveryCandidateWrite` carry new metric fields.
- Candidate enqueue/hydration round-trips all new metric fields.
- Bilibili, Xiaohongshu, Douyin, YouTube, and X normalizers map available
  metrics correctly and tolerate missing fields.
- Compact count parser handles `1.2万`, `3k`, `1,234`, `赞 42`, and empty text.
- Batch prompt includes metrics and tags.
- Single fallback prompt includes `body_text`, metrics, and tags.
- Multimodal batch-size reduction applies only when enabled and vision is
  available.
- Provider unsupported path falls back to text-only and reports a config/runtime
  issue.

Integration tests:

- Enqueued XHS/Douyin/Bili extension candidates with metrics reach
  `discovery_candidates` and then `content_cache`.
- A fake vision-capable LLM receives image references and compressed image
  payloads.
- A fake non-vision LLM receives no image payload and still evaluates text.

Manual verification:

- Enable multimodal evaluation in desktop settings.
- Use a vision-capable evaluation model and confirm logs show smaller
  multimodal batches.
- Use a non-vision evaluation model and confirm the UI shows text-only fallback.
- Verify recommendations still render covers normally and no base64 image blobs
  are persisted in SQLite.

## 12. Documentation Updates

Required docs:

- `docs/modules/discovery.md`: update evaluator input schema and multimodal
  flow.
- `docs/modules/config.md`: document new `[discovery]` fields.
- `docs/modules/extension.md`: document added XHS/Douyin/Bili metric fields if
  extension collectors change.
- `docs/changelog.md`: add a short entry for richer discovery metrics and
  optional cover-aware evaluation.

Architecture docs only need updates if implementation introduces a new shared
image-processing service rather than a small evaluator helper.

## 13. Acceptance Criteria

1. Text-only evaluation includes title, description/body text, tags, view count,
   like count, and available source engagement metrics for all sources.
2. Existing candidates without new metric columns still evaluate successfully.
3. XHS and Douyin extension card metadata can include visible engagement counts
   without requiring detail-page navigation.
4. `multimodal_evaluation_enabled=false` preserves current text-only behavior
   except for the newly visible metrics.
5. `multimodal_evaluation_enabled=true` with a vision-capable model sends
   compressed cover images for candidates with valid covers.
6. Multimodal evaluation uses an effective batch size no larger than
   `multimodal_batch_size`.
7. Non-vision evaluation models automatically fall back to text-only evaluation
   and surface a clear settings/status message.
8. Image download/conversion failures do not fail the discovery batch.
9. No image bytes are persisted in `discovery_candidates` or `content_cache`.
10. Tests cover the new data fields, prompt shape, batch sizing, and fallback
    behavior.
