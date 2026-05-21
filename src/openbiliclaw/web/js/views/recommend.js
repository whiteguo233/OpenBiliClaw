/**
 * Recommend view — activity strip, semantic pool status, delight tray,
 * recommendation cards with feedback, pull-to-refresh.
 */

import {
  fetchRecommendations,
  reshuffleRecommendations,
  appendRecommendations,
  fetchRuntimeStatus,
  fetchDelightBatch,
  fetchActivityFeed,
  respondToDelight,
  markDelightSent,
  reportClick,
  submitFeedback,
} from "../api.js";
import { state, patchState } from "../state.js";
import {
  getCoverImageAttrs,
  normalizeRecommendation,
  normalizeRuntimeStatus,
  mergeRuntimeStatusEvent,
  getPoolStatusSummary,
  getReadyRecommendationHint,
  normalizeActivityFeed,
  getActivityCardState,
  normalizeDelightCandidate,
  getDelightUiState,
  getDelightActionState,
  buildFeedbackPayload,
  validateCommentInput,
  getCommentSubmitUiState,
  buildContentUrl,
  normalizeSourcePlatform,
  getSourceLabel,
  formatRelativeTimestamp,
} from "../view-models.js";

let $root = null;
let loaded = false;
let loading = false;
let feedbackSheet = null; // { itemId, note, submitState }

// ── Escape helper ────────────────────────────────────────────
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

