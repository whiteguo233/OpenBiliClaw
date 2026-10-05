# Six-Platform Native Save Execution Design

> **Current-state correction (2026-08-31):** Zhihu now exposes an item-level global
> `收藏 / 已收藏` toggle. Production uses target `知乎收藏`, does not claim named-collection
> support, and never clicks an initially `已收藏` control or any control during verification.

## Goal

Complete issue #56 for the six non-Bilibili formal sources: YouTube,
Xiaohongshu, Douyin, X/Twitter, Zhihu, and Reddit. A user action in
OpenBiliClaw must continue to save locally first, then write the matching
platform account when automatic sync is enabled or the user triggers manual
sync. Bilibili's existing direct adapter remains unchanged.

## Confirmed Product Contract

- Favorite always writes the platform's closest native favorite/save/bookmark
  capability.
- Watch later writes a native watch-later target when the platform has one;
  otherwise it resolves to the same native favorite target.
- Automatic sync remains globally default-off. Manual sync always bypasses the
  switch after the existing user-facing confirmation.
- Platforms needing a user-created container use exact title `OpenBiliClaw`:
  YouTube favorite uses that playlist and Zhihu favorite uses that collection.
- Xiaohongshu, Douyin, X, and Reddit use their native default favorite,
  bookmark, or Saved target rather than inventing a container the platform does
  not reliably expose.
- Local success never rolls back on platform failure; local removal never
  deletes a platform save.
- Real account writes occur only after the user grants a current, named-test
  authorization or supplies a test account.

## Platform Mapping

| Platform | Favorite target | Watch-later target | Executor mode |
| --- | --- | --- | --- |
| YouTube | `OpenBiliClaw` playlist | native Watch Later | logged-in extension |
| Xiaohongshu | native favorite | native favorite fallback | logged-in extension |
| Douyin | native favorite | native favorite fallback | logged-in extension |
| X/Twitter | native Bookmark | native Bookmark fallback | logged-in extension |
| Zhihu | global `知乎收藏` | same favorite fallback | logged-in extension |
| Reddit | native Saved | native Saved fallback | logged-in extension |

The extension executor first performs a platform-scoped same-origin request
when the platform exposes a stable request contract. It falls back to a
platform-specific visible-control action only when the request path is absent
or rejected. The fallback is not a generic clicker: every executor owns its
content-ID extraction, login detection, already-saved detection, action
confirmation, and structured result mapping.

## Architecture

The existing `SavedSyncService`, `NativeSaveRouter`, native task ledger, and
local-first API remain the source of truth. Six new adapters implement the
existing `NativeSaveAdapter` protocol and declare their real capability and
target labels. They do not receive cookies or browser credentials.

An `ExtensionNativeSaveBroker` bridges an adapter invocation to the installed,
authenticated OpenBiliClaw extension:

1. The adapter receives a local `SavedItemInput` and resolved route.
2. The broker persists an extension job correlated with the native task item,
   including only platform, canonical item identity, requested/resolved action,
   safe content URL, and target label.
3. It kicks the connected extension and waits within the service's existing
   bounded execution deadline.
4. The relevant platform dispatcher claims the job through the existing exact
   source endpoint shape `/api/sources/<slug>/next-task`, executes it in a
   logged-in platform page, and posts a structured result to
   `/api/sources/<slug>/task-result`.
5. The broker translates that result into the existing native-save task state.

The broker is durable across a backend reload. A disconnected extension yields
`extension_required`, never a false `synced`; a stale claimed job returns a
retryable safe failure after its lease expires. Existing source task types and
native-save jobs remain namespaced so an account/bootstrap/discovery task can
never be interpreted as a state-changing save job.

The rollout distinguishes a missing adapter from a genuinely unsupported
content type. Router absence is persisted as `unsupported_adapter_missing`;
an executor's actual content limitation is persisted as
`unsupported_content_type`. A migration/requeue rule converts the prior bare
`unsupported` records created before these adapters existed into the former
code. Once an adapter is registered, those legacy rows become eligible for one
explicit manual or automatic retry; true content-type limitations stay terminal
and local-only.

