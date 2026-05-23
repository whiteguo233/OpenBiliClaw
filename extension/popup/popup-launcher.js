// popup-launcher.js (ES module)
// Self-contained popup panel for Safari — matches the side panel UI (推荐/个人/聊一聊)
// without requiring "open in new tab".
// Imports API functions from popup-api.js for data fetching.

import {
  checkBackendStatus,
  fetchRecommendations,
  refreshRecommendations,
  reshuffleRecommendations,
  fetchProfileSummary,
  fetchRuntimeStatus,
  startChatTurn,
  fetchChatTurns,
  submitFeedback,
} from "./popup-api.js";
import { buildFeedbackPayload } from "./popup-helpers.js";

/* ── Protocol constants ─────────────────────────────────────────- */
const proto = window.OpenBiliClawLauncher || {};
const PING_TYPE = proto.PING_LAUNCHER_TO_BG || "openbiliclaw/popup-launcher/ping";
const QUERY_PENDING_TYPE = proto.QUERY_LAUNCHER_PENDING_STATUS || "openbiliclaw/popup-launcher/query-pending";
const QUERY_WATCH_LATER_TYPE = proto.QUERY_LAUNCHER_WATCH_LATER_COUNT || "openbiliclaw/popup-launcher/query-watch-later-count";

/* ── State ─────────────────────────────────────────────────────── */
const state = {
  activeTab: "recommend",
  online: false,
  recommendations: [],
  delights: [],
  delightIndex: 0,
};

/* ── DOM refs ──────────────────────────────────────────────────── */
const $ = (id) => document.getElementById(id);
const el = {
  statusBadge: $("statusBadge"),
  statusDot: $("statusDot"),
  statusLabel: $("statusLabel"),
  bgPill: $("bg-pill"),
  bgStatus: $("bg-status"),
  version: $("version"),
  watchLater: $("watch-later-count"),
  repostCallout: $("repost-callout"),
  repostKnown: $("repost-known"),
  repostPending: $("repost-pending"),
  repostJump: $("repost-jump"),
  tabRecommend: $("tabRecommend"),
  tabProfile: $("tabProfile"),
  tabChat: $("tabChat"),
  viewRecommend: $("viewRecommend"),
  viewProfile: $("viewProfile"),
  viewChat: $("viewChat"),
  recList: $("recommendationList"),
  recLoading: $("recLoading"),
  recEmpty: $("recEmpty"),
  recEmptyTitle: $("recEmptyTitle"),
  recEmptyText: $("recEmptyText"),
  recRefreshBtn: $("recRefreshBtn"),
  refreshBtn: $("refreshBtn"),
  profileCard: $("profileCard"),
  profileEmpty: $("profileEmpty"),
  profileLoading: $("profileLoading"),
  chatMessages: $("chatMessages"),
  chatInput: $("chatInput"),
  chatSend: $("chatSend"),
  chatStatus: $("chatStatus"),
};

/* ── Utilities ─────────────────────────────────────────────────── */
function setText(id, text) {
  const node = typeof id === "string" ? $(id) : id;
  if (node) node.textContent = text;
}

function setPill(pillId, statusId, kind, label) {
  const pill = typeof pillId === "string" ? $(pillId) : pillId;
  if (pill) pill.className = "pill " + kind;
  setText(statusId, label);
}

function setBadge(tone, label) {
  if (el.statusBadge) el.statusBadge.dataset.tone = tone;
  if (el.statusDot) el.statusDot.className = "status-dot" + (tone === "offline" ? " offline" : "");
  if (el.statusLabel) el.statusLabel.textContent = label;
}

function show(el, show) {
  if (el) el.hidden = !show;
}

/* ── Tab switching ─────────────────────────────────────────────── */
const tabs = [
  { button: el.tabRecommend, view: el.viewRecommend, name: "recommend" },
  { button: el.tabProfile, view: el.viewProfile, name: "profile" },
  { button: el.tabChat, view: el.viewChat, name: "chat" },
];

function switchTab(name) {
  state.activeTab = name;
  for (const t of tabs) {
    const isActive = t.name === name;
    if (t.button) {
      t.button.classList.toggle("is-active", isActive);
      t.button.setAttribute("aria-selected", String(isActive));
    }
    if (t.view) t.view.hidden = !isActive;
  }
  // Lazy load profile/chat
  if (name === "profile") loadProfile();
  if (name === "chat") ensureChat();
}

