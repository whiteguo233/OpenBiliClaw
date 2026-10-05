# Six-Platform Native-Save Authorized E2E Runbook

> This runbook changes real platform accounts. Do not run a native-save write unless the user
> grants current, named authorization for one exact public item and target, or supplies a test
> account. Task 10 defines the harness only; it performs no real account writes.

## Scope and login boundary

YouTube, Xiaohongshu, Douyin, X/Twitter, Zhihu, and Reddit execute native saves inside the
installed OpenBiliClaw extension using that browser's existing platform login. The authorization
and result records never receive account IDs, cookies, tokens, session credentials, HTML, response bodies,
or full URLs. Xiaohongshu is the narrow navigation exception: an already-stored public-note URL
may carry only `xsec_token` and `xsec_source` into the exact extension job because a bare note URL
is not openable; neither value enters the authorization or result record. A temporary automation
browser, CDP session, copied cookie, or backend credential is not a substitute for the installed
extension's real login state.

Automatic sync remains default-off: `[saved_sync].auto_sync_enabled = false`. A local save is
always committed first. A manual favorite or manual watch-later sync is a separate, explicit
trigger and bypasses the automatic-sync switch. Enabling automatic sync requires explicit
auto-sync consent for the named test. Local removal is one-way cleanup: platform saves remain.
The mandatory matrix is auto-sync-off local-only, manual favorite, manual watch-later, explicit
auto-sync consent, duplicate `already_synced`, and local cleanup while platform saves remain.

## Exact platform mapping

`expected_target` is case-sensitive and must equal the platform capability target. `OpenBiliClaw`
is YouTube's exact playlist title; `知乎收藏` is Zhihu's persisted item-level global favorite
state, not a named collection.

| Platform | Favorite target | Watch-later target |
| --- | --- | --- |
| YouTube | `OpenBiliClaw` | `YouTube Watch Later` |
| Xiaohongshu | `小红书收藏` | `小红书收藏` |
| Douyin | `抖音收藏` | `抖音收藏` |
| X/Twitter | `X Bookmarks` | `X Bookmarks` |
| Zhihu | `知乎收藏` | `知乎收藏` |
| Reddit | `Reddit Saved` | `Reddit Saved` |

Watch later therefore uses a distinct native target only on YouTube. The other five platforms
resolve it to their favorite/bookmark/Saved target. YouTube favorite uses the exact-title
`OpenBiliClaw` playlist; Zhihu uses the current global `收藏 / 已收藏` toggle and does not claim
named-collection support.

## Authorization envelope

Immediately before each state-changing native-save case, obtain an authorization envelope with
exactly these five fields:

```json
{
  "allow_state_changing": true,
  "platform": "reddit",
  "action": "favorite",
  "content_id": "t3_public1",
  "expected_target": "Reddit Saved"
}
```

The extension harness rejects the request unless `allow_state_changing=true` and all four named
values are present. `platform` must be one of the six rows; `action` must be `favorite` or
`watch_later`; public content_id must be a public platform identity (not a profile/account identity or
URL); and `expected_target` must exactly match the mapping. Extra fields fail closed, including
`account_id`, `cookie`, `token`, `html`, `response_body`, and `content_url`.

The public-ID forms accepted by the harness are intentionally narrow:

- YouTube: the 11-character public video ID.
- Xiaohongshu: the 24-character lowercase hexadecimal note ID.
- Douyin and X/Twitter: the public numeric content/status ID.
- Zhihu: `question:<digits>`, `answer:<digits>`, or `article:<digits>`.
- Reddit: the public `t3_<id>` post or `t1_<id>` comment fullname.

An ID-shaped string alone cannot prove that a numeric value names content rather than an account.
Select `content_id` only from the already validated durable native-save task whose canonical
content URL, platform, content type, and `item_key` establish the public content identity. Discard
the URL before constructing the authorization or result record.

Authorization is per platform, action, item, and target. Do not reuse an authorization for another
row, another item, the automatic-sync case, cleanup, or a later run.

## Bound execution path

The authorization/result helpers in `background/e2e-runner.ts` are pure fail-closed builders. The
generic capture self-test (`OBC_E2E_EXECUTE`) refuses every favorite/bookmark request, even when
its envelope is valid, because a platform landing-page selector cannot prove it is mutating the
named `content_id`. Native-save uses a separate trusted-local `extension_e2e_run` mode with empty
generic platforms/actions and one exact `native_save_authorization`.

Invoke that mode only after the named item already exists in the corresponding local saved list:

```json
{
  "allow_state_changing": true,
  "timeout_seconds": 180,
  "native_save_authorization": {
    "allow_state_changing": true,
    "platform": "reddit",
    "action": "favorite",
    "content_id": "t3_public1",
    "expected_target": "Reddit Saved"
  }
}
```

