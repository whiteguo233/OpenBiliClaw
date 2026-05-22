# Mobile Web Native Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `/m/` into a first-class mobile web surface for recommendations, feedback, delight/probe handling, profile reading, and contextual chat.

**Architecture:** Keep Vanilla JS + ES modules, but move mobile UI data normalization into `view-models.js` and shared mutable UI state into a dedicated mobile state module. `app.js` owns shell rendering, routing, health/stream wiring, and cross-view navigation; individual view modules render their own tab content from the shared state.

**Tech Stack:** Python/FastAPI backend APIs already present, SQLite-backed recommendation/chat data, Vanilla JS ES modules, CSS, pytest-driven Node checks for mobile view-model helpers, Playwright/manual mobile viewport smoke tests.

---

Implements: `docs/plans/2026-05-21-mobile-web-native-redesign-design.md`

## Scope Summary

Rewrite `/m/` from a stripped-down MVP into a mobile-native experience that covers the same core workflows as the browser extension side panel. No new backend business endpoints required — all APIs already exist.

## Backend Ground Truth

These API facts are fixed inputs for implementation:

- Recommendation feedback uses `POST /api/feedback`, not `/api/recommendation-feedback`.
- Recommendation feedback payload must use `recommendation_id: item.id`; `/api/recommendations` returns `id` as the `recommendations` table primary key.
- `/api/delight/respond` accepts API response tokens only: `"view"`, `"like"`, `"dislike"`, `"chat"`.
- Delight UI states may still be `"viewed"`, `"rejected"`, `"chatting"`, `"chatted"`; do not send those UI-state strings to `/api/delight/respond`.
- Delight contextual chat should use durable `POST /api/chat/turns` with `session: "mobile"`, `scope: "delight"`, `subject_id: bvid`, `subject_title: title`; do not also call `/api/delight/respond` with `"chat"` unless intentionally using the older synchronous endpoint.
- Probe contextual chat should use durable `POST /api/chat/turns` with `session: "mobile"`, `scope: "probe"`, `subject_id: domain`.
- `/api/health` reports degraded mode as `status: "degraded"` plus `reason`; it does not currently return `degraded: true`.
- `/api/activity-feed` pagination uses `before=<created_at>` and returns `next_cursor`; same-timestamp boundary items can be skipped because the backend uses strict older-than filtering.

## Files Touched

### New
- `src/openbiliclaw/web/js/state.js` — shared mobile UI state, `patchState()`, subscriptions, and cross-view context fields

### Modified
- `src/openbiliclaw/web/js/view-models.js` — grow from the current small helper set into the full mobile normalization layer
- `tests/test_mobile_web_view_models.py` — extend the existing pytest Node wrapper with direct mobile helper coverage
- `src/openbiliclaw/web/css/app.css` — significant additions (activity strip, feedback sheet, delight tray, placeholder carousel, bottom sheet, degraded banner, `prefers-reduced-motion`)
- `src/openbiliclaw/web/js/app.js` — shell render, status text next to dot, degraded detection, stream event routing, `navigateToTab()`
- `src/openbiliclaw/web/js/api.js` — add `fetchHealth()`, `submitFeedback()`, `markDelightSent()`, `refreshRecommendations()`; keep existing `fetchActivityFeed()`
- `src/openbiliclaw/web/js/views/recommend.js` — major rewrite (activity strip, pool semantic summary, delight tray with actions, card feedback, feedback bottom sheet)
- `src/openbiliclaw/web/js/views/profile.js` — moderate rewrite (full normalizeProfileSummary, empty/uninit state, favorite UP, style/context sections, cognition card expand)
- `src/openbiliclaw/web/js/views/chat.js` — moderate rewrite; Phase 1 adds the contextual-chat entry contract, Phase 3/5 polish messages/chat UI
- `src/openbiliclaw/web/index.html` — no changes expected (shell is correct)
- `src/openbiliclaw/web/manifest.json` — no changes expected