// ── Render ────────────────────────────────────────────────────
function render() {
  if (!$root) return;
  $root.innerHTML = "";

  // Pull indicator
  const pull = document.createElement("div");
  pull.className = "pull-indicator";
  pull.id = "pull-indicator";
  pull.textContent = "\u2193 \u4E0B\u62C9\u5237\u65B0";
  $root.appendChild(pull);

  // Activity strip
  renderActivityStrip();

  // Pool semantic summary
  renderPoolSummary();

  // Delight tray
  renderDelightTray();

  // Action row
  renderActionRow();

  // Recommendation cards
  const recs = state.recommendations;
  if (recs.length === 0 && !loading) {
    const hint = getReadyRecommendationHint(state.runtimeStatus);
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div class="empty-state-icon">\u{1F30A}</div><div class="empty-state-text">${esc(hint.message)}</div>`;
    $root.appendChild(empty);
  }

  for (const item of recs) {
    $root.appendChild(renderCard(item));
  }

  if (loading) {
    const sp = document.createElement("div");
    sp.style.padding = "20px";
    sp.innerHTML = `<div class="spinner"></div>`;
    $root.appendChild(sp);
  }

  // Feedback bottom sheet
  renderFeedbackSheet();
}

// ── Activity Strip ───────────────────────────────────────────
function renderActivityStrip() {
  const actState = getActivityCardState({
    feed: state.activityFeed,
    runtimeEvent: state.runtimeEvent,
    expanded: state.activityExpanded,
  });

  const strip = document.createElement("div");
  strip.className = `activity-strip${actState.expanded ? " expanded" : ""}`;

  // Collapsed: one-line summary
  const summary = document.createElement("div");
  summary.className = "activity-strip-summary";
  summary.innerHTML = `<span>${esc(actState.line1)}</span>`;
  const toggle = document.createElement("button");
  toggle.className = "activity-strip-toggle";
  toggle.textContent = actState.expanded ? "\u25B2" : "\u25BC";
  toggle.addEventListener("click", () => {
    patchState({ activityExpanded: !state.activityExpanded });
    render();
  });
  summary.appendChild(toggle);
  strip.appendChild(summary);

  // Expanded: history items
  if (actState.expanded && actState.items.length > 0) {
    const list = document.createElement("div");
    list.className = "activity-strip-list";
    for (const item of actState.items) {
      const row = document.createElement("div");
      row.className = "activity-item";
      row.innerHTML = `<span class="activity-item-time">${esc(formatRelativeTimestamp(item.created_at))}</span> ${esc(item.summary)}`;
      list.appendChild(row);
    }
    if (actState.has_more) {
      const more = document.createElement("button");
      more.className = "load-more-btn";
      more.textContent = "\u52A0\u8F7D\u66F4\u591A";
      more.addEventListener("click", loadMoreActivity);
      list.appendChild(more);
    }
    strip.appendChild(list);
  }

  $root.appendChild(strip);
}

async function loadMoreActivity() {
  const feed = normalizeActivityFeed(state.activityFeed);
  if (!feed.next_cursor) return;
  try {
    const next = await fetchActivityFeed({ limit: 10, before: feed.next_cursor });
    const merged = normalizeActivityFeed(next);
    patchState({
      activityFeed: {
        ...next,
        items: [...(state.activityFeed?.items || []), ...(merged.items || [])],
      },
    });
    render();
  } catch { /* ignore */ }
}

// ── Pool Summary ─────────────────────────────────────────────
function renderPoolSummary() {
  const poolSummary = getPoolStatusSummary(state.runtimeStatus);
  if (!poolSummary) return;

  const grid = document.createElement("div");
  grid.className = "pool-status";
  const chips = [
    { value: poolSummary.available, label: "\u6C60\u5B58\u91CF" },
    { value: poolSummary.replenished, label: "\u8FD1\u671F\u8865\u5145" },
    { value: poolSummary.topics, label: "\u5F53\u524D\u8BDD\u9898" },
  ];
  for (const c of chips) {
    const chip = document.createElement("div");
    chip.className = "pool-chip";
    chip.innerHTML = `<div class="pool-chip-value" style="font-size:13px">${esc(String(c.value))}</div><div class="pool-chip-label">${esc(c.label)}</div>`;
    grid.appendChild(chip);
  }
  $root.appendChild(grid);
}

// ── Delight Tray ─────────────────────────────────────────────
function renderDelightTray() {
  const delights = state.activeDelights;
  if (delights.length === 0) return;

  const idx = state.delightCurrentIndex;
  const d = normalizeDelightCandidate(delights[idx] || delights[0]);
  const uiState = getDelightUiState(d);
  if (!uiState.visible) return;

  const tray = document.createElement("div");
  tray.className = "delight-tray";

  const cover = getCoverImageAttrs(d.cover_url);
  const coverHtml = cover
    ? `<img class="card-cover" src="${esc(cover.src)}" alt="" loading="lazy" referrerpolicy="${cover.referrerPolicy}" onerror="this.remove()" style="border-radius:10px;margin-bottom:8px">`
    : "";

  tray.innerHTML = `
    <span class="delight-tag">\u2728 \u60CA\u559C\u63A8\u8350</span>
    ${coverHtml}
    <div class="delight-title">${esc(d.title)}</div>
    <div class="delight-hook">${esc(d.delight_hook || d.delight_reason)}</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
      <span class="card-source" data-source="${d.source_platform}">${esc(getSourceLabel(d.source_platform))}</span>
      ${uiState.score_label ? ` · ${esc(uiState.score_label)}` : ""}
    </div>`;

  if (uiState.handled) {
    tray.innerHTML += `<div style="margin-top:8px;font-size:12px;color:var(--${uiState.response_tone === "success" ? "success" : "text-muted"})">${esc(uiState.response_message)}</div>`;
  } else {
    // Action buttons
    const actions = document.createElement("div");
    actions.className = "delight-actions";
    const btns = [
      { label: "\u770B\u770B", action: "view" },
      { label: "\u4E0D\u611F\u5174\u8DA3", action: "reject" },
      { label: "\u804A\u4E00\u804A", action: "chat" },
      { label: "\u7A0D\u540E", action: "later" },
    ];
    for (const b of btns) {
      const btn = document.createElement("button");
      btn.className = `btn ${b.action === "view" ? "btn-brand" : "btn-outline"}`;
      btn.style.cssText = "flex:1;font-size:12px;padding:8px 4px;min-height:44px";
      btn.textContent = b.label;
      btn.addEventListener("click", () => handleDelightAction(d, b.action));
      actions.appendChild(btn);
    }
    tray.appendChild(actions);
  }

  // Nav
  if (delights.length > 1) {
    const nav = document.createElement("div");
    nav.className = "delight-nav";
    nav.innerHTML = `
      <button class="delight-nav-btn" id="delight-prev" ${idx <= 0 ? "disabled" : ""}>\u2039</button>
      <span class="delight-counter">${idx + 1} / ${delights.length}</span>
      <button class="delight-nav-btn" id="delight-next" ${idx >= delights.length - 1 ? "disabled" : ""}>\u203A</button>`;
    nav.querySelector("#delight-prev")?.addEventListener("click", () => {
      if (idx > 0) { patchState({ delightCurrentIndex: idx - 1 }); render(); }
    });
    nav.querySelector("#delight-next")?.addEventListener("click", () => {
      if (idx < delights.length - 1) { patchState({ delightCurrentIndex: idx + 1 }); render(); }
    });
    tray.appendChild(nav);
  }

  $root.appendChild(tray);
}

async function handleDelightAction(d, action) {
  const { apiResponse, uiState, permanent } = getDelightActionState(action);

  if (action === "chat") {
    const { startContextualChat } = await import("./chat.js");
    startContextualChat({
      scope: "delight",
      subjectId: d.bvid,
      subjectTitle: d.title,
    });
    return;
  }

  if (action === "later") {
    // Advance without responding
    const next = state.delightCurrentIndex + 1;
    if (next < state.activeDelights.length) {
      patchState({ delightCurrentIndex: next });
    }
    render();
    return;
  }

  // "view" or "reject" — call API with correct token
  if (apiResponse) {
    try {
      await respondToDelight(d.bvid, apiResponse, d.title);
    } catch { /* best-effort */ }
  }
  if (permanent) {
    markDelightSent(d.bvid).catch(() => {});
  }

  // Update local delight state for brief result display
  const updated = state.activeDelights.map((item) =>
    (item.bvid || normalizeDelightCandidate(item).bvid) === d.bvid
      ? { ...item, state: uiState }
      : item
  );
  patchState({ activeDelights: updated });
  render();

  // Remove after brief display
  if (permanent) {
    setTimeout(() => {
      const filtered = state.activeDelights.filter(
        (item) => (item.bvid || normalizeDelightCandidate(item).bvid) !== d.bvid
      );
      const newIdx = Math.min(state.delightCurrentIndex, Math.max(0, filtered.length - 1));
      patchState({ activeDelights: filtered, delightCurrentIndex: newIdx });
      render();
    }, 1500);
  }

  if (action === "view") {
    const url = buildContentUrl(d);
    if (url) window.open(url, "_blank");
  }
}

// ── Action Row ───────────────────────────────────────────────
function renderActionRow() {
  const actions = document.createElement("div");
  actions.className = "action-row";

  const reshuffleBtn = document.createElement("button");
  reshuffleBtn.className = "btn btn-brand";
  reshuffleBtn.textContent = "\u{1F500} \u6362\u4E00\u6279";
  reshuffleBtn.disabled = loading;
  reshuffleBtn.addEventListener("click", handleReshuffle);
  actions.appendChild(reshuffleBtn);

  const appendBtn = document.createElement("button");
  appendBtn.className = "btn btn-outline";
  appendBtn.textContent = "\u52A0\u8F7D\u66F4\u591A";
  appendBtn.disabled = loading;
  appendBtn.addEventListener("click", handleAppend);
  actions.appendChild(appendBtn);

  $root.appendChild(actions);
}

// ── Recommendation Card ──────────────────────────────────────
function renderCard(rawItem) {
  const item = normalizeRecommendation(rawItem);
  const card = document.createElement("div");
  card.className = "card";
  const url = buildContentUrl(item);
  const cover = getCoverImageAttrs(item.cover_url);

  const coverHtml = cover
    ? `<img class="card-cover" src="${esc(cover.src)}" alt="" loading="lazy" referrerpolicy="${cover.referrerPolicy}" onerror="this.remove()">`
    : "";

  card.innerHTML = `
    ${coverHtml}
    <div class="card-body">
      <div class="card-title">${esc(item.title)}</div>
      <div class="card-meta">
        <span class="card-source" data-source="${item.source_platform}">${esc(getSourceLabel(item.source_platform))}</span>
        ${item.up_name ? `<span>${esc(item.up_name)}</span>` : ""}
        ${item.topic_label ? `<span style="color:var(--text-muted)">${esc(item.topic_label)}</span>` : ""}
      </div>
      ${item.expression ? `<div class="card-expression">${esc(item.expression)}</div>` : ""}
    </div>`;

  // Card actions
  const actionsRow = document.createElement("div");
  actionsRow.className = "card-actions";
  actionsRow.addEventListener("click", (e) => e.stopPropagation());

  const openBtn = createCardAction("\u{1F517} \u6253\u5F00", () => {
    reportClick({ bvid: item.bvid, title: item.title, recommendation_id: item.id, topic_label: item.topic_label, up_name: item.up_name });
    if (url) window.open(url, "_blank");
  });

  const likeBtn = createCardAction("\u{1F44D}", async () => {
    likeBtn.disabled = true;
    try {
      await submitFeedback(buildFeedbackPayload(item.id, "like"));
      likeBtn.textContent = "\u2705";
    } catch { likeBtn.disabled = false; }
  });

  const dislikeBtn = createCardAction("\u{1F44E}", async () => {
    dislikeBtn.disabled = true;
    try {
      await submitFeedback(buildFeedbackPayload(item.id, "dislike"));
      dislikeBtn.textContent = "\u274C";
    } catch { dislikeBtn.disabled = false; }
  });

  const commentBtn = createCardAction("\u{1F4AC}", () => {
    feedbackSheet = { itemId: item.id, note: "", submitState: "idle" };
    renderFeedbackSheet();
  });

  actionsRow.appendChild(openBtn);
  actionsRow.appendChild(likeBtn);
  actionsRow.appendChild(dislikeBtn);
  actionsRow.appendChild(commentBtn);
  card.appendChild(actionsRow);

  // Whole card click (except action row)
  if (url) {
    card.style.cursor = "pointer";
    card.addEventListener("click", () => {
      reportClick({ bvid: item.bvid, title: item.title, recommendation_id: item.id, topic_label: item.topic_label, up_name: item.up_name });
      window.open(url, "_blank");
    });
  }

  return card;
}

function createCardAction(label, handler) {
  const btn = document.createElement("button");
  btn.className = "card-action-btn";
  btn.textContent = label;
  btn.addEventListener("click", handler);
  return btn;
}

// ── Feedback Bottom Sheet ────────────────────────────────────
function renderFeedbackSheet() {
  let overlay = document.querySelector(".feedback-sheet");
  if (!feedbackSheet) {
    if (overlay) overlay.remove();
    return;
  }

  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "feedback-sheet";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) { feedbackSheet = null; renderFeedbackSheet(); }
    });
    document.body.appendChild(overlay);
  }

  const uiState = getCommentSubmitUiState(feedbackSheet.submitState);

  overlay.innerHTML = `
    <div class="feedback-sheet-panel">
      <div class="messages-header">
        <span class="messages-title">\u5199\u4E00\u53E5</span>
        <button class="messages-close" id="feedback-close">\u2715</button>
      </div>
      <textarea class="feedback-input" id="feedback-note" placeholder="\u8BF4\u8BF4\u4F60\u7684\u60F3\u6CD5\u2026" rows="3">${esc(feedbackSheet.note)}</textarea>
      ${uiState.statusMessage ? `<div style="font-size:12px;color:var(--text-muted);margin-top:4px">${esc(uiState.statusMessage)}</div>` : ""}
      <button class="btn btn-brand" id="feedback-submit" style="margin-top:8px;width:100%" ${uiState.disabled ? "disabled" : ""}>${esc(uiState.buttonLabel)}</button>
    </div>`;

  overlay.querySelector("#feedback-close").addEventListener("click", () => {
    feedbackSheet = null;
    renderFeedbackSheet();
  });

  overlay.querySelector("#feedback-note").addEventListener("input", (e) => {
    feedbackSheet.note = e.target.value;
  });

  overlay.querySelector("#feedback-submit").addEventListener("click", async () => {
    const validation = validateCommentInput(feedbackSheet.note);
    if (!validation.valid) {
      feedbackSheet.submitState = "error";
      renderFeedbackSheet();
      return;
    }
    feedbackSheet.submitState = "submitting";
    renderFeedbackSheet();
    try {
      await submitFeedback(buildFeedbackPayload(feedbackSheet.itemId, "comment", feedbackSheet.note));
      feedbackSheet.submitState = "success";
      renderFeedbackSheet();
      setTimeout(() => { feedbackSheet = null; renderFeedbackSheet(); }, 1200);
    } catch {
      feedbackSheet.submitState = "error";
      renderFeedbackSheet();
    }
  });
}

// ── Actions ──────────────────────────────────────────────────
async function handleReshuffle() {
  if (loading) return;
  loading = true;
  render();
  try {
    const result = await reshuffleRecommendations();
    patchState({ recommendations: (result.items || []).map(normalizeRecommendation) });
  } catch { /* ignore */ }
  loading = false;
  render();
}

async function handleAppend() {
  if (loading) return;
  loading = true;
  render();
  try {
    const existing = state.recommendations.map((i) => i.bvid).filter(Boolean);
    const result = await appendRecommendations(existing);
    patchState({ recommendations: [...state.recommendations, ...(result.items || []).map(normalizeRecommendation)] });
  } catch { /* ignore */ }
  loading = false;
  render();
}

// ── Pull-to-Refresh ──────────────────────────────────────────
let pullStartY = 0;
let pulling = false;

function initPullRefresh() {
  const container = document.getElementById("app");
  container.addEventListener("touchstart", (e) => {
    if (container.scrollTop <= 0 && state.activeTab === "recommend") {
      pullStartY = e.touches[0].clientY;
      pulling = true;
    }
  }, { passive: true });

  container.addEventListener("touchmove", (e) => {
    if (!pulling) return;
    const dy = e.touches[0].clientY - pullStartY;
    const indicator = document.getElementById("pull-indicator");
    if (indicator) indicator.classList.toggle("visible", dy > 50);
  }, { passive: true });

  container.addEventListener("touchend", () => {
    if (!pulling) return;
    pulling = false;
    const indicator = document.getElementById("pull-indicator");
    if (indicator?.classList.contains("visible")) {
      indicator.classList.remove("visible");
      handleReshuffle();
    }
  }, { passive: true });
}

// ── Load ─────────────────────────────────────────────────────
async function loadData() {
  loading = true;
  render();
  try {
    const [recs, status, delights, activity] = await Promise.all([
      fetchRecommendations(),
      fetchRuntimeStatus().catch(() => null),
      fetchDelightBatch().catch(() => []),
      fetchActivityFeed({ limit: 5 }).catch(() => null),
    ]);
    patchState({
      recommendations: recs.map(normalizeRecommendation),
      runtimeStatus: status ? normalizeRuntimeStatus(status) : state.runtimeStatus,
      activeDelights: delights.map(normalizeDelightCandidate),
      delightCurrentIndex: 0,
      activityFeed: activity,
    });
  } catch { /* ignore */ }
  loading = false;
  render();
}

// ── Public API ───────────────────────────────────────────────
export function initRecommendView(root) {
  $root = root;
  if (!loaded) {
    loaded = true;
    initPullRefresh();
  }
  loadData();
}

export function onStreamEvent(payload) {
  const type = payload?.type || payload?.event;
  if (type === "refresh.pool_updated") {
    // Merge runtime status from event
    patchState({
      runtimeStatus: mergeRuntimeStatusEvent(state.runtimeStatus, payload.data || payload),
    });
    loadData();
  } else if (type === "refresh.started" || type === "refresh.strategy") {
    patchState({ runtimeEvent: payload.data || payload });
    render();
  } else if (type === "activity.added") {
    // Prepend to activity feed
    const item = payload.data || payload;
    if (item?.summary) {
      const feed = state.activityFeed || {};
      patchState({
        activityFeed: {
          ...feed,
          items: [item, ...(feed.items || [])],
          live_summary: item.summary,
        },
      });
      render();
    }
  } else if (type === "delight.candidate") {
    const item = payload.data || payload;
    if (item?.title) {
      patchState({
        activeDelights: [...state.activeDelights, normalizeDelightCandidate(item)],
      });
      render();
    }
  } else if (type === "delight.liked" || type === "delight.disliked") {
    // Another client (e.g. extension) dismissed this delight — remove from local queue
    const bvid = (payload.data || payload)?.bvid;
    if (bvid) {
      const filtered = state.activeDelights.filter(
        (d) => (d.bvid || normalizeDelightCandidate(d).bvid) !== bvid
      );
      if (filtered.length !== state.activeDelights.length) {
        const newIdx = Math.min(state.delightCurrentIndex, Math.max(0, filtered.length - 1));
        patchState({ activeDelights: filtered, delightCurrentIndex: newIdx });
        render();
      }
    }
  }
}