POST it to trusted-local `/api/extension/e2e/run`. The backend schema rejects extra fields, false
consent, wrong targets, unsafe IDs, and any mixture with generic `actions`. The runtime event does
not carry a content URL or account material. Before publishing, the backend loads the exact saved
membership and verifies its platform, content ID, executor-supported content type, canonical HTTPS
content URL identity, resolved action, and target through the production router. A missing row,
profile/account URL, or mismatch returns a fixed 422 without publishing or creating a task.
Canonical URL preflight mirrors the production executor: HTTPS only, no credentials, explicit
port or fragment; exact executor host set; and exact route cardinality. Queries are rejected except
for YouTube's identity `v` and Xiaohongshu's single nonempty `xsec_token` plus optional single
nonempty `xsec_source`; every other key and duplicate is rejected. In
particular YouTube accepts only `/watch?v=<id>`, `/shorts/<id>`, or the one-segment `youtu.be`
form; Xiaohongshu and Douyin reject trailing route segments; X accepts only
`/i/status/<id>` or `/<user>/status/<id>`; Zhihu binds the typed content kind to its exact route;
and Reddit binds post/comment fullname to the executor's permalink positions. The router's
`resolved_action` must also be exact: five non-YouTube watch-later rows resolve to `favorite`.

After a fresh authorization passes preflight, the dedicated extension branch triggers the normal
saved API operation. The only executable path is the production durable path:

```text
POST /api/saved/{favorite|watch_later}/sync
  -> validated canonical saved item + durable sync task
  -> extension_native_save_jobs
  -> /api/sources/<slug>/next-task
  -> installed extension native-save task runner
  -> exact task/platform/item/content URL/action/target checks immediately before mutation
  -> authenticated /api/sources/<slug>/task-result
```

The branch requires exactly one returned item, the original task ID on every poll, and the
canonical item key. The real creation snapshot is normally `pending` with the requested action and
an empty target because routing has not run yet; the harness must keep polling that same task.
After routing, resolved action/target must match, and exact target is mandatory for terminal
`synced`/`already_synced`. A correlation or transport uncertainty records only `pending`/`syncing`,
never a false `failed` that might invite a duplicate retry while the durable write continues. The
backend sends an absolute execution deadline one second before its callback deadline, reserving a
full second for the six-field callback before run-registry cleanup. Endpoint resolution, device
session/401 authentication, every request, and every clamped poll sleep are charged to the
execution deadline; callback endpoint resolution and authentication are charged to the callback
deadline. This path covers both actions
for all six platforms. Never add a second generic DOM-click path for the harness, and never POST a
tab URL, selector detail, raw executor action array, or raw error to the safe result ledger.

## Required verification sequence

Use a public item the user named and the installed extension browser. Keep automatic sync at its
original value on exit, including interruption and failure paths.

1. **Auto-sync-off local-only:** set and re-read `auto_sync_enabled = false`; save the named item
   locally and confirm the response has an empty `sync_task_id`. Confirm no native-save job or
   platform membership was created.
2. **Manual favorite:** obtain a fresh favorite authorization, trigger the saved page's manual
   sync, poll the durable task, and verify the exact favorite target.
3. **Manual watch-later:** use a different named public item and fresh authorization. Verify
   `YouTube Watch Later` on YouTube and the favorite fallback target on the other five rows.
4. **Explicit automatic-sync consent:** name a third public item, obtain fresh authorization,
   explicitly enable auto-sync, re-read the effective setting, save locally, and poll its nonempty
   task ID. Restore the original setting immediately after this case.
5. **Duplicate:** after a successful platform save, remove only its local membership, re-add it
   with auto-sync off, then manually sync with fresh authorization. The executor must return
   `already_synced` without duplicating the container or membership.
6. **Local cleanup:** remove only the local OpenBiliClaw memberships created for the run. Confirm the
   platform saves remain. Never invoke an unfavorite, unbookmark, playlist removal, collection
   removal, or Saved removal as implicit cleanup.

Stopping after step 1 is the correct result when named authorization or installed-extension login
is unavailable. Record that the real write is unverified; do not infer consent from an earlier run.

## Safe result schema

Persist or paste only this six-field record:

```json
{
  "platform": "reddit",
  "action": "favorite",
  "content_id": "t3_public1",
  "expected_target": "Reddit Saved",
  "task_status": "already_synced",
  "error_code": ""
}
```

No title, author, account ID, cookie, token, HTML, response body, full URL, executor message,
screenshot text, or raw platform error may be added. URLs with secrets are forbidden even when
the URL points to public content.