### Not Modified
- `src/openbiliclaw/api/app.py` — already has StaticFiles mount + degraded guard passthrough; all needed API endpoints exist
- `src/openbiliclaw/web/js/stream.js` — no changes expected
- `extension/` — no changes; mobile ports logic into its own `view-models.js`

---

## Phase 1: Foundation — State Model + View-Models

**Goal**: Centralized state, cross-view contracts, full normalization layer, tests.

### 1.1 Expand `view-models.js`

Port these from `extension/popup/popup-helpers.js` into mobile `view-models.js`:

| Function | Source in popup-helpers.js | Purpose |
|----------|--------------------------|---------|
| `buildVideoUrl(bvid)` | L25-27 | Build fallback Bilibili URL |
| `buildContentUrl(item)` | L29-33 | Build multi-source content URL without duplicating per-view URL logic |
| `normalizeRecommendation(item)` | L64-78 | Normalize rec fields, defaults for title/up_name |
| `normalizeDelightCandidate(item)` | L80-93 | Normalize delight fields, score, state |
| `getDelightUiState(delight, opts)` | L117-180 | Derive visible/handled/score_label/response_tone |
| `buildFeedbackPayload(id, type, note)` | L182-188 | Build feedback POST body |
| `normalizeProfileSummary(summary)` | L326-405 | Full onion model normalization |
| `normalizeCognitionUpdateCard(item)` | L190-230 | Cognition card with expand hint |
| `buildNextCognitionHistoryState(cur, next)` | L433-448 | Merge paginated cognition |
| `normalizeActivityFeed(payload)` | L768-790 | Activity items + live_summary |
| `getActivityCardState({feed, event, expanded})` | L792-804 | Activity strip state |
| `getPoolStatusSummary(status)` | L536-581 | **Semantic** pool summary (not just numbers) |
| `normalizeRuntimeStatus(status)` | L496-514 | Runtime fields normalization |
| `mergeRuntimeStatusEvent(status, event)` | L516-534 | Patch runtime from stream event |
| `getReadyRecommendationHint(status)` | L613-631 | Hint when pool exhausted |
| `formatRelativeTimestamp(iso, now)` | L242-273 | Relative time labels |
| `validateCommentInput(note)` | L633-644 | Comment validation |
| `getCommentSubmitUiState(state)` | L646-674 | Comment button/status |

Already in view-models.js and kept as-is:
- `normalizeCoverUrl`, `getCoverImageAttrs`, `normalizePoolStatus` (simple), `normalizeMbtiDimensions`, `normalizeChatTurn`

Note: `normalizePoolStatus` (simple 3-field version) stays for backward compat but `getPoolStatusSummary` (semantic copy) becomes the primary pool renderer.

Add mobile-only helpers where needed:

| Function | Purpose |
|----------|---------|
| `normalizeSourcePlatform(item)` | Replace duplicated source inference in views |
| `getSourceLabel(source)` | Centralize source badge copy |
| `getDelightActionState(action)` | Map UI actions to API token + UI state without leaking `viewed/rejected/chatted` into the API |

`getDelightActionState(action)` must follow this contract:

```js
getDelightActionState("view")
// { apiResponse: "view", uiState: "viewed", permanent: true }

getDelightActionState("reject")
// { apiResponse: "dislike", uiState: "rejected", permanent: true }

getDelightActionState("chat")
// { apiResponse: null, uiState: "chatting", permanent: false }

getDelightActionState("later")
// { apiResponse: null, uiState: "pending", permanent: false }
```

### 1.2 Centralized State in `state.js`

Create `src/openbiliclaw/web/js/state.js` with the spec's state model:

```js
export const state = {
  activeTab: "recommend",
  online: false,
  degraded: false,
  degradedReason: "",
  runtimeStatus: null,    // normalizeRuntimeStatus()
  runtimeEvent: null,     // last stream event
  activityFeed: null,     // normalizeActivityFeed()
  activityExpanded: false,
  recommendations: [],    // normalizeRecommendation()
  activeDelights: [],     // normalizeDelightCandidate()
  delightCurrentIndex: 0,
  messages: { notifications: [], delights: [] },
  profile: null,          // normalizeProfileSummary()
  chatTurns: [],
  pendingChatPolls: new Set(),
  pendingChatContext: null,
};
```

