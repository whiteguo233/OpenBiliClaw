/**
 * OpenBiliClaw — TikTok content script entry.
 *
 * Injected into tiktok.com pages (isolated world). Listens for
 * TIKTOK_SCOPE_EXECUTE messages from the background dispatcher, drives
 * the single-scope scrape — click-driven navigation to the requested
 * account view (profile Liked/Favorites tab, Following modal, or the
 * avatar-menu Watch history entry), programmatic scroll to trigger
 * TikTok's virtual-list pagination, DOM harvest into a TiktokItemSink —
 * and posts one TIKTOK_SCOPE_RESULT back.
 *
 * DOM-only by design: no MAIN-world fetch-tap, no request signing, no
 * credential material. Only bounded normalized rows cross the bridge
 * (contract docs/platform-source-contract.tiktok.toml [task] buffer).
 *
 * Module isolation: zero imports from extension/src/content/dy/.
 */

import {
  detectTiktokAccessState,
  detectTiktokEmptyState,
  extractTiktokItemsFromDocument,
  pickTiktokScrollTarget,
  isTiktokElementVisible,
  isTiktokOwnProfile,
  resolveTiktokScopeRoot,
  TIKTOK_SCOPE_TAB_SELECTORS,
} from "./tt/dom-extractor.ts";
import {
  readTiktokUniversalData,
  TIKTOK_UNIVERSAL_DATA_ELEMENT_ID,
} from "./tt/bootstrap.ts";
import {
  classifyTiktokScopeCompletion,
  isValidTiktokScopeExecuteMessage,
  normalizeTiktokScrollRounds,
  TiktokItemSink,
  ttShouldContinueScroll,
  type TiktokAccessState,
  type TiktokScope,
  type TiktokScopeExecuteMessage,
  type TiktokScopeResult,
} from "./tt/task-executor.ts";
import { installNativeSaveExecutor } from "./native-save/runtime.ts";
import { saveTiktok, verifyTiktok } from "./native-save/tiktok.ts";

const SCROLL_DELAY_MS = 1_500;
const NAV_SETTLE_MS = 2_500;
const TAB_SETTLE_MS = 1_500;

async function waitForRenderedState(
  predicate: () => boolean, timeoutMs = 10_000,
): Promise<boolean> {
  // A completed document can still be waiting for TikTok's client hydration.
  // Keep this inside the dispatcher's existing idle/absolute task deadlines.
  const deadline = Date.now() + timeoutMs;
  for (let attempt = 0; attempt < Math.ceil(timeoutMs / 250); attempt++) {
    if (predicate()) return true;
    if (Date.now() >= deadline) break;
    await sleep(250);
  }
  return predicate();
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fireRealClick(el: HTMLElement): void {
  el.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true, composed: true }),
  );
}

function visibleText(el: HTMLElement): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

