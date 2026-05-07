/**
 * Douyin MAIN-world fetch-tap.
 *
 * Pattern: install a wrapper around `window.fetch` (and
 * `XMLHttpRequest.prototype.send`) that **observes** the response
 * bodies of `/aweme/v1/web/aweme/{post,favorite,like}/` and
 * `/aweme/v1/web/user/follow/list/` calls and posts captured items
 * back to the content script via `window.postMessage`. Douyin's own
 * `webmssdk.js` has already signed the outgoing call before our
 * wrapper sees it, so we never compute X-Bogus / msToken / `_signature`
 * ourselves.
 *
 * **Critical timing detail** (verified empirically 2026-05-07 via
 * chrome-devtools MCP probe — see
 * docs/plans/2026-05-06-douyin-bootstrap-import-design.md §3 step 5):
 * Douyin's page bundle wraps `window.fetch` with its own axios-style
 * wrapper *after* document_start. Installing at `runAt:"document_start"`
 * is shadowed by the page bundle's later wrapper and captures **zero**
 * responses. The bootstrap content script must
 * `await waitForDouyinSdk(window, 8000)` (polling for
 * `window.byted_acrawler`) before calling `installFetchTap`.
 * Wrapping the SDK's wrapper preserves the signing (their wrapper
 * signs internally) and adds our observation as the outermost layer.
 *
 * This module does NOT auto-install. Side effects only happen when
 * the content script explicitly calls `installFetchTap(window, ...)`.
 */

export type DouyinScope = "dy_post" | "dy_collect" | "dy_like" | "dy_follow";

export interface DouyinBootstrapItem {
  scope: DouyinScope;
  aweme_id: string;
  creator_sec_uid: string;
  url: string;
  title: string;
  author: string;
  author_sec_uid: string;
  cover_url: string;
}

/**
 * Map a Douyin API URL to a bootstrap scope, or null if the endpoint
 * is not one we care about. Used by both the fetch-tap to decide
 * whether to capture and by the executor to route incoming
 * postMessage events.
 *
 * Endpoint catalog cross-referenced with Johnserf-Seed/f2 (Apache-2.0,
 * read-only reference — see design doc §"Open-Source Prior Art").
 * Empirically validated against real /jingxuan landing-page traffic.
 */
export function classifyDouyinResponseUrl(url: string): DouyinScope | null {
  if (!url) return null;
  // Strip query string before matching so request_source params don't
  // disturb the path-based decision.
  const path = url.split("?", 1)[0] ?? "";
  if (path.includes("/aweme/v1/web/aweme/post/")) return "dy_post";
  if (path.includes("/aweme/v1/web/aweme/favorite/")) return "dy_collect";
  if (path.includes("/aweme/v1/web/aweme/collection/")) return "dy_collect";
  if (path.includes("/aweme/v1/web/aweme/like/")) return "dy_like";
  if (path.includes("/aweme/v1/web/user/follow/list/")) return "dy_follow";
  if (path.includes("/aweme/v1/web/user/following/list/")) return "dy_follow";
  return null;
}

function pickString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function pickFirstUrl(coverField: unknown): string {
  if (!coverField || typeof coverField !== "object") return "";
  const cover = coverField as { url_list?: unknown };
  if (!Array.isArray(cover.url_list)) return "";
  const first = cover.url_list.find((u) => typeof u === "string" && u);
  return typeof first === "string" ? first : "";
}

function pickAuthor(awemeAuthor: unknown): { nickname: string; sec_uid: string } {
  if (!awemeAuthor || typeof awemeAuthor !== "object") return { nickname: "", sec_uid: "" };
  const a = awemeAuthor as { nickname?: unknown; sec_uid?: unknown };
  return {
    nickname: pickString(a.nickname),
    sec_uid: pickString(a.sec_uid),
  };
}