Export `state`, `patchState(partial)`, and `subscribe(listener)` so views read from `state` and dispatch updates centrally.

Important state rules:

- `patchState()` shallow-merges object fields, but any `Set`/array updates must replace the collection instead of mutating it in place.
- `app.js` subscribes to state changes and always re-renders shell-owned UI when shell fields change: connection text, degraded banner, badge count, tab bar.
- Only the active tab content should re-render on normal view data changes.
- Stream events still update inactive view data in state so tabs are fresh when opened.

### 1.3 App Shell + Cross-View Contracts

Update `app.js` to consume `state.js` and expose:

```js
export function navigateToTab(id) {
  // validate id, update hash/state, render tab bar, initialize target view
}
```

Add the minimal contextual chat contract in `chat.js` during Phase 1, even though chat UI polish waits until Phase 5:

```js
export async function startContextualChat({ scope, subjectId, subjectTitle, message }) {
  // scope is "delight" or "probe"
  // session must be "mobile"
  // creates an optimistic pending turn, calls startChatTurn(), then polls by turn_id
}
```

Phase 2 and Phase 3 may call this function. They must not wait for Phase 5 to define it.

### 1.4 API Client Contract

Update `api.js`:

- Add `fetchHealth()` returning the parsed `/api/health` payload.
- Keep `checkHealth()` as a boolean wrapper if existing callers still need it.
- Add `submitFeedback(payload)` for `POST /api/feedback`.
- Add `markDelightSent(bvid)` for `POST /api/delight/sent`.
- Add `refreshRecommendations()` for `POST /api/recommendations/refresh`.
- Keep `startChatTurn()` default session as `"mobile"`.

### 1.5 Tests

Extend existing `tests/test_mobile_web_view_models.py`. Since `view-models.js` is pure JS, keep the current pytest wrapper that runs Node with `--input-type=module`; do not rely on nonexistent `tests/test_popup_helpers.py`.

Required coverage:

- export presence for every Phase 1 helper
- `normalizeRecommendation` defaults and `id` preservation
- `buildFeedbackPayload` uses numeric recommendation id and trims note
- `getDelightActionState` maps UI action strings to backend-safe API tokens
- `normalizeDelightCandidate` + `getDelightUiState`
- `getPoolStatusSummary`
- `normalizeActivityFeed` + `getActivityCardState`
- `normalizeProfileSummary`
- `normalizeChatTurn`
- `formatRelativeTimestamp`

---

## Phase 2: Recommend Tab Rewrite

**Goal**: Activity strip, semantic pool, delight tray with full actions, card feedback.

### 2.1 Activity Strip

Top of recommend view. Collapsed = one-line summary. Expandable = paginated history.

- Add `fetchActivityFeed` call in `loadData()`
- Render using `getActivityCardState()` from view-models
- Expand/collapse toggle
- "加载更多" loads next page via `fetchActivityFeed({ before: cursor })`
- Stream events `refresh.started`, `refresh.strategy`, `activity.added` update `runtimeEvent` in state → re-render strip

### 2.2 Pool Semantic Summary

Replace current numeric-only pool-status grid with semantic copy from `getPoolStatusSummary()`:

- 3 chips now show text strings: "还有 34 条可换" / "刚补进 6 条" / topic/status message
- During refresh: "后台继续在找更多" messaging

### 2.3 Delight Tray

Upgrade from simple banner to full tray:

