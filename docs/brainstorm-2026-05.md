# Follow-up brainstorm: improvements identified during the Safari port + Watch-Later + 标记为搬运 work

This is a scratch list of things noticed while doing the work in this branch.
None of them are blockers; ordered roughly by how much pain they cause vs. how
much effort to fix.

## 1. The launcher's "👍 感兴趣" button is a no-op stub

`extension/popup/popup-launcher.js:342-348` — the `.like-btn` handler just
changes its own text to "✅ 已标记" and disables itself. No `submitFeedback`
call, nothing reaches the backend. Users who only use the toolbar popup (the
launcher) and never open the full panel will think they're giving feedback that
isn't being recorded.

**Two reasonable fixes**:
- (a) Wire it to `submitFeedback` (need to import or duplicate the helper —
  the launcher is intentionally lightweight, so duplicating the minimal POST
  is fine)
- (b) Remove the button entirely; the launcher is meant to be a quick view, not
  a feedback surface

I'd lean (a) since the button visibly exists and silently broken is worse than
absent.

## 2. content_id stays as bvid after manual 搬运 mark

`mark_content_as_youtube_repost` updates `content_url` and `source_platform`
but leaves `content_id` as the bvid. The recommendation cache still keys on
bvid, so this isn't broken per se, but it's inconsistent — a row with
`source_platform='youtube'` and `content_id='BV1xxx'` looks wrong to a future
reader.

**Fix**: either parse the YT video ID from the URL and store that as
content_id, or rename the column to make it explicit that content_id is the
discovery-time identifier and may not match source_platform.

## 3. The popup-launcher's tabs lose state on close

The launcher's About/推荐/我的画像/聊聊想法 tabs reset to default when the
popup closes. The Watch Later count fetches fresh every time. For the
chat tab, in-flight messages are lost.

**Fix**: store `state.activeTab` in `chrome.storage.session` (Chrome) or
`localStorage` (Safari). Persist scroll position too. Lightweight; <30 LOC.

## 4. The mark-as-repost button has no "undo"

Once a user clicks 🔁 标记为搬运 and YT search succeeds, the override is
written to content_cache. There's no way to undo it from the UI — they'd
have to find the bvid in the SQLite db and `UPDATE content_cache SET ...`.

**Fix**: add a `POST /api/yt-replacer/clear-mark` endpoint that reverts the
content_cache row to its original Bilibili URL + cover. The original URL can
be reconstructed from the bvid (`https://www.bilibili.com/video/{bvid}`).
Cover is trickier — we'd need to fetch it from Bilibili again, or
preemptively cache the pre-override cover in a new column.

Realistically, "undo" is rare; the bigger gap is just letting the user know
that marks are sticky. A line of UI copy near the button explaining this
would do.

## 5. backend-endpoint.ts hardcodes http://

`extension/src/shared/backend-endpoint.ts` builds the backend URL as
`http://${host}:${port}/api`. Users running the backend behind a tunnel
(natapp, ngrok, cloudflared) that terminates TLS get HTTPS URLs and can't use
them. The hard-coded scheme also conflicts with Safari's App Transport Security
when the host isn't 127.0.0.1.

**Fix**: parse the host string and let `https://...:port` syntax through.
Bigger: add a scheme dropdown to the popup config screen (currently only port
is configurable in-UI; host is set via DevTools console).

## 6. Popup config UI doesn't expose host

`extension/popup/popup-backend-config.js` reads/writes host + port, but
`popup.html` only has a port input. Users with the backend on a non-localhost
host (LAN box, Tailscale IP) have to set it via DevTools or by editing the
extension's storage.

**Fix**: add a host input next to the port input. Validation already exists
(`isValidBackendHost`). Small frontend change, ~10 LOC.

## 7. Distribute signed Safari builds (on top of the new CI)

The new `safari-macos.yml` produces an unsigned `.app.zip`. Users have to
right-click → Open → "Open Anyway" each time they install. For real
distribution:

- Add Apple Developer ID cert + provisioning profile as repo secrets
  (`APPLE_CERT_P12`, `APPLE_CERT_PASSWORD`, `APPLE_API_KEY_ID`, etc.)
- Use `apple-actions/import-codesign-certs@v3` to import the cert into the
  runner's keychain
- Replace `CODE_SIGN_IDENTITY="-"` with `"Developer ID Application: ..."`
- Add `xcrun notarytool submit ... --wait` after building
- Add `xcrun stapler staple` to embed the notarization ticket

Needs a paid Apple Developer account ($99/year). The CI changes are ~50 LOC.

## 8. iOS Safari target

The current `safari/project.yml` targets macOS only. Building for iOS Safari
would let the extension run on iPhone/iPad. Requires:

- A second app target with `platform: iOS` in project.yml
- iOS-specific entitlements file
- A SwiftUI or UIKit shell view (the macOS storyboard won't transfer)
- Testing on a real device or simulator (Xcode Cloud could do this in CI)

Probably one weekend of work. Worth it if there's user demand — the iOS Safari
extension story is much better than it was a year ago.

## 9. Unit tests for the new code paths

What I added in this session that has no test coverage:

- `_normalize_shares` with share=0 mid-list, end-list, all-zero (the standalone
  test I ran by hand should be in `tests/test_source_policy.py`)
- `_source_target_counts` with share=0 sources (similar; belongs in
  `tests/test_refresh_runtime.py`)
- `replace_if_foreign(..., skip_detection=True)` (new flag, no test)
- `Database.mark_content_as_youtube_repost` (DB method, no test)
- `POST /api/yt-replacer/mark-as-repost` endpoint (no test)
- The popup-launcher height/scrolling change is CSS-only so probably out of
  scope for tests

Each is small. The DB method test would need a temp sqlite fixture; the API
test would need a FastAPI TestClient setup, which the codebase already has.

## 10. The mark-as-repost expression suffix is verbose

The endpoint appends `\n💡 用户标记为搬运，原视频在 YouTube：{yt_url}` to
the recommendation's expression. That's pretty long and includes the raw URL.
The card already shows a "YouTube" platform badge after the mark, so the
suffix is redundant.

**Fix**: drop the suffix entirely, or make it short like `（已标记搬运）`.
Easy change in `app.py`.

## 11. Pre-existing test failures in xhs-task-dispatcher

Six tests in `extension/tests/xhs-task-dispatcher.test.ts` (291-296) fail on
the unmodified base — not caused by my work but worth flagging:

- 291: bootstrap_profile foreground tab
- 292: search task background tab
- 293: explore active waits after profile navigation
- 294: sendMessage_failed when content script absent
- 295: bootstrap follows discovered profile URL
- 296: bootstrap partial results posted without closing

They look like they're testing tab-handling in `executeTask`. Worth a separate
investigation pass since they've been red for a while.

## 12. The like-btn stub fix could come with cleaning up duplicated state

Both `popup-launcher.js` (`state` object at line 27) and `popup.js` (`state`
object at line 70-ish) maintain their own local state. The launcher's state
is sparse (just the recommendations list and the active tab) but it duplicates
shape with the full popup. If you ever want the launcher and full popup to
share preferences or in-flight chat turns, this is the place to refactor.

Not urgent — they're independent UIs today.