tabs.forEach((t) => {
  if (t.button) {
    t.button.addEventListener("click", () => switchTab(t.name));
  }
});

/* ── Backend ping (same as original) ───────────────────────────── */
function pingBackend() {
  const api = typeof chrome !== "undefined" ? chrome : null;
  if (!api || !api.runtime || typeof api.runtime.sendMessage !== "function") {
    setPill(el.bgPill, el.bgStatus, "err", "不可用");
    return;
  }
  let settled = false;
  const timer = setTimeout(() => {
    if (settled) return;
    settled = true;
    setPill(el.bgPill, el.bgStatus, "ok", "运行中");
    setBadge("online", "在线");
    state.online = true;
  }, 250);

  try {
    api.runtime.sendMessage({ type: PING_TYPE }, () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const _ = api.runtime.lastError;
      setPill(el.bgPill, el.bgStatus, "ok", "运行中");
      setBadge("online", "在线");
      state.online = true;
    });
  } catch (_) {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    setPill(el.bgPill, el.bgStatus, "err", "未连接");
    setBadge("offline", "未连接");
  }
}

/* ── Version (same as original) ────────────────────────────────── */
function reportVersion() {
  try {
    const api = typeof chrome !== "undefined" ? chrome : null;
    if (api && api.runtime && typeof api.runtime.getManifest === "function") {
      const manifest = api.runtime.getManifest();
      if (manifest && manifest.version) {
        setText(el.version, "v" + manifest.version);
        return;
      }
    }
  } catch (_) { /* ignore */ }
  setText(el.version, "—");
}

/* ── Repost callout (same as original) ─────────────────────────── */
function queryRepostStatus() {
  const api = typeof chrome !== "undefined" ? chrome : null;
  if (!api || !api.runtime || typeof api.runtime.sendMessage !== "function") return;
  try {
    api.runtime.sendMessage({ type: QUERY_PENDING_TYPE }, (reply) => {
      const _ = api.runtime && api.runtime.lastError;
      if (!reply || typeof reply !== "object") return;
      renderRepost({
        isRepost: reply.isRepost === true,
        isPending: reply.isPending === true,
        ytUrl: typeof reply.ytUrl === "string" ? reply.ytUrl : "",
      });
    });
  } catch (_) { /* SW unreachable */ }
}

function renderRepost(status) {
  const { repostCallout, repostKnown, repostPending, repostJump } = el;
  if (!repostCallout || !repostKnown || !repostPending) return;
  repostCallout.hidden = true;
  repostKnown.hidden = true;
  repostPending.hidden = true;
  if (!status.isRepost) return;
  if (status.ytUrl) {
    repostCallout.hidden = false;
    repostKnown.hidden = false;
    if (repostJump) {
      repostJump.onclick = () => {
        const api = typeof chrome !== "undefined" ? chrome : null;
        if (api && api.tabs && typeof api.tabs.update === "function") {
          api.tabs.update({ url: status.ytUrl }, () => {
            const _ = api.runtime && api.runtime.lastError;
            if (typeof window.close === "function") window.close();
          });
        } else {
          window.open(status.ytUrl, "_blank");
        }
      };
    }
  } else if (status.isPending) {
    repostCallout.hidden = false;
    repostPending.hidden = false;
  }
}

/* ── Watch-later count (same as original) ──────────────────────── */
function queryWatchLaterCount() {
  const api = typeof chrome !== "undefined" ? chrome : null;
  if (!api || !api.runtime || typeof api.runtime.sendMessage !== "function") {
    setText(el.watchLater, "—");
    return;
  }
  try {
    api.runtime.sendMessage({ type: QUERY_WATCH_LATER_TYPE }, (reply) => {
      const _ = api.runtime && api.runtime.lastError;
      if (!reply || typeof reply !== "object" || reply.ok !== true) {
        setText(el.watchLater, "—");
        return;
      }
      setText(el.watchLater, String(typeof reply.total === "number" ? reply.total : 0));
    });
  } catch (_) {
    setText(el.watchLater, "—");
  }
}