- Show `delight_hook`, `delight_reason`, score label from `getDelightUiState()`
- Cover image (if available, using `getCoverImageAttrs`)
- Source badge
- Actions: 看看 / 不感兴趣 / 聊一聊 / 稍后
  - "看看" → `respondToDelight(bvid, "view", title)` + open URL + local UI state `"viewed"` + permanent dismissal
  - "不感兴趣" → `respondToDelight(bvid, "dislike", title)` + local UI state `"rejected"` + permanent dismissal
  - "聊一聊" → `startContextualChat({ scope: "delight", subjectId: bvid, subjectTitle: title, message })`; switch to chat tab; do **not** call `/api/delight/respond` with `"chatted"`
  - "稍后" → advance to next delight in queue without responding or acking
- After respond: show brief result state before removing/advancing
- For permanent dismissal, also call `markDelightSent(bvid)` best-effort so `/api/delight/pending-batch` does not rehydrate the same dismissed item on reload.

### 2.4 Recommendation Card Actions

Add action row below each card's expression:

- 打开 (primary) — opens URL, reports click
- 👍 喜欢 — `submitFeedback(buildFeedbackPayload(item.id, "like"))`
- 👎 不喜欢 — `submitFeedback(buildFeedbackPayload(item.id, "dislike"))`
- 💬 写一句 — opens feedback bottom sheet

Feedback bottom sheet (new CSS component):
- Slides up from bottom
- Text input + "发出去" button
- Uses `validateCommentInput` + `getCommentSubmitUiState`
- `submitFeedback(buildFeedbackPayload(item.id, "comment", note))`
- Close after success

**Key**: Card whole-area click must NOT trigger when tapping action buttons. Use `e.stopPropagation()` on the action row.

**Key**: Never use `bvid` as `recommendation_id`. Feedback must use `item.id`, which is the recommendations table primary key returned by `/api/recommendations`.

### 2.5 Stream Events

Handle additional events in recommend view:
- `refresh.started` → update activity strip "正在处理"
- `refresh.strategy` → update activity strip with strategy message
- `refresh.pool_updated` → reload recs + update pool summary + activity strip
- `activity.added` → prepend to activity feed

### 2.6 CSS Additions

- `.activity-strip`, `.activity-strip.expanded`, `.activity-item`
- `.delight-tray`, `.delight-actions`, `.delight-result-state`
- `.card-actions`, `.card-action-btn`
- `.feedback-sheet` (bottom sheet overlay)
- `.feedback-input`, `.feedback-submit`

---

## Phase 3: Messages Overlay Polish

**Goal**: Full probe + delight card handling, badge count, ack behavior.

### 3.1 Probe Cards

- Show domain, description, reason
- Actions: 感兴趣 / 不感兴趣 / 多聊聊
  - "感兴趣" → `respondToProbe(domain, "confirm")`
  - "不感兴趣" → `respondToProbe(domain, "reject")`
  - "多聊聊" → `startContextualChat({ scope: "probe", subjectId: domain, subjectTitle: domain, message })`
- After respond → brief result message, then remove card

### 3.2 Delight Cards

- Show title, hook, cover (if available), source
- Actions: 查看 / 不感兴趣 / 聊一聊
- Same behavior as delight tray actions
  - "查看" uses API token `"view"` and UI state `"viewed"`
  - "不感兴趣" uses API token `"dislike"` and UI state `"rejected"`
  - "聊一聊" uses durable `startChatTurn()` through `startContextualChat()`
- After respond → remove card

### 3.3 Badge & Ack

- Badge count = `notifications.length + delightMsgs.length`
- On overlay open: `loadNotifications()` fetches fresh data
- `POST /api/notifications/sent` ack on view
- `POST /api/delight/sent` ack on permanent delight dismissal/view/reject/chat completion
- Do not ack a delight on "稍后"; it should remain eligible in the queue

### 3.4 CSS Additions

- Polish `.messages-overlay` slide-up animation
- `.message-card` action button spacing for 3 buttons
- Result state after respond (brief green/blue confirmation)

---

## Phase 4: Profile Tab Rebuild

**Goal**: Full onion model, empty/uninit states, all layers.

### 4.1 Use `normalizeProfileSummary()`

