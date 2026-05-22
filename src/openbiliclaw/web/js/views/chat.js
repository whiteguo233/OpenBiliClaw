/**
 * Chat view — message history, input with placeholder carousel,
 * AI thinking state, messages overlay (probe + delight notifications),
 * contextual chat entry from delight/probe.
 */

import {
  startChatTurn,
  fetchChatTurn,
  fetchChatTurns,
  fetchProfileSummary,
  fetchActivityFeed,
  fetchPendingNotifications,
  ackNotification,
  fetchDelightBatch,
  respondToDelight,
  markDelightSent,
  respondToProbe,
} from "../api.js";
import { setUnreadCount, navigateToTab } from "../app.js";
import {
  normalizeChatTurn,
  normalizeProfileSummary,
  normalizeActivityFeed,
  normalizeDelightCandidate,
  getDelightActionState,
  getDelightMessageActions,
  getProbeMessageActions,
  getMobileChatSession,
  getCoverImageAttrs,
  getSourceLabel,
  buildContentUrl,
} from "../view-models.js";
import { state, patchState } from "../state.js";

let $root = null;
let loaded = false;
let turns = [];
let sending = false;
let pendingTurnId = null;
let pollTimer = null;
let userScrolledUp = false;

// Messages overlay state
let overlayOpen = false;
let notifications = [];
let delightMsgs = [];

// Placeholder carousel
const PLACEHOLDERS = [
  "\u6700\u8FD1\u6709\u4EC0\u4E48\u60F3\u804A\u7684\uFF1F",
  "\u5BF9\u54EA\u6761\u63A8\u8350\u6709\u60F3\u6CD5\uFF1F",
  "\u60F3\u63A2\u7D22\u4EC0\u4E48\u65B0\u9886\u57DF\uFF1F",
  "\u89C9\u5F97\u753B\u50CF\u51C6\u4E0D\u51C6\uFF1F",
  "\u6709\u4EC0\u4E48\u4E0D\u60F3\u518D\u770B\u5230\u7684\uFF1F",
];
let placeholderIdx = 0;
let placeholderTimer = null;
let inputFocused = false;

function chatSession(scope = "chat") {
  return getMobileChatSession(scope);
}

// ── Escape helper ────────────────────────────────────────────
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

// ── Render Chat ──────────────────────────────────────────────
function render() {
  if (!$root) return;
  $root.innerHTML = "";

  const shell = document.createElement("div");
  shell.className = "chat-shell";

  // Messages area
  const messages = document.createElement("div");
  messages.className = "chat-messages";
  messages.id = "chat-messages";

  if (turns.length === 0 && !sending) {
    messages.innerHTML = `<div class="empty-state"><div class="empty-state-icon">\u{1F4AC}</div><div class="empty-state-text">\u548C AI \u804A\u804A\u4F60\u7684\u5174\u8DA3\u548C\u60F3\u6CD5</div></div>`;
  }

  for (const turn of turns) {
    if (turn.message) {
      const userBubble = document.createElement("div");
      userBubble.className = "chat-bubble user";
      userBubble.textContent = turn.message;
      messages.appendChild(userBubble);
    }
    if (turn.response) {
      const aiBubble = document.createElement("div");
      aiBubble.className = "chat-bubble assistant";
      aiBubble.textContent = turn.response;
      messages.appendChild(aiBubble);
    } else if (turn.status === "pending" || turn.status === "processing") {
      const thinking = document.createElement("div");
      thinking.className = "chat-bubble thinking";
      thinking.innerHTML = `<div class="spinner" style="width:16px;height:16px;display:inline-block;vertical-align:middle;margin-right:6px"></div>\u601D\u8003\u4E2D\u2026`;
      messages.appendChild(thinking);
    } else if (turn.status === "error" || turn.status === "failed") {
      const errBubble = document.createElement("div");
      errBubble.className = "chat-bubble error";
      errBubble.textContent = turn.error || "\u56DE\u590D\u5931\u8D25";
      const retryBtn = document.createElement("button");
      retryBtn.className = "chat-retry-btn";
      retryBtn.textContent = "\u91CD\u8BD5";
      retryBtn.addEventListener("click", () => retryTurn(turn));
      errBubble.appendChild(retryBtn);
      messages.appendChild(errBubble);
    }
  }

  // Scroll tracking
  messages.addEventListener("scroll", () => {
    userScrolledUp = messages.scrollTop + messages.clientHeight < messages.scrollHeight - 40;
  });

  shell.appendChild(messages);

  // Input row
  const inputRow = document.createElement("div");
  inputRow.className = "chat-input-row";

  const textarea = document.createElement("textarea");
  textarea.className = "chat-input";
  textarea.id = "chat-input";
  textarea.placeholder = PLACEHOLDERS[placeholderIdx];
  textarea.rows = 2;
  textarea.addEventListener("input", autoGrow);
  textarea.addEventListener("focus", () => { inputFocused = true; });
  textarea.addEventListener("blur", () => { inputFocused = false; });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      handleSend();
    }
  });

  // Pre-fill from contextual chat context
  if (state.pendingChatContext && !sending) {
    const ctx = state.pendingChatContext;
    textarea.value = `\u5173\u4E8E\u300C${ctx.subjectTitle || ctx.subjectId}\u300D\uFF0C\u6211\u60F3\u804A\u804A`;
    patchState({ pendingChatContext: null });
  }

  const sendBtn = document.createElement("button");
  sendBtn.className = "chat-send-btn";
  sendBtn.id = "chat-send";
  sendBtn.innerHTML = "\u{1F4E8}";
  sendBtn.disabled = sending;
  sendBtn.addEventListener("click", handleSend);

  inputRow.appendChild(textarea);
  inputRow.appendChild(sendBtn);
  shell.appendChild(inputRow);

  $root.appendChild(shell);

  // Auto-scroll to bottom (unless user scrolled up)
  if (!userScrolledUp) {
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
  }

  // Start placeholder carousel
  startPlaceholderCarousel();

  // Render overlay if open
  renderOverlay();
}

