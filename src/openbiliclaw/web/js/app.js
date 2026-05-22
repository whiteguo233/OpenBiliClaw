/**
 * Mobile web SPA entry — shell rendering, routing, health/stream wiring,
 * cross-view navigation. Views render their own tab content.
 */

import { fetchHealth, checkHealth } from "./api.js";
import { createStreamClient } from "./stream.js";
import { state, patchState, subscribe } from "./state.js";
import { initRecommendView, onStreamEvent as recStreamEvent } from "./views/recommend.js";
import { initProfileView, onStreamEvent as profileStreamEvent } from "./views/profile.js";
import { initChatView, onStreamEvent as chatStreamEvent, toggleMessages } from "./views/chat.js";

// ── DOM refs ─────────────────────────────────────────────────
const $app = document.getElementById("app");
const $statusBar = document.getElementById("status-bar");
const $tabBar = document.getElementById("tab-bar");

// ── Status Bar ───────────────────────────────────────────────
function renderStatusBar() {
  $statusBar.innerHTML = "";

  const title = document.createElement("span");
  title.className = "status-title";
  title.textContent = "OpenBiliClaw";

  const right = document.createElement("div");
  right.className = "status-right";

  // Connection status dot + text
  const dot = document.createElement("span");
  dot.className = `status-dot ${state.online ? "online" : "offline"}`;
  right.appendChild(dot);

  const statusText = document.createElement("span");
  statusText.style.cssText = "font-size:11px;color:var(--text-muted);margin-right:4px";
  if (state.degraded) {
    statusText.textContent = "降级模式";
    statusText.style.color = "var(--danger)";
  } else {
    statusText.textContent = state.online ? "在线" : "离线";
  }
  right.appendChild(statusText);

  // Messages bell + badge
  const bell = document.createElement("button");
  bell.className = "badge-btn";
  bell.innerHTML = `<span style="font-size:18px">&#128276;</span>`;
  const unread = state.messages.notifications.length + state.messages.delights.length;
  const badge = document.createElement("span");
  badge.className = "badge-count";
  badge.dataset.count = unread;
  badge.textContent = unread > 0 ? (unread > 99 ? "99+" : String(unread)) : "";
  bell.appendChild(badge);
  bell.addEventListener("click", () => toggleMessages());
  right.appendChild(bell);

  $statusBar.appendChild(title);
  $statusBar.appendChild(right);

  // Degraded banner
  let existing = document.getElementById("degraded-banner");
  if (state.degraded) {
    if (!existing) {
      existing = document.createElement("div");
      existing.id = "degraded-banner";
      existing.style.cssText =
        "background:var(--warning-soft);color:#d97706;font-size:12px;padding:6px 16px;text-align:center";
      $statusBar.after(existing);
    }
    existing.textContent = state.degradedReason || "后端处于降级模式，部分功能不可用";
  } else if (existing) {
    existing.remove();
  }
}

// ── Tab Bar ──────────────────────────────────────────────────
const TABS = [
  { id: "recommend", icon: "\u2728", label: "\u63A8\u8350" },
  { id: "profile", icon: "\u{1F9E0}", label: "\u753B\u50CF" },
  { id: "chat", icon: "\u{1F4AC}", label: "\u5BF9\u8BDD" },
];

function renderTabBar() {
  $tabBar.innerHTML = "";
  $tabBar.setAttribute("role", "tablist");
  for (const tab of TABS) {
    const isActive = state.activeTab === tab.id;
    const el = document.createElement("button");
    el.className = `tab-item${isActive ? " active" : ""}`;
    el.setAttribute("role", "tab");
    el.setAttribute("aria-selected", String(isActive));
    el.tabIndex = isActive ? 0 : -1;
    el.innerHTML = `<span class="tab-icon" aria-hidden="true">${tab.icon}</span><span class="tab-label">${tab.label}</span>`;
    el.addEventListener("click", () => navigateToTab(tab.id));
    el.addEventListener("keydown", (e) => {
      let target = null;
      if (e.key === "ArrowRight") target = TABS[(TABS.indexOf(tab) + 1) % TABS.length];
      else if (e.key === "ArrowLeft") target = TABS[(TABS.indexOf(tab) - 1 + TABS.length) % TABS.length];
      if (target) { e.preventDefault(); navigateToTab(target.id); $tabBar.querySelector(`[aria-selected="true"]`)?.focus(); }
    });
    $tabBar.appendChild(el);
  }
}

// ── Views ────────────────────────────────────────────────────
const views = {};

function ensureView(id) {
  if (views[id]) return views[id];
  const el = document.createElement("div");
  el.className = "view";
  el.id = `view-${id}`;
  $app.appendChild(el);
  views[id] = el;
  return el;
}

function initActiveView() {
  const id = state.activeTab;
  if (id === "recommend") initRecommendView(views.recommend);
  else if (id === "profile") initProfileView(views.profile);
  else if (id === "chat") initChatView(views.chat);
}

/**
 * Navigate to a tab. Exported for cross-view use (e.g. delight "聊一聊" → chat).
 */
export function navigateToTab(id) {
  if (!TABS.find((t) => t.id === id)) return;
  location.hash = `#/${id}`;
  patchState({ activeTab: id });
  for (const [key, el] of Object.entries(views)) {
    el.classList.toggle("active", key === id);
  }
  renderTabBar();
  initActiveView();
}

// ── Hash Router ──────────────────────────────────────────────
function readHash() {
  const hash = location.hash.replace("#/", "").replace("#", "");
  return TABS.find((t) => t.id === hash) ? hash : "recommend";
}

// ── WebSocket ────────────────────────────────────────────────
const stream = createStreamClient({
  onConnect() {
    patchState({ online: true });
  },
  onDisconnect() {
    patchState({ online: false });
  },
  onEvent(payload) {
    patchState({ runtimeEvent: payload });
    recStreamEvent(payload);
    profileStreamEvent(payload);
    chatStreamEvent(payload);
  },
});

// ── State subscription — re-render shell on relevant changes ─
subscribe((_state, changed) => {
  if ("online" in changed || "degraded" in changed || "degradedReason" in changed || "messages" in changed) {
    renderStatusBar();
  }
  if ("activeTab" in changed) {
    renderTabBar();
  }
});

// ── Badge update hook (backward compat for chat.js) ──────────
export function setUnreadCount(n) {
  // Chat view updates messages directly in state now, but keep this
  // as a convenience bridge during transition.
  renderStatusBar();
}

// ── Init ─────────────────────────────────────────────────────
(async function init() {
  for (const tab of TABS) ensureView(tab.id);

  renderStatusBar();
  renderTabBar();

  // Health check with degraded detection
  try {
    const health = await fetchHealth();
    patchState({
      online: true,
      degraded: health.status === "degraded",
      degradedReason: health.reason || "",
    });
  } catch {
    const alive = await checkHealth();
    patchState({ online: alive });
  }

  stream.connect();

  window.addEventListener("hashchange", () => navigateToTab(readHash()));
  navigateToTab(readHash());
})();