Replace current ad-hoc field reads with `normalizeProfileSummary()` output. This gives:
- Proper defaults (e.g. portrait placeholder when uninit)
- Normalized interests with specifics
- Normalized MBTI (already handled via `normalizeMbtiDimensions`)
- Speculative interests with confirmation_count / threshold / specifics

### 4.2 Empty / Uninit State

- `!profile.initialized` → show clear next-step message: "还没完成初始化，先运行 openbiliclaw init"
- No profile data at all → spinner → then empty state

### 4.3 Add Missing Sections

From the spec's information architecture:

| Section | Current | Target |
|---------|---------|--------|
| Portrait | ✅ | ✅ (with uninit fallback) |
| Core traits | ✅ | ✅ |
| Deep needs | ✅ (as core_needs) | ✅ (rename label) |
| MBTI | ✅ | ✅ |
| Values | ✅ | ✅ |
| Motivational drivers | ❌ | ✅ new |
| Interest likes/dislikes | ✅ | ✅ (add specifics sub-items) |
| Favorite UP | ❌ | ✅ new |
| Life stage / Current phase | ✅ | ✅ |
| Cognitive style | ✅ (single string) | ✅ (array of chips) |
| Style preferences | ✅ (simple label:value) | ✅ (use normalized style object with named prefs) |
| Context | ❌ | ✅ new (weekday/weekend/time patterns) |
| Exploration openness | ✅ | ✅ |
| Speculative interests | ✅ | ✅ (add specifics, confirmation progress bar) |
| Cognition history | ✅ | ✅ (add expandable cards with impact/reasoning/evidence) |
| Active insights | ✅ | ✅ (add evidence list, confidence, validated badge) |
| Recent awareness | ✅ | ✅ (add emotion_guess, trend) |

### 4.4 Cognition History Expandable Cards

Use `normalizeCognitionUpdateCard()` → each card can have:
- Summary (always visible)
- Context line
- Impact, reasoning, evidence (expandable)
- Created_at with `formatRelativeTimestamp()`

### 4.5 CSS Additions

- `.profile-uninit` empty state
- `.cognition-card.expandable` toggle
- `.spec-interest-progress` (confirmation count bar)
- `.context-patterns` (weekday/weekend grid)
- `.insight-evidence`, `.insight-confidence`
- `.awareness-emotion`, `.awareness-trend`

---

## Phase 5: Chat Tab Polish

**Goal**: Placeholder carousel, contextual chat, per-turn states.

### 5.1 Placeholder Carousel

Rotate input placeholder text on interval (matching extension behavior):

```js
const PLACEHOLDERS = [
  "最近有什么想聊的？",
  "对哪条推荐有想法？",
  "想探索什么新领域？",
  "觉得画像准不准？",
  "有什么不想再看到的？",
];
```

Rotate every 4s. Stop when input is focused.

### 5.2 Contextual Chat Entry

The callable contract is created in Phase 1 because Phase 2 and Phase 3 depend on it.

Phase 5 only polishes the UX around that contract:

- If invoked without an explicit user message, pre-fill composer text like `关于「{title}」，我想聊聊` and focus the composer.
- If invoked with a message, immediately create a pending turn and poll it.
- Keep `session: "mobile"` for every contextual turn.
- Preserve `scope`, `subject_id`, and `subject_title` in the visible pending/error turn so retries keep context.

### 5.3 Per-Turn Status

Each turn in the list should show:
- User message bubble (right)
- AI response bubble (left) — or pending/error indicator
- `pending` → "思考中…" with spinner
- `processing` → "思考中…" with spinner
- `error` / `failed` → red error message with retry button

### 5.4 Scroll Stability

- Auto-scroll to bottom on new message
- If user scrolled up manually, don't auto-scroll (check `scrollTop + clientHeight < scrollHeight - threshold`)

### 5.5 CSS Additions

- `.chat-placeholder-carousel` transition
- `.chat-bubble.error` retry button
- Composer bottom padding for keyboard avoidance

---

## Phase 6: Polish & Testing

### 6.1 Status Bar Enhancements