function autoGrow(e) {
  const el = e.target;
  el.style.height = "auto";
  el.style.height = Math.min(Math.max(el.scrollHeight, 60), 112) + "px";
}

function startPlaceholderCarousel() {
  if (placeholderTimer) clearInterval(placeholderTimer);
  placeholderTimer = setInterval(() => {
    if (inputFocused) return;
    placeholderIdx = (placeholderIdx + 1) % PLACEHOLDERS.length;
    const input = document.getElementById("chat-input");
    if (input && !input.value) {
      input.placeholder = PLACEHOLDERS[placeholderIdx];
    }
  }, 4000);
}

// ── Send ─────────────────────────────────────────────────────
async function handleSend() {
  const input = document.getElementById("chat-input");
  const text = input?.value?.trim();
  if (!text || sending) return;

  sending = true;
  const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  turns.push({ turn_id: turnId, message: text, response: null, status: "pending" });
  userScrolledUp = false;
  render();

  try {
    await startChatTurn({ turnId, ...chatSession(), message: text });
    pendingTurnId = turnId;
    pollForResponse();
  } catch {
    const t = turns.find((t) => t.turn_id === turnId);
    if (t) { t.status = "error"; t.error = "\u53D1\u9001\u5931\u8D25"; }
    sending = false;
    render();
  }
}

async function retryTurn(failedTurn) {
  if (sending) return;
  failedTurn.status = "pending";
  failedTurn.error = "";
  sending = true;
  render();

  try {
    await startChatTurn({
      turnId: failedTurn.turn_id,
      ...chatSession(failedTurn.scope || "chat"),
      message: failedTurn.message,
      subjectId: failedTurn.subject_id || "",
      subjectTitle: failedTurn.subject_title || "",
    });
    pendingTurnId = failedTurn.turn_id;
    pollForResponse();
  } catch {
    failedTurn.status = "error";
    failedTurn.error = "\u91CD\u8BD5\u5931\u8D25";
    sending = false;
    render();
  }
}

function pollForResponse() {
  if (!pendingTurnId) return;
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const turn = normalizeChatTurn(await fetchChatTurn(pendingTurnId));
      const idx = turns.findIndex((t) => t.turn_id === pendingTurnId);
      if (idx >= 0) turns[idx] = turn;

      if (turn.status === "done" || turn.status === "completed" || turn.response) {
        pendingTurnId = null;
        sending = false;
        userScrolledUp = false;
        render();
        refreshAfterChatTurn();
      } else if (turn.status === "error" || turn.status === "failed") {
        pendingTurnId = null;
        sending = false;
        render();
      } else {
        render();
        pollForResponse();
      }
    } catch {
      pollForResponse();
    }
  }, 1500);
}