/* ── Recommendations ───────────────────────────────────────────── */
async function loadRecommendations() {
  show(el.recLoading, true);
  show(el.recEmpty, false);
  show(el.recList, false);
  el.recList.innerHTML = "";

  try {
    const items = await fetchRecommendations();
    state.recommendations = items;
    if (!items || items.length === 0) {
      show(el.recLoading, false);
      show(el.recEmpty, true);
      return;
    }
    renderRecommendations(items);
  } catch (err) {
    show(el.recLoading, false);
    if (err.name === "AbortError") return;
    show(el.recEmpty, true);
    setText(el.recEmptyTitle, "获取推荐失败");
    setText(el.recEmptyText, err.message || "后端未响应");
  }
}

async function handleRefresh() {
  show(el.recLoading, true);
  show(el.recEmpty, false);
  el.recList.innerHTML = "";
  try {
    const result = await reshuffleRecommendations();
    const items = result?.items || [];
    state.recommendations = items;
    if (items.length === 0) {
      show(el.recLoading, false);
      show(el.recEmpty, true);
      return;
    }
    renderRecommendations(items);
  } catch (err) {
    // fallback: try refresh
    try {
      await refreshRecommendations();
      const items = await fetchRecommendations();
      state.recommendations = items;
      if (items.length === 0) {
        show(el.recLoading, false);
        show(el.recEmpty, true);
        return;
      }
      renderRecommendations(items);
    } catch (e2) {
      show(el.recLoading, false);
      show(el.recEmpty, true);
      setText(el.recEmptyTitle, "刷新推荐失败");
      setText(el.recEmptyText, e2.message || "后端未响应");
    }
  }
}

function renderRecommendations(items) {
  show(el.recLoading, false);
  show(el.recEmpty, false);
  show(el.recList, true);
  el.recList.innerHTML = items.map((item, i) => {
    const src = item.source || "bilibili";
    const title = item.title || "无标题";
    const desc = item.description || item.reason || "";
    const coverUrl = item.cover_url || item.thumbnail || "";
    const url = item.url || item.bvid || "#";
    const duration = item.duration || "";
    const author = item.author || item.uploader || "";

    return `<div class="rec-card" data-index="${i}" data-url="${url}">
      <div class="rec-cover">
        ${coverUrl ? `<img src="${coverUrl}" alt="" loading="lazy" onerror="this.style.display='none'">` : "📹"}
      </div>
      <div class="rec-body">
        <div class="rec-title">${escapeHtml(title)}</div>
        <div class="rec-meta">
          <span class="rec-source" data-source="${src}">${src}</span>
          ${author ? `<span>${escapeHtml(author)}</span>` : ""}
          ${duration ? `<span>${duration}</span>` : ""}
        </div>
        ${desc ? `<div class="rec-desc">${escapeHtml(desc)}</div>` : ""}
        <div class="rec-actions">
          <button class="rec-action-btn open-video" data-url="${url}">打开</button>
          <button class="rec-action-btn like-btn" data-index="${i}">👍 感兴趣</button>
        </div>
      </div>
    </div>`;
  }).join("");

  // Attach click handlers
  el.recList.querySelectorAll(".rec-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("button")) return; // let buttons handle their own clicks
      const url = card.dataset.url;
      if (url && url !== "#") openUrl(url);
    });
  });
  el.recList.querySelectorAll(".open-video").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openUrl(btn.dataset.url);
    });
  });
  el.recList.querySelectorAll(".like-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      // Was a UI-only stub for months — clicking would change the
      // button text and disable it, but no feedback ever reached the
      // backend. Wire it up to submitFeedback (same path as the full
      // popup's 多来点 action). Optimistic UI: mark as 'submitting'
      // immediately, swap to '已标记' on success, restore + show
      // hint on failure.
      const index = Number(btn.dataset.index);
      const item = Number.isFinite(index) ? state.recommendations[index] : null;
      if (!item || !item.id) {
        // No recommendation_id — can't submit. Treat like the old stub
        // so the UI still gives some feedback, but mark visibly that
        // it's local-only.
        btn.textContent = "⚠️ 暂时无法记录";
        btn.disabled = true;
        return;
      }
      const originalLabel = btn.textContent;
      btn.disabled = true;
      btn.textContent = "⏳ 记录中…";
      try {
        await submitFeedback(buildFeedbackPayload(item.id, "like"));
        btn.textContent = "✅ 已记下";
        // keep disabled — feedback is one-shot per card
      } catch (err) {
        btn.textContent = originalLabel;
        btn.disabled = false;
        // Surface error in the empty-text slot if available; otherwise
        // just log. We don't have a status pill in the launcher card.
        console.warn("[launcher] feedback submission failed:", err);
      }
    });
  });
}

