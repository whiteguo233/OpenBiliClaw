# Instagram browser protocol recheck — 2026-09-28

Scope: bounded primary-source research only; no Instagram requests, browser navigation, credentials, account mutations, or live capability claims. This note records protocol facts, not an upstream support guarantee. No external implementation code was copied.

## Identity: verified facts

- Maintained `mautrix/meta` HEAD was resolved with `git ls-remote` to `e012f9f83ee0abd332b6160f57ecd07014c2d445`. Its browser configuration model includes `PolarisViewer`, with outer `id`, nested `data.id`, and `data.username`. Its `GetUserId` uses the outer ID; `GetUsername` uses the nested username. This is a useful, narrowly named current-viewer structure, unlike a recursive search for any profile ID. [Pinned account model](https://github.com/mautrix/meta/blob/e012f9f83ee0abd332b6160f57ecd07014c2d445/pkg/messagix/types/account.go)
- The same project models `XIGSharedData.native.config.viewerId` and named browser configuration modules including `PolarisViewer` and `CurrentUserInitialData`. The latter has separate `USER_ID`, `NON_FACEBOOK_USER_ID`, and `IG_USER_EIMU` fields. Their mere presence does not establish that an arbitrary one is an Instagram numeric account ID. [Pinned configuration schema](https://github.com/mautrix/meta/blob/e012f9f83ee0abd332b6160f57ecd07014c2d445/pkg/messagix/types/configs.go), [identity fields](https://github.com/mautrix/meta/blob/e012f9f83ee0abd332b6160f57ecd07014c2d445/pkg/messagix/types/account.go)
- Browser module parsing handles structured script JSON and `__bbox` require/define containers; named configuration payloads are decoded separately. Its socket setup prefers `PolarisViewer.ID` before a generic current-user ID. This supports considering a strict named SSR viewer parser, not arbitrary HTML ID extraction. [Pinned parser](https://github.com/mautrix/meta/blob/e012f9f83ee0abd332b6160f57ecd07014c2d445/pkg/messagix/httpclient/js_module_parser.go), [module handling](https://github.com/mautrix/meta/blob/e012f9f83ee0abd332b6160f57ecd07014c2d445/pkg/messagix/httpclient/modules.go), [socket selection](https://github.com/mautrix/meta/blob/e012f9f83ee0abd332b6160f57ecd07014c2d445/pkg/messagix/configs.go)
- `instagrapi` HEAD was resolved to `13ebe3b73f9a3fc2d495124c94d958d7b438e007`. `account_info` calls `private_request("accounts/current_user/?edit=true")`; the private transport constructs a URL using configured `API_DOMAIN`, which is `i.instagram.com`, with an Android app user-agent configuration. This is **not** evidence that the identical path works on browser same-origin `www.instagram.com`. [Pinned account implementation](https://github.com/subzeroid/instagrapi/blob/13ebe3b73f9a3fc2d495124c94d958d7b438e007/instagrapi/mixins/account.py), [private transport](https://github.com/subzeroid/instagrapi/blob/13ebe3b73f9a3fc2d495124c94d958d7b438e007/instagrapi/mixins/private.py), [domain configuration](https://github.com/subzeroid/instagrapi/blob/13ebe3b73f9a3fc2d495124c94d958d7b438e007/instagrapi/config.py)

## Discovery: evidence limits and timing

Bounded GitHub searches did **not** establish a maintained, pinned OSS implementation for the exact keys `xig_logged_out_popular_search_media_info` or `polaris_ordered_timeline_connection`. Do not attribute those shapes to researched OSS. Existing project evidence instead records a public creator SSR collection under `xig_user_by_username.polaris_ordered_timeline_connection`, and a prior logged-out topic task. These historical observations do not prove the latest installed task succeeds. [Local acceptance record](../platform-source-acceptance.instagram.md)

Browser timing fact: `DOMContentLoaded` follows document parsing and deferred scripts, but does not wait for asynchronous scripts. A setup that executes after the event must check `readyState`; a one-time script scan is not a general readiness guarantee. `MutationObserver` can observe subtree additions and text changes. [MDN event semantics](https://developer.mozilla.org/en-US/docs/Web/API/Document/DOMContentLoaded_event), [observer semantics](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver/observe)

**Hypothesis, not verified upstream diagnosis:** a bounded rescan / observer of allowlisted JSON script containers may recover late inserted SSR envelopes missed by the initial scan. Tests should distinguish parser shape failures, late script insertion, and genuinely absent envelopes. The main agent must validate this against the existing code and a fresh bounded live run; waiting longer alone is not proof of a fix.

## Safe implementation and acceptance boundaries

1. Prefer passive, named `PolarisViewer` evidence already delivered to the task-owned current-session page. Require a nonzero numeric ID, nonempty username, and agreement of duplicate IDs. Reject conflicting viewers; do not substitute visited-profile author IDs, generic `CurrentUserInitialData` fields, or cookie hints for an authenticated numeric identity.
2. Any optional same-origin page read must be deliberately bounded and preserve terminal 429 / login / challenge handling. This research does not authorize fallback across hosts, device emulation, GraphQL ID guessing, or retries after rate limits.
3. The viewer module parser should decode only JSON data, never evaluate script text. Export only necessary normalized identity, never raw HTML, tokens, private form data, or complete configuration modules. Follow existing task/account isolation and stale-result checks.
4. Discovery must retain route/claim-bound allowlists and require an actual expected media envelope. Unrelated GraphQL, logged-in search, profile metadata, skeleton pages, and unknown HTML are not an empty-success result. Bound observer duration, parse volume, and replay; disconnect observers at task completion.
5. Current-user identity success alone does not prove likes, saves, followings, event persistence, profile initialization, discovery candidate ingestion, or LLM evaluation. Each stage needs its own recorded result. Preserve incomplete / partial / failed distinctions.
6. Upstream 429 remains a stop condition; absence of a usable envelope remains a visible failure. Neither source research nor synthetic fixture success upgrades live acceptance to PASS.

## Follow-up: web liked-posts protocol (bounded GitHub-only research)

Trigger supplied by the main agent: browser `/api/v1/feed/liked/` returned HTTP 400 `useragent mismatch`, while saved posts returned HTTP 200. This observation does not authorize changing or spoofing the user agent. No Instagram request was made during this research.

Two primary implementation sources provide a **web Bloks lead**, not a verified GraphQL liked-feed replacement:

- A May 2026 userscript restricts itself to `/your_activity/interactions/likes*`, observes fetch/XHR URLs containing `/wbloks/fetch/`, removes the anti-JSON-hijacking prefix, and reads `payload.layout.bloks_payload.tree`. Its parser finds `on_bind` expressions and media maps containing `media_id`, `media_code`, `media_type`, and `media_image_url`. This demonstrates an implementation targeting the native web activity response rather than mobile `feed/liked`. It is a single gist, not a maintained library or official API guarantee. It also performs Telegram forwarding and automatic reloads: **do not install or execute it**. [Pinned source revision](https://gist.github.com/hoosnick/ca870e45edcf5d20d47938f6b1c20faf/8eeeb19c245e243b28360cf4ec25f0f827060bfc)
- A separate destructive unlike script contains a collection phase using same-origin `/async/wbloks/fetch/`, with application names `com.instagram.privacy.activity_center.liked_refresh` and `com.instagram.privacy.activity_center.liked_next`. It sends page-specific request metadata and version/state parameters. The script later performs an unlike mutation; that mutation is **not** a read protocol and must never be run or copied into initialization. Its hardcoded UI state/version values are not a portable contract. This corroborates the Bloks family, but not safe replay of the entire request. [Pinned source revision](https://gist.github.com/3lyly0/89fee1c20c7fbf29d8606cca82ae3357/12e96efa34c5c9a85dad829dcc9355221bf1b48e)

Practical next step (hypothesis requiring native-page evidence): observe the current session's normally rendered Likes activity page without clicking Select/Unlike, and inspect only normalized route/appid/schema metadata from naturally issued requests. If its native initial response supplies the expected Bloks tree, implement a claim/account-bound, bounded **data-only** parser with exact read application allowlists. Never evaluate `on_bind` expressions or execute embedded actions. Preserve string IDs rather than coercing large identifiers to JavaScript Number. Require affirmative empty/pagination evidence; lack of parsed media is not proof of an empty liked history. Missing captions, authors, and like timestamps remain unknown unless separately supported; source evidence does not establish them.

No verified current GraphQL liked-list query was found in this bounded search. Native Bloks request observation is the better-supported lead; active request reconstruction, fabricated version/state tokens, repeated reloads, Telegram forwarding, and destructive actions are outside this finding. Live acceptance remains unchanged.

### Data-only expression subset

The inspected observer accepts nested parenthesized operator expressions, comma-separated arguments, escaped double-quoted strings, bare booleans, and numeric tokens. `bk.action.array.Make` forms an array from its arguments. `bk.action.map.Make` receives a keys array and a values array, paired by index. Primitive wrappers include `bk.action.i32.Const`, `bk.action.bool.Const`, and `bk.action.string.Const`. These are facts about that reference implementation's accepted subset, **not** a complete official Bloks grammar. [Pinned reference](https://gist.github.com/hoosnick/ca870e45edcf5d20d47938f6b1c20faf/8eeeb19c245e243b28360cf4ec25f0f827060bfc)

Entirely invented illustration, not a captured Instagram response or copied implementation:

```text
(bk.action.map.Make,
  (bk.action.array.Make, "label", "quantity", "enabled"),
  (bk.action.array.Make,
    (bk.action.string.Const, "example only"),
    (bk.action.i32.Const, 2),
    (bk.action.bool.Const, true)))
```

Its intended data interpretation is `{label: "example only", quantity: 2, enabled: true}`. A safer independent parser should reject mismatched array lengths, duplicate or prototype-related map keys, invalid primitive arity/types, excessive depth/size, and unsupported expressions used as data. Preserve large identifier tokens losslessly; the reference's generic JavaScript Number coercion is not suitable for account/media IDs. Never invoke operators, callbacks, navigation, network actions, or arbitrary expressions. An empty media extraction is still not affirmative empty-history evidence; use the route-bound native empty-state evidence independently.
