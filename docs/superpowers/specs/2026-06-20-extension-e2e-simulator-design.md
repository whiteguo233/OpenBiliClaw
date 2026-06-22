# Extension E2E Simulator Design

## Goal

Verify cross-platform behavior capture from inside the user's installed browser extension, without relying on CDP or external browser automation. The simulator must use the same logged-in browser profile, click real platform DOM controls, and prove success by checking new rows written through the normal `BEHAVIOR_EVENT -> service worker -> /api/events -> events` path.

The first target platforms are Douyin, Xiaohongshu, and X/Twitter.

## Current Behavior

- The backend can push runtime events to the extension over `/api/runtime-stream`.
- The service worker already reacts to runtime events such as `extension_reload` and source task kicks.
- Platform content scripts already capture real DOM events and send `BEHAVIOR_EVENT` messages to the service worker.
- Existing source task dispatchers can open tabs and send messages to content scripts, but they are discovery/bootstrap oriented rather than behavior-capture verification oriented.
- Without CDP, the agent cannot directly click the user's logged-in Chrome tabs from outside the browser.

## Target Behavior

- A local backend command starts an explicit E2E run for selected platforms and actions.
- The backend records an event id watermark before the run.
- The backend pushes a runtime event to the extension with a signed, short-lived E2E plan id.
- The service worker opens or reuses tabs for the requested platforms and sends a whitelisted action plan to each content script.
- Each content script executes real DOM actions on the live page, such as scroll, card click, like, favorite, share, repost/bookmark, and follow.
- State-changing actions are allowed only when the request includes `allow_state_changing=true`.
- Normal content-script observers capture the resulting user-like DOM events and report through existing `/api/events`; the simulator does not directly insert events into the database.
- The backend polls the events table after the run and returns a report comparing expected event types with actual new events.

## Non-Goals

- Do not execute arbitrary JavaScript from backend payloads.
- Do not bypass platform UI, call platform private APIs for the action itself, or mutate site state through fetch shortcuts.
- Do not make the simulator run automatically on startup or scheduler ticks.
- Do not require browser debugging ports, CDP, Playwright, Puppeteer, or AppleScript for the core verification path.
- Do not use this harness as a production discovery or recommendation feature.

## Components

### Backend API

Add a local-only endpoint:

```text
POST /api/extension/e2e/run
```

Request fields:

- `platforms`: subset of `douyin`, `xiaohongshu`, `twitter`
- `actions`: optional action names; if omitted, use the default platform smoke plan
- `allow_state_changing`: required `true` for `like`, `favorite`, `follow`, `repost`, `bookmark`, or equivalent actions
- `timeout_seconds`: bounded run budget

Response fields:

- `run_id`
- `started_at`, `finished_at`
- `platform_reports`
- `events_before_id`, `events_after_id`
- `matched_events`
- `missing_expectations`
- `errors`

The endpoint must reject non-loopback callers and must not run when the extension presence is offline.

### Runtime Event

Push one runtime-stream event:

```json
{
  "type": "extension_e2e_run",
  "run_id": "...",
  "token": "...",
  "platforms": ["douyin", "xiaohongshu", "twitter"],
  "actions": {
    "douyin": ["snapshot", "scroll", "click", "like", "favorite", "follow"],
    "xiaohongshu": ["snapshot", "scroll", "click", "like", "favorite", "follow"],
    "twitter": ["snapshot", "scroll", "click", "like", "favorite", "share", "follow"]
  },
  "allow_state_changing": true
}
```

The token is short-lived and checked by the backend result endpoint. It prevents stale service-worker retries from being accepted as current runs.

### Service Worker Runner

Add a small runner separate from existing source task dispatchers:

```text
extension/src/background/e2e-runner.ts
```

Responsibilities:

- Listen for `extension_e2e_run` from `handleRuntimeEvent`.
- Serialize runs with an in-memory mutex so one E2E run is active at a time.
- Open or reuse the relevant platform tab.
- Wait for the tab to finish loading.
- Send `OBC_E2E_EXECUTE` to the content script.
- Forward each platform result to the backend through:

```text
POST /api/extension/e2e/result
```

The runner must keep existing discovery task dispatchers independent.

### Content Script Executor

Add a platform-neutral action executor that is imported by platform content scripts:

```text
extension/src/content/e2e-executor.ts
```

Responsibilities:

- Accept only fixed action names and platform-specific selector recipes.
- Wait for candidate DOM controls with bounded retries.
- Click the real visible element through `HTMLElement.click()` or dispatch mouse events when needed.
- Scroll with real `window.scrollBy`.
- Return structured evidence: matched selector, visible text, URL before/after, and action status.
- Never call `chrome.runtime.sendMessage({ action: "BEHAVIOR_EVENT" })` directly for the expected event. Existing observers must produce the event naturally.

## Platform Action Plan

### Douyin

Default URL:

```text
https://www.douyin.com/
```

Default actions:

- `snapshot`: page load should produce snapshot.
- `scroll`: scroll feed or selected surface.
- `click`: click a visible video/card/title.
- `like`: click the visible like control on a video page or feed item.
- `favorite`: click collect/favorite if visible.
- `follow`: click follow on an author profile or visible author action.

Expected events include `snapshot`, `scroll` or `click`, and the requested strong signal when the UI exposes that control.

### Xiaohongshu

Default URL:

```text
https://www.xiaohongshu.com/explore
```

Default actions:

- `snapshot`: page load should produce snapshot.
- `scroll`: scroll explore feed.
- `click`: click a visible note card.
- `like`: click note like control.
- `favorite`: click collect control.
- `follow`: click author follow control when visible.
- `share`: open share panel if visible.

Expected events include `snapshot`, `click`, and any requested strong signal supported by the current visible note UI.

### X/Twitter

Default URL:

```text
https://x.com/home
```

Default actions:

- `snapshot`: page load should produce snapshot.
- `scroll`: scroll timeline.
- `click`: click/open a visible tweet.
- `like`: click Like.
- `favorite`: click Bookmark.
- `share`: click Share or Repost, depending on requested action.
- `follow`: click Follow when an account card/profile exposes it.

Expected events include `snapshot`, `click`, and the requested strong signal. For GraphQL-backed actions, the MAIN-world tap may produce the stronger event; for DOM share controls, the generic collector should produce `share`.

## Validation

The backend validates by querying only events created after `events_before_id`:

- Match by `metadata.source_platform`.
- Match by `event_type`.
- Prefer matching run-time window and URL host.
- Report extra events but do not fail the run for them.
- Fail a platform action only when no acceptable event appears before timeout.

The report must distinguish:

- `captured`: expected event found
- `action_unavailable`: UI control not visible or login wall blocks it
- `action_failed`: click/scroll operation failed
- `event_missing`: action ran but no expected event reached backend
- `backend_unavailable`: `/api/events` or validation query failed

## Safety

- State-changing actions are allowed because the user explicitly approved them, but they still require `allow_state_changing=true` in the request.
- No action plan is accepted from arbitrary web pages; only backend runtime-stream can trigger a run.
- No remote code execution is allowed. Payloads contain action names, not scripts.
- The service worker rejects unknown platforms, unknown actions, and oversized plans.
- Each run emits audit logs with run id, platform, action, URL, and result.
- First version cancellation is manual: close the browser tab or stop the backend. A dedicated cancel endpoint is outside this design.

## Testing

Unit tests:

- Backend request validation rejects remote callers, unknown platforms, and state-changing actions without `allow_state_changing=true`.
- Runtime event builder includes a short-lived run token and platform action plan.
- Service worker runner serializes concurrent runs and posts per-platform results.
- Content executor refuses unknown actions and never accepts raw JS.
- Platform selector recipes map expected controls for Douyin, Xiaohongshu, and X.

Integration tests:

- Use a fake content script response to validate backend event matching and report generation.
- Use extension unit tests to simulate `extension_e2e_run` runtime event and verify tab/message/result flow.

Manual real E2E:

- Start `openbiliclaw start`.
- Load unpacked extension in a browser logged into Douyin, Xiaohongshu, and X.
- Call `POST /api/extension/e2e/run` with all three platforms and `allow_state_changing=true`.
- Confirm the report contains captured events for all actions that were visible and executable.

## Documentation

- Update `docs/modules/extension.md` with the E2E simulator API, safety model, and supported platforms/actions.
- Update `docs/changelog.md` in the current version block.
- If the endpoint becomes a CLI command, update `docs/modules/cli.md`.