function findClickableByText(labels: string[]): HTMLElement | null {
  const normalized = labels.map((label) => label.trim().toLowerCase()).filter(Boolean);
  if (!normalized.length) return null;
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>(
      'a, button, [role="link"], [role="button"], [role="tab"], [role="menuitem"], [data-e2e], li',
    ),
  );
  for (const el of candidates) {
    const text = visibleText(el).toLowerCase();
    if (!text || text.length > 40 || !isTiktokElementVisible(el)) continue;
    if (normalized.some((label) => text === label || text.includes(label))) return el;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Access state — fail closed, never a pseudo-empty success
// ---------------------------------------------------------------------------

function readPageIdentity(): ReturnType<typeof readTiktokUniversalData> {
  return readTiktokUniversalData(
    document.getElementById(TIKTOK_UNIVERSAL_DATA_ELEMENT_ID)?.textContent ?? "",
  );
}

function resolveAccessState(): TiktokAccessState {
  const domState = detectTiktokAccessState(document, location.href);
  if (domState === "login_wall") return domState;
  const identity = readPageIdentity();
  if (identity.explicitLoggedOut) return "logged_out";
  if (domState === "signed_in" || identity.loggedIn) return "signed_in";
  return domState;
}

function resolveViewerHandle(): string {
  const identity = readPageIdentity();
  if (identity.explicitLoggedOut) return "";
  const anchor = document.querySelector<HTMLAnchorElement>(
    '[data-e2e="nav-profile"] a[href*="/@"], a[data-e2e="nav-profile"][href*="/@"]',
  );
  const href = anchor?.getAttribute("href") ?? "";
  const navHandle = href.match(/\/@([\w.]{2,24})(?:[/?#]|$)/)?.[1] ?? "";
  const sessionHandle = identity.viewer.handle;
  if (sessionHandle && (identity.loggedIn ||
      detectTiktokAccessState(document, location.href) === "signed_in")) {
    // Settings has no nav-profile link. The canonical session user remains
    // usable with positive login evidence, but conflicting identities fail.
    if (navHandle && navHandle.toLowerCase() !== sessionHandle.toLowerCase()) return "";
    return sessionHandle;
  }
  return navHandle;
}

// ---------------------------------------------------------------------------
// Click-driven navigation (URL jumps trip risk control on the dy sibling;
// mirror the same discipline here)
// ---------------------------------------------------------------------------

interface ScopeNavReport {
  attempted: boolean;
  note: string;
  page_url: string;
}

const SCOPE_TAB_LABELS: Record<"tt_like" | "tt_collect", string[]> = {
  tt_like: ["Liked", "Likes", "喜欢"],
  tt_collect: ["Favorites", "收藏"],
};

function ownProfileVisible(handle: string): boolean {
  if (!isTiktokOwnProfile(location.href, handle)) return false;
  // TikTok can render the display name as title and the handle as subtitle.
  // Either field must still match the independently observed viewer exactly.
  return ['[data-e2e="user-title"]', '[data-e2e="user-subtitle"]'].some((selector) => {
    const heading = document.querySelector<HTMLElement>(selector);
    return Boolean(heading && isTiktokElementVisible(heading) &&
      visibleText(heading).replace(/^@/, "").toLowerCase() === handle.toLowerCase());
  });
}

async function ensureOnOwnProfile(handle: string): Promise<boolean> {
  if (!handle) return false;
  if (ownProfileVisible(handle)) return true;
  if (isTiktokOwnProfile(location.href, handle)) {
    // Re-clicking this link can full-navigate again before hydration, then
    // resume the same persisted scope in an endless reload loop.
    return waitForRenderedState(() => ownProfileVisible(handle), 20_000);
  }
  const profileLink = document.querySelector<HTMLElement>('[data-e2e="nav-profile"]');
  if (!profileLink || !isTiktokElementVisible(profileLink)) return false;
  fireRealClick(profileLink);
  // Clicking or changing a URL is not evidence that the app rendered our
  // profile. A full navigation resumes the persisted scope on the new page.
  return waitForRenderedState(() => ownProfileVisible(handle), 20_000);
}

async function navigateToProfileTab(
  scope: "tt_like" | "tt_collect",
  handle: string,
): Promise<ScopeNavReport> {
  const report: ScopeNavReport = {
    attempted: false,
    note: "",
    page_url: location.href,
  };
  if (!(await ensureOnOwnProfile(handle))) {
    report.note = "profile_entry_not_found";
    return report;
  }
  // Profile headings hydrate before the tab strip. Wait for the actual scope
  // entry; a rendered heading alone does not mean navigation is ready.
  await waitForRenderedState(() => TIKTOK_SCOPE_TAB_SELECTORS[scope].some((selector) => {
    const tab = document.querySelector<HTMLElement>(selector);
    return Boolean(tab && isTiktokElementVisible(tab));
  }) || Boolean(findClickableByText(SCOPE_TAB_LABELS[scope])));
  for (const sel of TIKTOK_SCOPE_TAB_SELECTORS[scope]) {
    const el = document.querySelector<HTMLElement>(sel);
    if (el && isTiktokElementVisible(el)) {
      fireRealClick(el);
      report.attempted = true;
      report.note = `selector:${sel}`;
      break;
    }
  }
  if (!report.attempted) {
    const tab = findClickableByText(SCOPE_TAB_LABELS[scope]);
    if (tab) {
      fireRealClick(tab);
      report.attempted = true;
      report.note = "text_fallback";
    }
  }
  if (report.attempted) await sleep(TAB_SETTLE_MS);
  else report.note = "scope_tab_not_found";
  report.page_url = location.href;
  return report;
}

async function navigateToFollowList(handle: string): Promise<ScopeNavReport> {
  const report: ScopeNavReport = {
    attempted: false,
    note: "",
    page_url: location.href,
  };
  if (!(await ensureOnOwnProfile(handle))) {
    report.note = "profile_entry_not_found";
    return report;
  }
  // The Following count on the profile header opens the following-list modal.
  const statSelectors = [
    '[data-e2e="following-count"]',
    '[data-e2e="following"]',
    '[data-e2e="user-following"]',
  ];
  await waitForRenderedState(() => statSelectors.some((selector) => {
    const entry = document.querySelector<HTMLElement>(selector);
    return Boolean(entry && isTiktokElementVisible(entry));
  }));
  for (const sel of statSelectors) {
    const el = document.querySelector<HTMLElement>(sel);
    if (el && isTiktokElementVisible(el)) {
      fireRealClick(el);
      report.attempted = true;
      report.note = `selector:${sel}`;
      break;
    }
  }
  if (!report.attempted) {
    const stat = findClickableByText(["Following", "关注"]);
    if (stat) {
      fireRealClick(stat);
      report.attempted = true;
      report.note = "text_fallback";
    }
  }
  if (report.attempted) await sleep(TAB_SETTLE_MS);
  else report.note = "following_entry_not_found";
  report.page_url = location.href;
  return report;
}

/**
 * Watch history has no stable desktop-web URL; it hangs off the avatar
 * or more menu when that account exposes a history entry. We only
 * click a positively identified "Watch history" entry — if neither the
 * menu nor the entry is found, the scope fails with an explicit reason
 * instead of fabricating an empty result.
 */
async function navigateToWatchHistory(): Promise<ScopeNavReport> {
  const report: ScopeNavReport = { attempted: false, note: "", page_url: location.href };
  const historyLabels = ["Watch history", "观看历史", "观看记录"];
  const activityLabels = ["Activity center", "活动中心"];
  const inSettings = (): boolean => new URL(location.href).pathname.replace(/\/$/, "") === "/setting";
  const settingsEntry = (): HTMLElement | null => {
    const entry = document.querySelector<HTMLElement>(
      '[data-e2e="see-more-popup"] [data-e2e="settings-entrance"]',
    );
    return entry && isTiktokElementVisible(entry) ? entry : null;
  };
  if (!inSettings()) {
    const menuEntry = (): HTMLElement | null => {
      for (const selector of ['[data-e2e="profile-icon"]', '[data-e2e="nav-more-menu"]']) {
        const element = document.querySelector<HTMLElement>(selector);
        if (element && isTiktokElementVisible(element)) return element;
      }
      return null;
    };
    await waitForRenderedState(() => Boolean(menuEntry()));
    const avatar = menuEntry();
    if (!avatar) {
      report.note = "history_menu_not_found";
      return report;
    }
    // nav-profile is a profile link, not an account-menu trigger. The live
    // Chinese more menu labels settings as 通用, so use its exact marker.
    fireRealClick(avatar);
    await waitForRenderedState(() => Boolean(settingsEntry() ||
      findClickableByText([...historyLabels, ...activityLabels, "Settings", "设置"])));
  }
  if (inSettings()) {
    await waitForRenderedState(() => Boolean(findClickableByText([
      ...historyLabels, ...activityLabels,
    ])), 20_000);
  }
  let entry = findClickableByText(historyLabels);
  for (const labels of [activityLabels, ["Settings", "设置"], activityLabels]) {
    if (entry) break;
    const isSettingsStep = labels[0] === "Settings";
    const parent = isSettingsStep
      ? (inSettings() ? null : settingsEntry() ?? findClickableByText(labels))
      : findClickableByText(labels);
    if (parent) fireRealClick(parent);
    if (parent) {
      // Also applies on full-navigation replay: never reopen settings when
      // already there, and wait for its content rather than clicking again.
      await waitForRenderedState(() => Boolean(findClickableByText(historyLabels) ||
        (isSettingsStep && findClickableByText(activityLabels))), 20_000);
    }
    entry = findClickableByText(historyLabels);
  }
  if (!entry) {
    report.note = "history_entry_not_found";
    report.page_url = location.href;
    return report;
  }
  fireRealClick(entry);
  report.attempted = true;
  report.note = "menu_entry";
  await sleep(NAV_SETTLE_MS);
  report.page_url = location.href;
  return report;
}

async function navigateToScope(
  scope: TiktokScope,
  handle: string,
): Promise<ScopeNavReport> {
  if (resolveTiktokScopeRoot(document, scope, handle) && (scope === "tt_history" || ownProfileVisible(handle))) {
    return { attempted: true, note: "existing_scope", page_url: location.href };
  }
  if (scope === "tt_history") return navigateToWatchHistory();
  if (scope === "tt_follow") return navigateToFollowList(handle);
  return navigateToProfileTab(scope, handle);
}

// ---------------------------------------------------------------------------
// End-of-feed detection
// ---------------------------------------------------------------------------

const END_OF_FEED_PHRASES: readonly string[] = [
  "you've reached the end",
  "no more videos",
  "no more content",
  "that's all",
  "没有更多了",
  "暂时没有更多",
  "已经到底",
];

function detectEndOfFeed(root: HTMLElement): string {
  const candidates = Array.from(
    root.querySelectorAll<HTMLElement>(
      'div, span, p, [class*="loading"], [class*="end"], [class*="footer"]',
    ),
  );
  for (const el of candidates) {
    const text = (el.textContent ?? "").trim().toLowerCase();
    if (!text || text.length > 40 || !isTiktokElementVisible(el)) continue;
    for (const phrase of END_OF_FEED_PHRASES) {
      if (text.includes(phrase)) return phrase;
    }
  }
  return "";
}

// ---------------------------------------------------------------------------
// Scope execution
// ---------------------------------------------------------------------------

async function runScope(msg: TiktokScopeExecuteMessage, record: ScopeExecutionRecord): Promise<TiktokScopeResult> {
  const sink = new TiktokItemSink({ maxItems: msg.max_items });
  sink.ingest(record.accepted_items ?? []);
  let navigationFailed = false;
  let navigationError = "";
  let navNote = "";
  let endOfFeedPhrase = "";
  let scrollRounds = 0;

  await waitForRenderedState(() =>
    resolveAccessState() === "signed_in" && Boolean(resolveViewerHandle()), 20_000);
  const accessState = resolveAccessState();
  let scopeRoot: HTMLElement | null = null;
  let viewerHandle = record.viewer_handle ?? "";
  let viewerSecUid = record.viewer_sec_uid ?? "";

  const harvestDom = async (): Promise<void> => {
    if (resolveAccessState() !== "signed_in" || resolveViewerHandle() !== viewerHandle) {
      throw new Error("account_session_changed");
    }
    if (viewerSecUid) {
      const raw = document.getElementById(TIKTOK_UNIVERSAL_DATA_ELEMENT_ID)?.textContent ?? "";
      const current = readTiktokUniversalData(raw);
      if (current.explicitLoggedOut || current.viewer.secUid !== viewerSecUid) throw new Error("account_session_changed");
    }
    if (msg.scope !== "tt_history" && !ownProfileVisible(viewerHandle)) {
      throw new Error("account_profile_changed");
    }
    scopeRoot = resolveTiktokScopeRoot(document, msg.scope, viewerHandle);
    if (!scopeRoot) throw new Error("scope_root_not_proven");
    const rows = extractTiktokItemsFromDocument(
      scopeRoot,
      msg.scope,
      location.origin,
      msg.max_items,
    );
    sink.ingest(rows.filter((row) => msg.scope !== "tt_follow" || row.author_handle.toLowerCase() !== viewerHandle.toLowerCase()));
    record.accepted_items = sink.snapshot();
    await persistScopeRecord(record);
  };

  try {
    if (accessState !== "signed_in" && sink.count() > 0) throw new Error("account_session_changed");
    if (accessState === "signed_in") {
      const currentHandle = resolveViewerHandle();
      const raw = document.getElementById(TIKTOK_UNIVERSAL_DATA_ELEMENT_ID)?.textContent ?? "";
      const identity = readTiktokUniversalData(raw);
      const currentSecUid = !identity.explicitLoggedOut && identity.viewer.handle === currentHandle ? identity.viewer.secUid : "";
      if (!currentHandle) throw new Error("viewer_identity_unknown");
      if ((record.viewer_handle && record.viewer_handle !== currentHandle) ||
          (record.viewer_sec_uid && record.viewer_sec_uid !== currentSecUid)) {
        throw new Error("account_session_changed");
      }
      viewerHandle = currentHandle;
      viewerSecUid = currentSecUid;
      record.viewer_handle = viewerHandle;
      record.viewer_sec_uid = viewerSecUid;
      await persistScopeRecord(record);
      const nav = await navigateToScope(msg.scope, viewerHandle);
      navNote = nav.note;
      navigationFailed = !nav.attempted;
      navigationError = nav.note;
    }

    if (!navigationFailed && accessState === "signed_in") {
      await waitForRenderedState(() => Boolean(resolveTiktokScopeRoot(document, msg.scope, viewerHandle)),
        msg.scope === "tt_follow" ? 20_000 : 60_000);
      await harvestDom();
      const maxRounds = normalizeTiktokScrollRounds(msg.max_scroll_rounds);
      let stagnantRounds = 0;
      for (let round = 0; round < maxRounds; round += 1) {
        const beforeCount = sink.count();
        const scrollTarget = scopeRoot ? pickTiktokScrollTarget(scopeRoot) : null;
        if (scrollTarget) {
          scrollTarget.scrollTop = scrollTarget.scrollHeight;
        }
        if (!scrollTarget) window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
        await sleep(SCROLL_DELAY_MS);
        await harvestDom();
        const afterCount = sink.count();
        scrollRounds = round + 1;
        endOfFeedPhrase = scopeRoot ? detectEndOfFeed(scopeRoot) : "";
        stagnantRounds = afterCount > beforeCount ? 0 : stagnantRounds + 1;
        if (endOfFeedPhrase) break;
        if (
          !ttShouldContinueScroll({
            currentCount: afterCount,
            maxItems: msg.max_items,
            round: round + 1,
            maxScrollRounds: maxRounds,
            stagnantRounds,
            maxStagnantScrollRounds: msg.max_stagnant_scroll_rounds,
          })
        ) {
          break;
        }
      }
      await harvestDom();
    }

    const items = sink.snapshot();
    const explicitEmpty =
      accessState === "signed_in" && items.length === 0 && scopeRoot
        ? detectTiktokEmptyState(scopeRoot, msg.scope) !== ""
        : false;
    const completion = classifyTiktokScopeCompletion({
      accessState,
      itemCount: items.length,
      explicitEmpty,
      // Hitting the item cap is a planned stop but NOT a terminal end —
      // contract scope_caps: capped scopes remain incomplete (degraded),
      // never silently "ok".
      endOfFeed: endOfFeedPhrase !== "" && items.length < msg.max_items,
      navigationFailed,
      navigationError,
    });
    return {
      task_id: msg.task_id,
      scope: msg.scope,
      items,
      scope_count: items.length,
      viewer_handle: viewerHandle,
      ...(viewerSecUid ? { viewer_sec_uid: viewerSecUid } : {}),
      status: completion.status,
      ...(completion.error ? { error: completion.error } : {}),
      debug: {
        access_state: accessState,
        navigation: navNote,
        scroll_rounds: scrollRounds,
        end_of_feed: endOfFeedPhrase,
        explicit_empty: explicitEmpty,
        page_url: location.href,
      },
    };
  } catch (err) {
    const items = sink.snapshot();
    return {
      task_id: msg.task_id,
      scope: msg.scope,
      items,
      scope_count: items.length,
      viewer_handle: viewerHandle,
      ...(viewerSecUid ? { viewer_sec_uid: viewerSecUid } : {}),
      status: items.length ? "degraded" : "failed",
      error: String(err),
      debug: {
        access_state: accessState,
        navigation: navNote,
        scroll_rounds: scrollRounds,
        end_of_feed: endOfFeedPhrase,
        page_url: location.href,
      },
    };
  }
}

const SCOPE_POINTER_KEY = "openbiliclaw_tiktok_scope_pointer_v1";
const SCOPE_RECORD_KEY = "openbiliclaw_tiktok_content_scope_v1";
const SCOPE_RECORD_TTL_MS = 5 * 60_000;
interface ScopeExecutionRecord {
  token: string;
  expires_at: number;
  message: TiktokScopeExecuteMessage;
  viewer_handle?: string;
  viewer_sec_uid?: string;
  result?: TiktokScopeResult;
  accepted_items?: TiktokScopeResult["items"];
}
let activeScopeKey: string | null = null;
let acceptedScopeKey: string | null = null;
let memoryRecord: ScopeExecutionRecord | null = null;

async function readScopeRecord(): Promise<ScopeExecutionRecord | null> {
  const value: unknown = (await chrome.storage.local.get(SCOPE_RECORD_KEY))[SCOPE_RECORD_KEY] ?? memoryRecord;
  if (!value || typeof value !== "object") return null;
  const record = value as ScopeExecutionRecord;
  if (typeof record.token !== "string" || !isValidTiktokScopeExecuteMessage(record.message) ||
      !Number.isFinite(record.expires_at)) return null;
  if (record.expires_at < Date.now()) {
    await chrome.storage.local.remove(SCOPE_RECORD_KEY);
    memoryRecord = null;
    return null;
  }
  return record;
}
async function persistScopeRecord(record: ScopeExecutionRecord): Promise<void> {
  await chrome.storage.local.set({ [SCOPE_RECORD_KEY]: record });
  memoryRecord = record;
}
async function deliverScopeResult(result: TiktokScopeResult): Promise<void> {
  const response = await chrome.runtime.sendMessage({ action: "TIKTOK_SCOPE_RESULT", data: result }).catch(() => null);
  if (response?.ok !== true) return;
  // The dispatcher has durably accepted these rows. Avoid retaining profile
  // data after ACK, and never delete a newer scope's storage slot.
  try {
    const current = (await chrome.storage.local.get(SCOPE_RECORD_KEY))[SCOPE_RECORD_KEY] as ScopeExecutionRecord | undefined;
    if (current?.message.task_id === result.task_id && current.message.scope === result.scope) {
      await chrome.storage.local.remove(SCOPE_RECORD_KEY);
    }
  } catch { /* Expiring replay is safe if cleanup fails. */ }
}

/** Keep canonical rows in extension storage; page storage contains only an opaque resume pointer. */
export async function executeTiktokScopeOnce(
  data: TiktokScopeExecuteMessage,
  onAccepted?: (accepted: boolean) => void,
): Promise<void> {
  const key = `${data.task_id}:${data.scope}`;
  if (activeScopeKey !== null) {
    onAccepted?.(activeScopeKey === key && acceptedScopeKey === key);
    return;
  }
  activeScopeKey = key;
  acceptedScopeKey = null;
  let record: ScopeExecutionRecord | null = null;
  try {
    const previous = await readScopeRecord();
    const matches = previous?.message.task_id === data.task_id && previous.message.scope === data.scope;
    if (matches && previous.result) {
      onAccepted?.(true);
      activeScopeKey = null;
      await deliverScopeResult(previous.result);
      return;
    }
    record = matches ? previous : {
      token: crypto.randomUUID(), expires_at: Date.now() + SCOPE_RECORD_TTL_MS, message: data,
    };
    await persistScopeRecord(record);
    sessionStorage.setItem(SCOPE_POINTER_KEY, record.token);
    acceptedScopeKey = key;
    // A resolved sendMessage without an explicit ACK can mean the old
    // document ignored the scope or unloaded before persisting its pointer.
    // Acknowledge only when a new document can safely resume this execution.
    onAccepted?.(true);
    const result = await runScope(data, record);
    record.result = result;
    await persistScopeRecord(record);
    try { sessionStorage.removeItem(SCOPE_POINTER_KEY); } catch { /* Result replay remains read-only. */ }
    // Collection is durably finished. The dispatcher may start the next scope
    // before replying to this result (including same-document navigation).
    // Holding the collection lock during that ACK silently drops the handoff.
    activeScopeKey = null;
    await deliverScopeResult(result);
  } catch (error) {
    if (acceptedScopeKey !== key) onAccepted?.(false);
    // Failure to persist the resume record must stop before any navigation.
    // The background dispatcher still owns durable reporting of this failure.
    const items = record?.accepted_items ?? [];
    activeScopeKey = null;
    await deliverScopeResult({ task_id: data.task_id, scope: data.scope,
      items, scope_count: items.length, status: items.length ? "degraded" : "failed",
      ...(record?.viewer_handle ? { viewer_handle: record.viewer_handle } : {}),
      ...(record?.viewer_sec_uid ? { viewer_sec_uid: record.viewer_sec_uid } : {}),
      error: `scope_persistence_failed: ${String(error)}` });
  } finally {
    if (activeScopeKey === key) activeScopeKey = null;
    if (acceptedScopeKey === key) acceptedScopeKey = null;
  }
}

async function resumeTiktokScopeAfterNavigation(): Promise<void> {
  const record = await readScopeRecord();
  if (!record || record.result) return;
  let token: string | null = null;
  try { token = sessionStorage.getItem(SCOPE_POINTER_KEY); } catch { return; }
  if (token === record.token) await executeTiktokScopeOnce(record.message);
}

export function registerTiktokScopeExecutor(): void {
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.onMessage) return;
  chrome.runtime.onMessage.addListener((message: Record<string, unknown>, _sender, sendResponse) => {
    if (message.action !== "TIKTOK_SCOPE_EXECUTE" || !isValidTiktokScopeExecuteMessage(message.data)) return false;
    const data = message.data;
    void executeTiktokScopeOnce(data, accepted => {
      try { sendResponse({ accepted, task_id: data.task_id, scope: data.scope }); }
      catch { /* Navigation may close the port; the persisted pointer survives. */ }
    });
    return true;
  });
  void resumeTiktokScopeAfterNavigation().catch(() => { /* Dispatcher timeout reports unavailable recovery storage. */ });
}

if (typeof chrome !== "undefined" && chrome.runtime) {
  registerTiktokScopeExecutor();
  if (chrome.runtime.onMessage) {
    installNativeSaveExecutor("tiktok", saveTiktok, verifyTiktok);
  }
  // eslint-disable-next-line no-console
  console.debug("[OpenBiliClaw] tiktok content script registered (isolated world)");
}