/**
 * Parse a `/aweme/v1/web/aweme/{post,favorite,like}/` response into
 * normalized items. Tolerates missing `aweme_list`, wrong types,
 * and individual-row malformations (drops the bad row, keeps the rest).
 *
 * Field shape reference:
 * - `aweme_id`: stable id, used as identity key
 * - `desc` / `preview_title`: title (real /aweme/v2/web/module/feed/
 *   samples shipped preview_title alongside a blank desc — accept both)
 * - `author.nickname` / `author.sec_uid`: creator
 * - `video.cover.url_list[]`: cover image candidates
 */
export function parseAwemeListResponse(
  json: unknown,
  scope: DouyinScope,
): DouyinBootstrapItem[] {
  if (!json || typeof json !== "object") return [];
  const root = json as { aweme_list?: unknown };
  if (!Array.isArray(root.aweme_list)) return [];

  const items: DouyinBootstrapItem[] = [];
  for (const raw of root.aweme_list) {
    if (!raw || typeof raw !== "object") continue;
    const aweme = raw as {
      aweme_id?: unknown;
      desc?: unknown;
      preview_title?: unknown;
      author?: unknown;
      video?: { cover?: unknown };
    };
    const awemeId = pickString(aweme.aweme_id);
    const title = pickString(aweme.desc) || pickString(aweme.preview_title);
    if (!awemeId && !title) continue;
    const author = pickAuthor(aweme.author);
    const coverUrl = pickFirstUrl(aweme.video?.cover);
    items.push({
      scope,
      aweme_id: awemeId,
      creator_sec_uid: "",
      url: awemeId ? `https://www.douyin.com/video/${awemeId}` : "",
      title,
      author: author.nickname,
      author_sec_uid: author.sec_uid,
      cover_url: coverUrl,
    });
  }
  return items;
}

/**
 * Parse a `/aweme/v1/web/user/follow/list/` response into normalized
 * items. Accepts both `followings` and `follow_list` as the array key
 * since f2 references show the variant has shifted historically.
 */
export function parseUserFollowListResponse(json: unknown): DouyinBootstrapItem[] {
  if (!json || typeof json !== "object") return [];
  const root = json as { followings?: unknown; follow_list?: unknown };
  const list = Array.isArray(root.followings)
    ? root.followings
    : Array.isArray(root.follow_list)
      ? root.follow_list
      : null;
  if (!list) return [];

  const items: DouyinBootstrapItem[] = [];
  for (const raw of list) {
    if (!raw || typeof raw !== "object") continue;
    const creator = raw as {
      sec_uid?: unknown;
      nickname?: unknown;
      avatar_thumb?: unknown;
    };
    const secUid = pickString(creator.sec_uid);
    if (!secUid) continue;
    const nickname = pickString(creator.nickname);
    const avatarUrl = pickFirstUrl(creator.avatar_thumb);
    items.push({
      scope: "dy_follow",
      aweme_id: "",
      creator_sec_uid: secUid,
      url: `https://www.douyin.com/user/${secUid}`,
      title: nickname,
      author: nickname,
      author_sec_uid: secUid,
      cover_url: avatarUrl,
    });
  }
  return items;
}

/**
 * Poll `target.byted_acrawler` until it appears or the timeout elapses.
 * Resolves true on appearance, false on timeout. The 50ms poll
 * cadence is fine: the SDK is loaded by a synchronous script tag
 * relatively early, and a real installer typically waits 200-1500ms
 * before resolving.
 */