- Status text next to dot: "在线" / "离线" / "降级模式"
- Degraded state: call `fetchHealth()` and treat `status === "degraded"` as degraded; show `reason` in a compact banner below status bar
- `prefers-reduced-motion`: disable all transitions

### 6.2 Image Error Handling

Already partially in place (`onerror="this.remove()"`). Ensure:
- `referrerpolicy="no-referrer"` on all external images (via `getCoverImageAttrs`)
- XHS CDN filtered (via `normalizeCoverUrl`)
- Protocol-relative and http → https (via `normalizeCoverUrl`)

### 6.3 Console Cleanliness

- Remove console.error calls in catch blocks that are expected (health check offline, etc.)
- Replace with silent failure or user-visible state

### 6.4 Viewport Testing

Manual checks:
- 390x844 (iPhone 14)
- 430x932 (iPhone 15 Pro Max)
- 360x780 (Android mid-range)

Verify:
- No text/button overlap
- Tab bar fully visible
- Cards don't overflow
- Feedback sheet doesn't clip

### 6.5 View-Model JS Tests

Use the existing pytest wrapper:

```
pytest tests/test_mobile_web_view_models.py -q
```

Coverage:
- `normalizeRecommendation` — missing fields get defaults
- `normalizeCoverUrl` — http→https, XHS blocked, protocol-relative
- `getPoolStatusSummary` — running/idle/sufficient states
- `normalizeActivityFeed` — empty/populated
- `getDelightUiState` — pending/viewed/rejected/chatted
- `getDelightActionState` — view/reject/chat/later maps to backend-safe tokens and UI states
- `buildFeedbackPayload` — types and note
- `normalizeProfileSummary` — all layers, missing fields
- `normalizeChatTurn` — reply field fallback
- `formatRelativeTimestamp` — ranges (刚刚, 分钟, 小时, 天, 日期)

---

## Execution Order

```
Phase 1 → Phase 2 → Phase 3 → Phase 5 → Phase 4 → Phase 6
  │         │          │
  │         │          └── chat.js polish after messages overlay changes
  │         └── recommend.js rewrite can proceed once contextual chat contract exists
  └── unblocks state, API wrappers, action-token mapping, and contextual chat contract
```

Phase 1 (view-models + state + API wrappers + contextual chat contract) is the critical path — every other phase depends on it.

Parallelism guidance:

- Phase 2 (`recommend.js`) and Phase 4 (`profile.js`) can run independently after Phase 1.
- Phase 3 and Phase 5 both touch `views/chat.js`; keep them sequential unless split messages overlay into a separate module first.
- Commit sequentially for clean review even if implementation happens in parallel.

## Risk Notes

1. **Feedback API field mapping — resolved**: `POST /api/feedback` takes `recommendation_id` (integer), `feedback_type`, `note`. The recommendation_id comes from the `id` field in recommendation items; it is the DB primary key, not `bvid`. Add a Phase 1 view-model test for `buildFeedbackPayload(item.id, ...)`.

2. **Delight action tokens — critical**: `/api/delight/respond` accepts `"view"`, `"like"`, `"dislike"`, `"chat"`. UI states like `"viewed"`, `"rejected"`, `"chatted"` must never be sent as the `response` value. Use `getDelightActionState()`.

3. **Contextual chat scope — contract moved to Phase 1**: `POST /api/chat/turns` accepts `scope` and `subject_id`. Mobile chat session must be `"mobile"` not `"popup"` to avoid cross-contamination with extension chat.

4. **Activity feed pagination — verified with caveat**: `GET /api/activity-feed` supports `limit` and `before`; `before` is the prior response's `next_cursor` derived from item `created_at`. Because backend filtering is strictly older-than, same-timestamp items at a page boundary may be skipped. Use a comfortable page size for mobile history; fix backend cursor only if this becomes user-visible.

5. **Degraded health payload**: `/api/health` degraded mode is `status: "degraded"` + `reason`, not `degraded: true`. `fetchHealth()` should preserve the payload.