// ── Messages Overlay ─────────────────────────────────────────
function renderOverlay() {
  let overlay = document.querySelector(".messages-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "messages-overlay";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) toggleMessages();
    });
    document.body.appendChild(overlay);
  }
  overlay.classList.toggle("open", overlayOpen);

  if (!overlayOpen) {
    overlay.innerHTML = "";
    return;
  }

  const panel = document.createElement("div");
  panel.className = "messages-panel";

  // Header
  const header = document.createElement("div");
  header.className = "messages-header";
  header.innerHTML = `<span class="messages-title">\u6D88\u606F</span>`;
  const closeBtn = document.createElement("button");
  closeBtn.className = "messages-close";
  closeBtn.textContent = "\u2715";
  closeBtn.addEventListener("click", toggleMessages);
  header.appendChild(closeBtn);
  panel.appendChild(header);

  // Probe notifications
  for (const n of notifications) {
    const card = document.createElement("div");
    card.className = "message-card";
    card.innerHTML = `
      <div class="message-card-type">\u{1F50D} \u5174\u8DA3\u63A2\u6D4B</div>
      <div class="message-card-title">${esc(n.domain || n.title || "")}</div>
      <div class="message-card-body">${esc(n.description || n.reason || n.message || "")}</div>
      <div class="message-card-actions">
        ${getProbeMessageActions().map((item) => `
          <button class="message-action-btn ${item.primary ? "primary" : "secondary"}" data-probe="${esc(item.action)}" data-domain="${esc(n.domain || "")}">${esc(item.label)}</button>
        `).join("")}
      </div>`;
    panel.appendChild(card);
  }

  // Delight notifications
  for (const d of delightMsgs) {
    const nd = normalizeDelightCandidate(d);
    const cover = getCoverImageAttrs(nd.cover_url);
    const card = document.createElement("div");
    card.className = "message-card";
    card.innerHTML = `
      <div class="message-card-type">\u2728 \u60CA\u559C\u63A8\u8350</div>
      ${cover ? `<div class="message-cover-frame"><img src="${esc(cover.src)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-error');this.remove()"></div>` : `<div class="message-cover-frame is-error"></div>`}
      <div class="message-card-title">${esc(nd.title)}</div>
      <div class="message-card-body">${esc(nd.delight_hook || nd.delight_reason)}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
        <span class="card-source" data-source="${nd.source_platform}">${esc(getSourceLabel(nd.source_platform))}</span>
      </div>
      <div class="message-card-actions">
        ${getDelightMessageActions().map((item) => `
          <button class="message-action-btn ${item.primary ? "primary" : "secondary"}" data-delight="${esc(item.action)}" data-bvid="${esc(nd.bvid)}" data-title="${esc(nd.title)}">${esc(item.label)}</button>
        `).join("")}
      </div>`;
    panel.appendChild(card);
  }

  if (notifications.length === 0 && delightMsgs.length === 0) {
    panel.innerHTML += `<div class="empty-state" style="padding:24px"><div class="empty-state-text">\u6CA1\u6709\u65B0\u6D88\u606F</div></div>`;
  }

  overlay.innerHTML = "";
  overlay.appendChild(panel);

  // Bind probe actions
  for (const btn of panel.querySelectorAll("[data-probe]")) {
    btn.addEventListener("click", async () => {
      const domain = btn.dataset.domain;
      const action = btn.dataset.probe;
      if (action === "chat") {
        toggleMessages();
        startContextualChat({ scope: "probe", subjectId: domain, subjectTitle: domain });
        return;
      }
      btn.disabled = true;
      try {
        await respondToProbe(domain, action);
        notifications = notifications.filter((n) => (n.domain || n.title) !== domain);
        updateBadgeCount();
        renderOverlay();
      } catch {
        btn.disabled = false;
      }
    });
  }

  // Bind delight actions
  for (const btn of panel.querySelectorAll("[data-delight]")) {
    btn.addEventListener("click", async () => {
      const bvid = btn.dataset.bvid;
      const action = btn.dataset.delight;
      const title = btn.dataset.title || "";

      if (action === "chat") {
        toggleMessages();
        startContextualChat({ scope: "delight", subjectId: bvid, subjectTitle: title });
        return;
      }

      const { apiResponse, permanent } = getDelightActionState(action);
      btn.disabled = true;

      if (apiResponse) {
        try { await respondToDelight(bvid, apiResponse, title); } catch { /* best-effort */ }
      }
      if (permanent) {
        markDelightSent(bvid).catch(() => {});
      }

      delightMsgs = delightMsgs.filter((d) => d.bvid !== bvid);
      updateBadgeCount();
      renderOverlay();

      if (action === "view") {
        const item = normalizeDelightCandidate({ bvid, title });
        const url = buildContentUrl(item);
        if (url) window.open(url, "_blank");
      }
    });
  }
}