## Status and error semantics

- `pending` / `syncing`: nonterminal; keep polling the same durable task ID, but do not record the
  task ID in the safe result.
- `synced`: this execution confirmed the platform changed to the expected target.
- `already_synced`: the exact target already contained the item and no second mutation occurred.
- `login_required`: the installed extension's platform session is absent or expired.
- `rate_limited`: the executor observed correlated platform throttle/risk evidence.
- `unsupported` with `unsupported_content_type`: the platform cannot represent this exact item or
  action; it remains local-only.
- `extension_required`: no connected installed extension claimed the job; the safe code may be
  `extension_unavailable`.
- `failed`: only a fixed bounded code is accepted: `adapter_exception`, `adapter_timeout`,
  `extension_task_timeout`, `interrupted`, `invalid_adapter_result`, `item_heartbeat_failed`,
  `native_confirmation_not_observed`, `native_content_not_ready`, `native_control_not_found`,
  `native_dialog_not_opened`, `native_request_rejected`, `native_target_not_found`,
  `native_save_failed`, `native_save_timeout`, `not_saved_locally`, or
  `sync_already_in_progress`. Never copy a raw error message or response body; arbitrary
  snake-case text is rejected because it can still contain secret material.

Local success is never rolled back by these terminal states. A failed or uncertain mutation is not
automatically retried; any retry needs a new explicit authorization.

The same in-flight task may perform one read-only persisted-state verification after an executor
reports `native_confirmation_not_observed`. For Douyin, Xiaohongshu, YouTube, and Zhihu, the runner
first aborts and awaits the mutation sender, reloads the exact content URL, and requires a fresh
`NATIVE_SAVE_READY` response whose `document_instance_id` differs from the mutation document. It
then sends `verification_only` with a new `execution_id`; a platform verifier may open a necessary
read-only dialog, but it must not create a container or click a target row. Zhihu only reads the
global favorite control and never clicks `已收藏`. Positive proof returns `already_synced`; every other verifier terminal or exception preserves
the original failure. This is continuation of the already-authorized task, not a second mutation
or a new retry. The task-result transport requires 2xx and may resend only the exact same payload
within its bounded callback deadline; the backend idempotently ACKs only an identical canonical
terminal replay.

## Evidence state

Task 10 verified the authorization and result-schema harness with fixtures. The 2026-07-13 first
favorite pass exposed real SPA gaps. After those fixes, a newly authorized 2026-07-14 run cleared
the selected items' old local terminal states and forced every case through the production durable
broker and the currently logged-in platform account. These are the only safe result fields retained:

| Platform | Action | Public content ID | Expected target | Terminal status | Safe code |
| --- | --- | --- | --- | --- | --- |
| YouTube | favorite | `SdQRhJl7Bvo` | `OpenBiliClaw` | `already_synced` | — |
| YouTube | watch_later | `cvh0xXmu0bs` | `YouTube Watch Later` | `already_synced` | — |
| Xiaohongshu | favorite | `6a531204000000001003eae2` | `小红书收藏` | `already_synced` | — |
| Xiaohongshu | watch_later | `6a531204000000001003eae2` | `小红书收藏` | `already_synced` | — |
| Douyin | favorite | `7636735113514011939` | `抖音收藏` | `already_synced` | — |
| Douyin | watch_later | `7640353849837123526` | `抖音收藏` | `already_synced` | — |
| X/Twitter | favorite | `2063895528816181253` | `X Bookmarks` | `already_synced` | — |
| X/Twitter | watch_later | `2063895528816181253` | `X Bookmarks` | `already_synced` | — |
| Zhihu | favorite | `answer:2053546899609740246` | `知乎收藏` | `already_synced` | — |
| Zhihu | watch_later | `answer:2053546899609740246` | `知乎收藏` | `already_synced` | — |
| Reddit | favorite | `t3_x2eklf` | `Reddit Saved` | `already_synced` | — |
| Reddit | watch_later | `t3_x2eklf` | `Reddit Saved` | `already_synced` | — |

The same session separately forced Bilibili `BV1xx411c7mD` through its direct adapter: favorite
finished `synced` at `B站 OpenBiliClaw 收藏夹`, and watch-later finished `synced` at `B站稍后再看`.
Before the batch, `GET /api/config` reported `saved_sync.auto_sync_enabled=false`; four newly added
watch-later memberships returned `pending` with an empty `sync_task_id`, proving that local card
saves did not mutate accounts. Every platform write above was started manually from the saved-list
sync route. YouTube's account contained multiple exact `OpenBiliClaw` playlists; the executor did
not delete them, preferred an already checked exact row, and otherwise uses stable DOM order.
