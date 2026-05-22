# Mobile Recommend Preload And Autoload Design

## Goal

Improve the mobile recommendation feed so cover images are warmed before the user scrolls to them, and the next batch loads automatically near the bottom while keeping the existing button as a fallback.

## Approach

The mobile web recommendation page already has a `POST /api/recommendations/append` path through `appendRecommendations()`. The UI should reuse that path instead of introducing a new endpoint.

Recommendation cards will keep stable cover frames, but the first cards will use eager loading and high fetch priority. The page will also prewarm proxy image URLs with `Image()` for the next several cards after initial render and after append. This is intentionally best-effort and must not block rendering.

Bottom loading will use `IntersectionObserver` on the existing load-more row. When the row approaches the viewport, the handler calls the same `handleAppend()` used by the button. The observer should be recreated after full renders and guarded by the existing `loading` flag so fast scrolls do not issue duplicate requests.

## Data Flow

1. Backend returns recommendation items with `cover_url`.
2. `view-models.js` normalizes and converts cover URLs to `/api/image-proxy` URLs.
3. `recommend.js` renders cards with eager attributes for the first few items and lazy attributes after that.
4. `recommend.js` prewarms the next several cover URLs via `Image()`.
5. `IntersectionObserver` notices the load-more row entering a large bottom margin and calls `handleAppend()`.
6. Appended items are normalized, inserted before the load-more row, and have their cover URLs prewarmed.

## Error Handling

Image prewarming is best-effort. Decode/load errors are ignored because individual card `onerror` handlers already show fixed fallback frames.

Auto append is also best-effort. Network errors keep the button enabled so the user can tap manually.

## Testing

Regression tests should cover:

- cover prewarm URL selection deduplicates and skips invalid covers
- first recommendation cards render eager/high-priority image attributes
- `recommend.js` wires `IntersectionObserver` to the load-more row and calls the shared append path