function updateBadgeCount() {
  const msgs = { notifications: [...notifications], delights: [...delightMsgs] };
  patchState({ messages: msgs });
  setUnreadCount(notifications.length + delightMsgs.length);
}

// ── Load ─────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const data = await fetchChatTurns({ ...chatSession(), limit: 50 });
    turns = Array.isArray(data?.items || data?.turns)
      ? (data.items || data.turns).map(normalizeChatTurn)
      : [];
    const last = turns[turns.length - 1];
    if (last && (last.status === "pending" || last.status === "processing")) {
      pendingTurnId = last.turn_id;
      sending = true;
      pollForResponse();
    }
  } catch { /* ignore */ }
  render();
}

async function refreshAfterChatTurn() {
  try {
    const [profileResult, activityResult] = await Promise.allSettled([
      fetchProfileSummary({ limit: 5 }),
      fetchActivityFeed({ limit: 5 }),
    ]);
    const next = {};
    if (profileResult.status === "fulfilled") {
      next.profile = normalizeProfileSummary(profileResult.value);
    }
    if (activityResult.status === "fulfilled") {
      next.activityFeed = normalizeActivityFeed(activityResult.value);
    }
    if (Object.keys(next).length > 0) patchState(next);
  } catch { /* best-effort */ }
}

async function loadNotifications() {
  try {
    const [notifData, delightData] = await Promise.all([
      fetchPendingNotifications().catch(() => ({})),
      fetchDelightBatch(10).catch(() => []),
    ]);
    const raw = notifData?.items || notifData?.pending || (notifData?.domain ? [notifData] : []);
    notifications = Array.isArray(raw) ? raw : [];
    delightMsgs = delightData;
    updateBadgeCount();
  } catch { /* ignore */ }
}

// ── Public API ───────────────────────────────────────────────
export function initChatView(root) {
  $root = root;
  if (!loaded) {
    loaded = true;
    loadNotifications();
  }
  loadHistory();
}

export function toggleMessages() {
  overlayOpen = !overlayOpen;
  if (overlayOpen) loadNotifications();
  renderOverlay();
}

export function updateBadge() {
  updateBadgeCount();
}

export function onStreamEvent(payload) {
  const type = payload?.type || payload?.event;
  if (type === "interest.probe") {
    const item = payload.data || payload;
    if (item.domain) {
      notifications.push(item);
      updateBadgeCount();
    }
  } else if (type === "delight.candidate") {
    const item = payload.data || payload;
    if (item.title && item.bvid) {
      delightMsgs.push(item);
      updateBadgeCount();
    }
  } else if (type === "delight.liked" || type === "delight.disliked") {
    // Another client dismissed this delight — remove from messages overlay
    const bvid = (payload.data || payload)?.bvid;
    if (bvid) {
      const before = delightMsgs.length;
      delightMsgs = delightMsgs.filter((d) => d.bvid !== bvid);
      if (delightMsgs.length !== before) {
        updateBadgeCount();
        if (overlayOpen) renderOverlay();
      }
    }
  }
}

/**
 * Start a contextual chat from delight "聊一聊" or probe "多聊聊".
 */
export async function startContextualChat({ scope, subjectId, subjectTitle, message }) {
  patchState({ pendingChatContext: { scope, subjectId, subjectTitle } });
  navigateToTab("chat");

  if (!message) return; // render() will pre-fill composer text

  const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  turns.push({
    turn_id: turnId, message, response: null, status: "pending",
    scope, subject_id: subjectId, subject_title: subjectTitle,
  });
  sending = true;
  userScrolledUp = false;
  render();

  try {
    await startChatTurn({ turnId, ...chatSession(scope), subjectId, subjectTitle, message });
    pendingTurnId = turnId;
    pollForResponse();
  } catch {
    const t = turns.find((t) => t.turn_id === turnId);
    if (t) { t.status = "error"; t.error = "\u53D1\u9001\u5931\u8D25"; }
    sending = false;
    render();
  }
}