function openUrl(url) {
  const api = typeof chrome !== "undefined" ? chrome : null;
  // Try navigation directly
  if (api && api.tabs && typeof api.tabs.create === "function") {
    api.tabs.create({ url, active: true });
    return;
  }
  window.open(url, "_blank");
}

function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

/* ── Profile ───────────────────────────────────────────────────── */
let profileLoaded = false;

async function loadProfile() {
  if (profileLoaded) return;
  profileLoaded = true;
  show(el.profileLoading, true);
  show(el.profileEmpty, false);
  show(el.profileCard, false);

  try {
    const profile = await fetchProfileSummary();
    if (!profile || !profile.portrait) {
      show(el.profileLoading, false);
      show(el.profileEmpty, true);
      return;
    }
    renderProfile(profile);
  } catch (err) {
    show(el.profileLoading, false);
    if (err.name === "AbortError") return;
    show(el.profileEmpty, true);
  }
}

function renderProfile(profile) {
  show(el.profileLoading, false);
  show(el.profileEmpty, false);
  show(el.profileCard, true);

  const portrait = profile.portrait || "还没攒够数据，暂时说不出你是什么样的人。";
  const needs = profile.needs || profile.core_needs || "";
  const traits = profile.traits || [];
  const memory = profile.recent_memory || "";
  const likes = profile.likes || [];
  const stats = profile.stats || {};

  el.profileCard.innerHTML = `
    <h3>这会儿的你</h3>
    <div class="profile-portrait">${escapeHtml(portrait)}</div>
    ${needs ? `<div class="memory-card"><div class="memory-label">深层需求</div>${escapeHtml(needs)}</div>` : ""}
    ${memory ? `<div class="memory-card"><div class="memory-label">近期记忆</div>${escapeHtml(memory)}</div>` : ""}
    <div class="profile-stat-row">
      <div class="profile-stat">
        <div class="num">${stats.interactions || stats.items_discovered || "—"}</div>
        <div class="label">互动</div>
      </div>
      <div class="profile-stat">
        <div class="num">${stats.interests || stats.interests_mapped || "—"}</div>
        <div class="label">兴趣</div>
      </div>
    </div>
  `;
}

/* ── Chat ──────────────────────────────────────────────────────── */
let chatInitialized = false;

function ensureChat() {
  if (chatInitialized) return;
  chatInitialized = true;
  // Show initial prompt
  addChatMessage("system", "想聊什么？说说你最近在看什么内容、有什么想法。");
}

function addChatMessage(role, text) {
  const msg = document.createElement("div");
  msg.className = "chat-msg " + role;
  msg.textContent = text;
  el.chatMessages.appendChild(msg);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

async function sendChatMessage() {
  const text = el.chatInput.value.trim();
  if (!text) return;
  el.chatInput.value = "";
  addChatMessage("user", text);
  el.chatSend.disabled = true;
  show(el.chatStatus, true);
  setText(el.chatStatus, "正在思考…");

  try {
    const reply = await startChatTurn({ message: text, session: "popup" });
    el.chatSend.disabled = false;
    show(el.chatStatus, false);
    if (reply && reply.response) {
      addChatMessage("agent", reply.response);
    } else if (reply && reply.message) {
      addChatMessage("agent", reply.message);
    } else {
      addChatMessage("agent", "嗯，我记下了。还有别的想聊的吗？");
    }
  } catch (err) {
    el.chatSend.disabled = false;
    show(el.chatStatus, false);
    addChatMessage("agent", "抱歉，我没能理解。可以再说一遍吗？");
  }
}

el.chatSend.addEventListener("click", sendChatMessage);
el.chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  }
});

/* ── Init ──────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  reportVersion();
  pingBackend();
  queryRepostStatus();
  queryWatchLaterCount();
  loadRecommendations();

  // Refresh button in Recommend tab
  if (el.refreshBtn) el.refreshBtn.addEventListener("click", handleRefresh);
  if (el.recRefreshBtn) el.recRefreshBtn.addEventListener("click", handleRefresh);
});