## Executor Contract

Every platform executor accepts one canonical job and returns exactly one of:

- `synced` when it changed the platform state;
- `already_synced` when the target already contained the item;
- `login_required` when the real platform session is absent or expired;
- `rate_limited` for an explicit platform throttle/risk-control response;
- `unsupported` only for an unsupported content type or target action;
- `failed` with a short safe code for any other action/page/result mismatch.

The job payload must never include raw cookies, CSRF tokens, OAuth tokens,
page HTML, or response bodies. The backend persists a safe result code and
short message only. The extension calls backend endpoints through the existing
authenticated shared API client.

For `OpenBiliClaw` containers, the executor first searches an exact-title
playlist or collection, creates it if absent, then rechecks it before adding
the item. Container creation failure is a per-item `failed`; it must not write
to an unrelated container. Repeating the same save detects an existing target
membership and returns `already_synced`.

## User Surfaces

The existing popup, desktop Web, and mobile Web retain their local-first
optimistic behavior. Once a non-Bilibili adapter is registered, its pending
items are sync-eligible rather than permanently marked local-only. If the
extension is unavailable, saved pages show the existing truthful
`extension_required` state and a connection instruction.

CLI remains configuration/status-only: `config-show` exposes the global
automatic-sync switch and no CLI command silently changes a platform account.

## Error, Concurrency, And Compatibility Rules

- One platform executor serializes writes for a single account; batch work can
  still run across platforms in parallel under the existing native task owner
  fence.
- A native task never retries an uncertain write automatically. It records the
  terminal result; the user may explicitly retry from a saved page.
- Result callbacks must match both job ID and expected canonical item key; a
  late or mismatched callback is ignored and logged safely.
- Existing Bilibili direct writes, old Bilibili API routes, normalized local
  memberships, and unsupported UI behavior for genuinely unsupported content
  types remain backward compatible.
- Legacy non-Bilibili `unsupported` rows produced solely because no adapter was
  registered are reclassified as adapter-missing and become eligible after the
  matching adapter ships; genuine `unsupported_content_type` rows do not.
- The Zhihu typed identities `question:<id>`, `answer:<id>`, and `article:<id>`
  remain accepted at the saved API boundary; other extra-colon identities stay
  fail-closed.

## Testing And Real Verification

Each delivery unit uses RED/GREEN tests before implementation:

- broker persistence, lease, callback correlation, and extension-disconnected
  outcomes;
- six adapter capability/route/target matrices, including watch-later fallback;
- one executor test suite per platform covering login, already-saved, success,
  rate-limit, unsupported content, and safe failed results;
- popup, desktop, and mobile regression tests showing each adapter-backed
  platform becomes sync-eligible and `extension_required` remains truthful;
- full backend Ruff/MyPy/Pytest and extension test/typecheck/build.

Real E2E uses the installed extension browser and real platform login state.
For each platform, validate local-only save while auto-sync is off, manual
favorite, manual watch-later mapping, automatic sync after explicit consent,
and a duplicate save. Record only selected public content IDs, task states,
targets, and safe error codes. Remove local test memberships after verification
without deleting platform records.

## Delivery Order

1. Build the shared durable extension-native-save broker and common task
   protocol without registering new production adapters.
2. Add Reddit and X adapters/executors, which map both user intents to a
   single native target and exercise duplicate handling.
3. Add YouTube's named playlist and native Watch Later executor.
4. Add Xiaohongshu, Douyin, and Zhihu executors, including their distinct
   favorite/collection UI and exact-title container behavior.
5. Register all adapters at runtime, update all graphical surface contracts,
   document mappings, and run authorized real-account E2E for all seven
   platforms.

## Documentation Scope

Implementation updates the module/API/runtime/extension/config/integration
documentation, architecture diagrams, changelog, README CN/EN architecture and
feature summaries, configuration examples, and the real E2E runbook. No release
tag, publish, or marketplace upload is part of this design.
