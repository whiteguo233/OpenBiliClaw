# Firefox AMO source build instructions

This archive contains the complete human-readable source needed to reproduce the Firefox
package submitted to Mozilla Add-ons. No commercial or private build tools are required.

## Build environment

- Ubuntu 24.04 LTS (the GitHub Actions runner used for submission)
- Node.js 22
- npm, using the committed `extension/package-lock.json`

## Reproduce the submitted package

From the archive root:

```bash
cd extension
npm ci
npm run build:firefox
```

The reviewed extension is written to `extension/dist-firefox/`. The build uses TypeScript and
the open-source esbuild package declared in `extension/package.json`; it bundles readable
TypeScript entry points and copies the shared browser UI module from
`src/openbiliclaw/web/shared/`. Source maps are included in the submitted extension.

The submission workflow packages this source directly from the same Git commit, builds
`dist-firefox`, synchronizes `docs/privacy.md`, and invokes `web-ext sign --channel=listed`.

## Privacy-relevant build note

`extension/src/main/xhs-token-sniffer.ts` is bundled as a document-start MAIN-world script for
Xiaohongshu pages. It observes the page's own fetch/XHR responses without changing requests.
For the exact web search-notes endpoint it forwards only a bounded, normalized set of public
card fields and existing per-note access tokens to the same-tab isolated task executor; it never
forwards raw responses or cookie values. The resulting task payload is sent only to the
user-configured OpenBiliClaw backend. See `docs/privacy.md` for the complete disclosure.

`https://linux.do/*` is a required host permission for two bounded features: the regular-page
behavior adapter, and isolated task tabs created by the extension for Linux.do source work. The
task executor only issues same-origin GET requests for public search/hot/feed/creator/related
discovery and signed-in bookmarks/likes/read-history bootstrap. It never performs a posting,
like, bookmark, follow, edit, or other state-changing request. `/session/current.json` positively
identifies the account for personal scopes; the `_t` cookie is reduced to a boolean login hint,
and its value is never uploaded. Cookie values, CSRF data, raw JSON/HTML responses, and challenge
page bodies never enter the task result. Only bounded normalized topic fields, scope counts, or a
structured error are sent to the user-configured backend. Automated tests cover the protocol,
pagination/caps, timeout/error mapping, and task-tab isolation; a real installed-extension E2E
with a signed-in Linux.do account has not yet been completed.
`extension/src/content/v2ex.ts` and the V2EX task executor run only on declared V2EX hosts.
Normal Topic collection is passive and read-only. A user-triggered bootstrap or incremental
task reads only bounded rendered fields from the user's topic, public-reply, favorite-topic,
and favorite-Node pages. The background login check asks Firefox only whether the `A2` cookie
exists and sends a boolean plus the public rendered username; it never accesses, stores, or
sends the cookie value. The build contains no V2EX write client and does not forward page HTML,
headers, CSRF/once values, private messages, or browser history.

`*://*.instagram.com/*` is required only for the experimental, disabled-by-default Instagram
source. The extension creates isolated, marked task tabs for bounded public topic/creator reads
or an explicitly selected initialization import of liked/saved/following rows. The latter first
requires a same-origin current-account response with a numeric id. Firefox is asked only whether
the `sessionid` cookie exists; its value, Authorization/CSRF headers, raw JSON/HTML, challenge
pages, browser history, and search history never enter a task result. The document-start
MAIN-world response tap sends only bounded normalized media/user fields, opaque pagination, and
terminal evidence to its same-tab isolated executor. The build contains no Instagram login,
2FA/challenge automation, Search input, or like/save/follow/comment/message write client.
