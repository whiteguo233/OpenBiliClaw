import assert from "node:assert/strict";
import test from "node:test";
import { executeTiktokScopeOnce, registerTiktokScopeExecutor } from "../src/content/tiktok.ts";
import type { TiktokScopeExecuteMessage, TiktokScopeResult } from "../src/content/tt/task-executor.ts";

const recordKey = "openbiliclaw_tiktok_content_scope_v1";
const pointerKey = "openbiliclaw_tiktok_scope_pointer_v1";

class ElementFixture {
  parentElement: ElementFixture | null = null;
  textContent = "";
  clicks = 0;
  attrs: Record<string, string>;
  constructor(attrs: Record<string, string> = {}) { this.attrs = attrs; }
  getAttribute(key: string) { return this.attrs[key] ?? null; }
  hasAttribute(key: string) { return key in this.attrs; }
  contains(other: ElementFixture) { return other === this; }
  closest() { return this; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  dispatchEvent() { this.clicks++; if ("aria-selected" in this.attrs) this.attrs["aria-selected"] = "true"; return true; }
}

function environment(options: { onResult?: (result: TiktokScopeResult) => Promise<void>; route?: string; appContextIdentity?: boolean; navAbsent?: boolean; positiveMarker?: boolean; explicitLogout?: boolean; conflictingNav?: boolean; historyMenuAfterTicks?: number; scopeRoot?: boolean; scrollFails?: boolean; storageFails?: boolean; switchViewer?: boolean; switchSecUid?: boolean; readyAfterTicks?: number; profileTitleAfterTicks?: number; displayNameTitle?: boolean; tabAfterTicks?: number; panelAfterTicks?: number } = {}) {
  let ticks = 0;
  const saved: Record<string, PropertyDescriptor | undefined> = {};
  const values: Record<string, unknown> = {};
  const session = new Map<string, string>();
  const results: TiktokScopeResult[] = [];
  const nav = new ElementFixture({ href: options.conflictingNav ? "/@other.user" : "/@me.user" });
  const moreMenu = new ElementFixture();
  const historyEntry = new ElementFixture(); historyEntry.textContent = "Watch history";
  const title = new ElementFixture(); title.textContent = options.displayNameTitle ? "My display name" : "me.user";
  const subtitle = new ElementFixture(); subtitle.textContent = "@me.user";
  const tab = new ElementFixture({ "aria-selected": "false", "aria-controls": "liked-panel" });
  const video = new ElementFixture({ href: "/@creator/video/7300000000000000001", title: "Cats" });
  const panel = new ElementFixture({ role: "tabpanel" });
  panel.querySelectorAll = ((selector: string) => selector.includes('/video/') ? [video] : []) as typeof panel.querySelectorAll;
  const identity = { __DEFAULT_SCOPE__: { "webapp.user-context": {
    isLoggedIn: true, user: { uniqueId: "me.user", secUid: "SEC-me" },
  } } };
  const doc = {
    body: { textContent: "" },
    getElementById(id: string) {
      if (ticks < (options.readyAfterTicks ?? 0)) return null;
      if (id === "__UNIVERSAL_DATA_FOR_REHYDRATION__") return { textContent: JSON.stringify(options.appContextIdentity ? {
        __DEFAULT_SCOPE__: { "webapp.app-context": { user: identity.__DEFAULT_SCOPE__["webapp.user-context"].user },
          ...(options.explicitLogout ? { "webapp.user-context": { isLoggedIn: false } } : {}) },
      } : identity) };
      return options.scopeRoot === false || ticks < (options.panelAfterTicks ?? 0) ? null : panel;
    },
    querySelector(selector: string) {
      if (ticks < (options.readyAfterTicks ?? 0)) return null;
      if (selector.includes('data-e2e="nav-profile"')) return options.navAbsent ? null : nav;
      if (selector === '[data-e2e="profile-icon"]' && options.positiveMarker) return new ElementFixture();
      if (selector === '[data-e2e="nav-more-menu"]' && options.historyMenuAfterTicks !== undefined) {
        return ticks >= options.historyMenuAfterTicks ? moreMenu : null;
      }
      if (selector === '[data-e2e="user-title"]') return ticks < (options.profileTitleAfterTicks ?? 0) ? null : title;
      if (selector === '[data-e2e="user-subtitle"]') return options.displayNameTitle ? subtitle : null;
      if (selector === '[data-e2e="liked-tab"]' || selector === '[data-e2e="favorites-tab"]') return ticks < (options.tabAfterTicks ?? 0) ? null : tab;
      return null;
    },
    querySelectorAll(selector: string) {
      if (selector.includes('[role="menuitem"]') && moreMenu.clicks > 0) return [historyEntry];
      if (selector === '[data-e2e="liked-tab"]' || selector === '[data-e2e="favorites-tab"]') return ticks < (options.tabAfterTicks ?? 0) ? [] : [tab];
      return [];
    },
  };
  const originalTimer = globalThis.setTimeout;
  const globals = {
    document: doc,
    location: { href: options.route ?? "https://www.tiktok.com/@me.user", origin: "https://www.tiktok.com" },
    window: { innerHeight: 800, scrollBy() {
      if (options.scrollFails) throw new Error("scroll_interrupted");
      if (options.switchViewer) identity.__DEFAULT_SCOPE__["webapp.user-context"].user.uniqueId = "other.user";
      if (options.switchSecUid) identity.__DEFAULT_SCOPE__["webapp.user-context"].user.secUid = "other-sec-uid";
    } },
    MouseEvent: class {},
    sessionStorage: { getItem: (key: string) => session.get(key) ?? null, setItem: (key: string, value: string) => session.set(key, value), removeItem: (key: string) => session.delete(key) },
    chrome: {
      storage: { local: {
        get: async (key: string) => ({ [key]: values[key] }),
        set: async (next: Record<string, unknown>) => {
          if (options.storageFails) throw new Error("storage_unavailable");
          Object.assign(values, structuredClone(next));
        },
        remove: async (key: string) => { delete values[key]; },
      } },
      runtime: { onMessage: { addListener() {} }, sendMessage: async (message: { data: TiktokScopeResult }) => { results.push(message.data); await options.onResult?.(message.data); } },
    },
    setTimeout: ((callback: (...args: unknown[]) => void) => originalTimer(() => { ticks++; callback(); }, 0)),
  };
  for (const [key, value] of Object.entries(globals)) {
    saved[key] = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  return { values, session, results, tab, nav, moreMenu, historyEntry, restore() {
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  } };
}

function message(id: string, rounds = 0): TiktokScopeExecuteMessage {
  return { task_id: id, scope: "tt_like", max_items: 10, max_scroll_rounds: rounds, max_stagnant_scroll_rounds: 1 };
}

test("actual content execution uses own active panel and replays duplicates without clicking", async () => {
  const env = environment();
  try {
    await executeTiktokScopeOnce(message("own-panel"));
    assert.equal(env.results[0]?.items.length, 1);
    assert.equal(env.results[0]?.viewer_handle, "me.user");
    assert.equal(env.results[0]?.viewer_sec_uid, "SEC-me");
    assert.equal(env.results[0]?.status, "degraded");
    await executeTiktokScopeOnce(message("own-panel"));
    assert.equal(env.tab.clicks, 1);
    assert.deepEqual(env.results[0], env.results[1]);
    assert.equal(env.session.has(pointerKey), false);
  } finally { env.restore(); }
});

test("another profile and unproven panel cannot generate account events", async () => {
  for (const [id, options, error] of [
    ["other-profile", { route: "https://www.tiktok.com/@someone.else" }, "profile_entry_not_found"],
    ["missing-panel", { scopeRoot: false }, "scope_root_not_proven"],
  ] as const) {
    const env = environment(options);
    try {
      await executeTiktokScopeOnce(message(id));
      assert.equal(env.results[0]?.status, "failed");
      assert.deepEqual(env.results[0]?.items, []);
      assert.ok(env.results[0]?.error?.includes(error));
    } finally { env.restore(); }
  }
});

test("interrupted scrolling retains accepted rows under the original viewer", async () => {
  const env = environment({ scrollFails: true });
  try {
    await executeTiktokScopeOnce(message("interrupted", 1));
    assert.equal(env.results[0]?.status, "degraded");
    assert.equal(env.results[0]?.items.length, 1);
    assert.match(env.results[0]?.error ?? "", /scroll_interrupted/);
  } finally { env.restore(); }
});

test("full navigation resumes only an extension-owned unexpired matching pointer", async () => {
  const env = environment();
  try {
    env.values[recordKey] = { token: "opaque-navigation", expires_at: Date.now() + 30_000, message: message("resume") };
    env.session.set(pointerKey, "opaque-navigation");
    registerTiktokScopeExecutor();
    for (let attempt = 0; attempt < 30 && env.results.length === 0; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    assert.equal(env.results[0]?.task_id, "resume");
    assert.equal(env.results[0]?.items.length, 1);
  } finally { env.restore(); }
});


test("durable storage failure stops before profile or scope clicks", async () => {
  const env = environment({ storageFails: true });
  try {
    await executeTiktokScopeOnce(message("storage-fail"));
    assert.equal(env.tab.clicks, 0);
    assert.equal(env.results[0]?.status, "failed");
    assert.deepEqual(env.results[0]?.items, []);
    assert.match(env.results[0]?.error ?? "", /scope_persistence_failed/);
  } finally { env.restore(); }
});

test("switching viewer during collection retains only pre-switch rows and identity", async () => {
  const env = environment({ switchViewer: true });
  try {
    await executeTiktokScopeOnce(message("account-switch", 1));
    assert.equal(env.results[0]?.status, "degraded");
    assert.equal(env.results[0]?.viewer_handle, "me.user");
    assert.equal(env.results[0]?.items.length, 1);
    assert.match(env.results[0]?.error ?? "", /account_session_changed/);
  } finally { env.restore(); }
});

test("resumed scope retains checkpoint rows even when the viewer was logged out", async () => {
  const env = environment({ route: "https://www.tiktok.com/login" });
  try {
    env.values[recordKey] = { token: "resume-partial", expires_at: Date.now() + 30_000,
      message: message("resume-partial"), viewer_handle: "me.user", viewer_sec_uid: "SEC-me",
      accepted_items: [{ scope: "tt_like", video_id: "7300000000000000001", url: "https://www.tiktok.com/@creator/video/7300000000000000001", title: "Cats", author_handle: "creator", author: "creator", cover_url: "" }],
    };
    await executeTiktokScopeOnce(message("resume-partial"));
    assert.equal(env.results[0]?.status, "degraded");
    assert.equal(env.results[0]?.items.length, 1);
    assert.equal(env.results[0]?.viewer_handle, "me.user");
    assert.equal(env.tab.clicks, 0);
  } finally { env.restore(); }
});


test("stable handle cannot conceal a changed session secUid", async () => {
  const env = environment({ switchSecUid: true });
  try {
    await executeTiktokScopeOnce(message("secuid-switch", 1));
    assert.equal(env.results[0]?.status, "degraded");
    assert.equal(env.results[0]?.viewer_sec_uid, "SEC-me");
    assert.equal(env.results[0]?.items.length, 1);
    assert.match(env.results[0]?.error ?? "", /account_session_changed/);
  } finally { env.restore(); }
});


test("completed documents wait for hydrated viewer and own-profile heading", async () => {
  for (const options of [{ readyAfterTicks: 2 }, { profileTitleAfterTicks: 3 }, { displayNameTitle: true }, { panelAfterTicks: 5 }, { tabAfterTicks: 4 }]) {
    const env = environment(options);
    try {
      await executeTiktokScopeOnce(message("hydration-" + JSON.stringify(options)));
      assert.equal(env.results[0]?.items.length, 1);
      assert.equal(env.results[0]?.viewer_handle, "me.user");
      assert.equal(env.nav.clicks, 0, "an owned profile awaiting hydration must not reload itself");
    } finally { env.restore(); }
  }
});


test("history waits for the real menu entry and never clicks the profile navigation link", async () => {
  const env = environment({ historyMenuAfterTicks: 3 });
  try {
    await executeTiktokScopeOnce({ ...message("history-menu"), scope: "tt_history" });
    assert.equal(env.moreMenu.clicks, 1);
    assert.equal(env.historyEntry.clicks, 1);
    assert.equal(env.nav.clicks, 0);
    assert.equal(env.results[0]?.error, "Error: scope_root_not_proven");
    assert.deepEqual(env.results[0]?.items, []);
  } finally { env.restore(); }
});


test("app-context identity survives missing nav only with positive login and no contradiction", async () => {
  for (const [id, options, count] of [
    ["session-with-marker", { appContextIdentity: true, navAbsent: true, positiveMarker: true }, 1],
    ["session-without-marker", { appContextIdentity: true, navAbsent: true }, 0],
    ["explicit-logout", { appContextIdentity: true, explicitLogout: true }, 0],
    ["conflicting-nav", { appContextIdentity: true, conflictingNav: true }, 0],
  ] as const) {
    const env = environment(options);
    try {
      await executeTiktokScopeOnce(message(id));
      assert.equal(env.results[0]?.items.length, count);
      if (count) assert.equal(env.results[0]?.viewer_sec_uid, "SEC-me");
      else assert.equal(env.results[0]?.status, "failed");
    } finally { env.restore(); }
  }
});

test("scope content can mount after the selected profile tab without becoming a false failure", async () => {
  const env = environment({ panelAfterTicks: 60 });
  try {
    await executeTiktokScopeOnce(message("late-profile-content"));
    assert.equal(env.results[0]?.items.length, 1);
    assert.equal(env.results[0]?.status, "degraded");
  } finally { env.restore(); }
});


test("next scope arriving before prior result ACK is collected in the same document", async () => {
  const env = environment({ onResult: async (result) => {
    if (result.scope === "tt_like") {
      await executeTiktokScopeOnce({ ...message("handoff-before-ack"), scope: "tt_collect" });
    }
  } });
  try {
    await executeTiktokScopeOnce(message("handoff-before-ack"));
    assert.deepEqual(env.results.map(result => result.scope), ["tt_like", "tt_collect"]);
    assert.deepEqual(env.results.map(result => result.items.length), [1, 1]);
    assert.equal((env.values[recordKey] as { message: TiktokScopeExecuteMessage }).message.scope, "tt_collect");
  } finally { env.restore(); }
});

test("scope acceptance is acknowledged only after its durable resume pointer exists", async () => {
  const env = environment();
  let accepted = false;
  let duplicateAccepted = false;
  try {
    await executeTiktokScopeOnce(message("durable-accept"), (ok) => {
      accepted = ok;
      const record = env.values[recordKey] as { token: string };
      assert.equal(env.session.get(pointerKey), record.token);
      assert.equal(env.tab.clicks, 0);
      void executeTiktokScopeOnce(message("durable-accept"), value => { duplicateAccepted = value; });
    });
    assert.equal(accepted, true);
    assert.equal(duplicateAccepted, true);
    assert.equal(env.tab.clicks, 1);
  } finally { env.restore(); }
});

test("failed scope persistence never acknowledges execution", async () => {
  const env = environment({ storageFails: true });
  let accepted: boolean | undefined;
  try {
    await executeTiktokScopeOnce(message("no-ack-storage"), value => { accepted = value; });
    assert.equal(accepted, false);
    assert.equal(env.tab.clicks, 0);
  } finally { env.restore(); }
});