export async function waitForDouyinSdk(
  target: Window,
  timeoutMs: number,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  // Cast through unknown to touch the SDK-bearing field on Window.
  const t = target as unknown as { byted_acrawler?: unknown };
  while (Date.now() < deadline) {
    if (t.byted_acrawler) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return Boolean(t.byted_acrawler);
}

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

/**
 * Install the fetch-tap onto `target.fetch`. Wraps whatever
 * `target.fetch` is at install time, which in production is the
 * SDK's already-installed wrapper (see waitForDouyinSdk above).
 *
 * The callback runs on every captured response. The fetch-tap never
 * mutates the original Response — we use `Response.clone()` so the
 * page's own consumer reads the body untouched.
 *
 * Returns a disposer that restores the original `target.fetch`.
 */
export function installFetchTap(
  target: Window,
  postBack: (items: DouyinBootstrapItem[], scope: DouyinScope) => void,
): () => void {
  const w = target as unknown as { fetch: FetchLike };
  const originalFetch = w.fetch;

  const wrapped: FetchLike = async (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : (input as Request).url;
    const resp = await originalFetch(input, init);
    const scope = classifyDouyinResponseUrl(url);
    if (scope) {
      try {
        const json: unknown = await resp.clone().json();
        const items =
          scope === "dy_follow"
            ? parseUserFollowListResponse(json)
            : parseAwemeListResponse(json, scope);
        if (items.length > 0) {
          postBack(items, scope);
        }
      } catch {
        // Body wasn't JSON or already consumed — silent skip is the
        // right move; we never want to throw inside fetch-tap because
        // the page's React app would observe the rejection.
      }
    }
    return resp;
  };

  w.fetch = wrapped;
  return (): void => {
    w.fetch = originalFetch;
  };
}

// ---------------------------------------------------------------------------
// Auto-install when loaded as a content_scripts MAIN-world script
// ---------------------------------------------------------------------------
//
// Side-effect block guarded by ``typeof window !== "undefined"`` so
// node:test importing the module for pure-helper tests doesn't trigger
// any real installation. Mirrors the xhs-state-bridge.ts pattern.

const FETCH_TAP_MESSAGE_TYPE = "OPENBILICLAW_DOUYIN_AWEME_PAGE";
// Install-status sentinel: MAIN world emits one of these on install
// resolve so the isolated-world content script can tell whether the
// fetch-tap successfully wrapped page-bundle fetch (status="installed")
// or whether SDK detection timed out (status="skipped_no_sdk"). Used
// for diagnosing scope_status=empty results — without this we can't
// tell "captured 0 because SDK never loaded" from "captured 0 because
// risk-control empty-200'd everything".
const FETCH_TAP_INSTALL_TYPE = "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL";

/**
 * Replay an install-status ping a few times, spaced apart, so an
 * isolated-world content script that registered its listener slightly
 * after MAIN-world install resolved still catches one. Defensive
 * against the race we observed in the 2026-05-08 e2e probe.
 *
 * Three pings × 500ms apart covers:
 *   - content script at document_start (catches first ping at T+0)
 *   - content script at document_idle (catches third ping at T+1000ms)
 *   - any unexpected delay short of 1.5s
 */
function replayInstallStatusPing(status: "installed" | "skipped_no_sdk"): void {
  const fire = (): void => {
    window.postMessage({ type: FETCH_TAP_INSTALL_TYPE, status }, window.location.origin);
  };
  fire();
  setTimeout(fire, 500);
  setTimeout(fire, 1_000);
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  // Generous timeout: real e2e probe (2026-05-08) showed
  // skipped_no_sdk on slow page-bundle loads even when the user was
  // logged in. Bumped 8s → 15s so first navs after a chrome.tabs.create
  // have headroom; subsequent SPA-route reloads in the same tab usually
  // resolve in <500ms.
  void waitForDouyinSdk(window, 15_000).then((ready) => {
    if (!ready) {
      replayInstallStatusPing("skipped_no_sdk");
      // eslint-disable-next-line no-console
      console.debug("[OpenBiliClaw] dy fetch-tap skipped: SDK not detected");
      return;
    }
    installFetchTap(window, (items, scope) => {
      window.postMessage(
        { type: FETCH_TAP_MESSAGE_TYPE, scope, items },
        window.location.origin,
      );
    });
    replayInstallStatusPing("installed");
    // eslint-disable-next-line no-console
    console.debug("[OpenBiliClaw] dy fetch-tap installed (MAIN world)");
  });
}
