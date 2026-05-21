/**
 * Recommend view — compact header, semantic pool status, delight tray,
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
  getReadyRecommendationHint,
  normalizeActivityFeed,
  getMobileRecommendationHeaderState,
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
const feedbackDone = new Map(); // recId -> "like" | "dislike" | "comment"

// ── Escape helper ────────────────────────────────────────────
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

// ── Render ────────────────────────────────────────────────────
function render() {
  if (!$root) return;

  // Capture scroll position before replacing DOM.
  const scrollTop = $root.parentElement?.scrollTop ?? 0;

  // Build everything into a fragment, then swap in one shot.
  const frag = document.createDocumentFragment();

  // Pull indicator
  const pull = document.createElement("div");
  pull.className = "pull-indicator";
  pull.id = "pull-indicator";
  pull.textContent = "\u2193 \u4E0B\u62C9\u5237\u65B0";
  frag.appendChild(pull);

  // Header slot
  const headerSlot = document.createElement("div");
  headerSlot.id = "header-slot";
  frag.appendChild(headerSlot);
  renderInto(headerSlot, renderRecommendationHeader);

  // Delight slot
  const delightSlot = document.createElement("div");
  delightSlot.id = "delight-slot";
  frag.appendChild(delightSlot);
  renderInto(delightSlot, renderDelightTray);

  // Recommendation cards
  const recs = state.recommendations;
  if (recs.length === 0 && !loading) {
    const hint = getReadyRecommendationHint(state.runtimeStatus);
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div class="empty-state-icon">\u{1F30A}</div><div class="empty-state-text">${esc(hint.message)}</div>`;
    frag.appendChild(empty);
  }

  for (const item of recs) {
    frag.appendChild(renderCard(item));
  }

  renderInto(frag, renderLoadMoreRow);

  if (loading) {
    const sp = document.createElement("div");
    sp.style.padding = "20px";
    sp.innerHTML = `<div class="spinner"></div>`;
    frag.appendChild(sp);
  }

  $root.replaceChildren(frag);

  // Restore scroll position so the page doesn't jump to top.
  if ($root.parentElement) $root.parentElement.scrollTop = scrollTop;

  // Feedback bottom sheet
  renderFeedbackSheet();
}

/** Run a sub-renderer with $root temporarily pointed at the given container. */
function renderInto(container, fn) {
  const prev = $root;
  $root = container;
  fn();
  $root = prev;
}

// ── Recommendation Header ───────────────────────────────────
function renderRecommendationHeader() {
  const headerState = getMobileRecommendationHeaderState({
    runtimeStatus: state.runtimeStatus,
    activityFeed: state.activityFeed,
    runtimeEvent: state.runtimeEvent,
    activityExpanded: state.activityExpanded,
  });

  const header = document.createElement("section");
  header.className = "recommend-header-card";

  const top = document.createElement("div");
  top.className = "recommend-header-top";
  top.innerHTML = `
    <div class="recommend-header-copy">
      <p class="recommend-kicker">${esc(headerState.kicker)}</p>
      <h2 class="recommend-title">${esc(headerState.title)}</h2>
    </div>`;

  const refreshBtn = document.createElement("button");
  refreshBtn.className = "btn btn-outline recommend-refresh-btn";
  refreshBtn.type = "button";
  refreshBtn.textContent = loading ? "\u6B63\u5728\u6362\u4E00\u6279\u2026" : headerState.primaryActionLabel;
  refreshBtn.disabled = loading;
  refreshBtn.addEventListener("click", handleReshuffle);
  top.appendChild(refreshBtn);
  header.appendChild(top);

  if (headerState.poolChips.length > 0) {
    const grid = document.createElement("div");
    grid.className = "recommend-pool-grid";
    for (const chip of headerState.poolChips) {
      const item = document.createElement("div");
      item.className = "recommend-pool-chip";
      item.dataset.tone = chip.tone;
      item.title = `${chip.label}: ${chip.value}`;
      item.innerHTML = `
        <span class="recommend-pool-label">${esc(chip.label)}</span>
        <span class="recommend-pool-value">${esc(String(chip.value))}</span>`;
      grid.appendChild(item);
    }
    header.appendChild(grid);
  }

  const activity = document.createElement("div");
  activity.className = "recommend-activity-line";
  activity.innerHTML = `<span class="recommend-activity-text">${esc(headerState.activityLine)}</span>`;
  const toggle = document.createElement("button");
  toggle.className = "recommend-activity-toggle";
  toggle.type = "button";
  toggle.textContent = headerState.activityToggleLabel;
  toggle.addEventListener("click", () => {
    patchState({ activityExpanded: !state.activityExpanded });
    rerenderHeaderOnly();
  });
  activity.appendChild(toggle);
  header.appendChild(activity);

  if (headerState.activityExpanded && headerState.activityItems.length > 0) {
    const list = document.createElement("div");
    list.className = "recommend-activity-list";
    for (const item of headerState.activityItems) {
      const row = document.createElement("div");
      row.className = "activity-item";
      row.innerHTML = `<span class="activity-item-time">${esc(formatRelativeTimestamp(item.created_at))}</span> ${esc(item.summary)}`;
      list.appendChild(row);
    }
    if (headerState.activityHasMore) {
      const more = document.createElement("button");
      more.className = "load-more-btn";
      more.textContent = "\u52A0\u8F7D\u66F4\u591A";
      more.addEventListener("click", loadMoreActivity);
      list.appendChild(more);
    }
    header.appendChild(list);
  }

  $root.appendChild(header);
}

/** Re-render only the header without touching cards or delight. */
function rerenderHeaderOnly() {
  const slot = document.getElementById("header-slot");
  if (!slot) return;
  slot.innerHTML = "";
  renderInto(slot, renderRecommendationHeader);
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
    rerenderHeaderOnly();
  } catch { /* ignore */ }
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
    ? `<span class="delight-thumb"><img src="${esc(cover.src)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-fallback');this.remove()"></span>`
    : `<span class="delight-thumb is-fallback">\u2728</span>`;
  const reasonText = d.delight_reason || d.delight_hook || "";

  tray.innerHTML = `
    ${delights.length > 1 ? `
      <div class="delight-corner-nav">
        <button class="delight-inline-nav" id="delight-prev" type="button" ${idx <= 0 ? "disabled" : ""}>\u2039</button>
        <span class="delight-inline-counter">${idx + 1}/${delights.length}</span>
        <button class="delight-inline-nav" id="delight-next" type="button" ${idx >= delights.length - 1 ? "disabled" : ""}>\u203A</button>
      </div>
    ` : ""}
    ${!uiState.handled ? `<button class="delight-later-btn" id="delight-later" type="button" title="\u7A0D\u540E\u770B" aria-label="\u7A0D\u540E\u770B">\u00D7</button>` : ""}
    <div class="delight-compact">
      <div class="delight-kicker-line">
        <span class="delight-tag">\u60CA\u559C\u63A8\u8350</span>
        ${d.delight_hook ? `<span class="delight-hook-badge">${esc(d.delight_hook)}</span>` : ""}
      </div>
      <div class="delight-feature-copy">
        <div class="delight-title">${esc(d.title)}</div>
        ${reasonText ? `
          <div class="delight-reason-wrap">
            ${coverHtml}
            <div class="delight-reason"><span class="delight-reason-label">\u63A8\u8350\u539F\u56E0</span>${esc(reasonText)}</div>
            <div class="delight-meta">
              <span class="card-source" data-source="${d.source_platform}">${esc(getSourceLabel(d.source_platform))}</span>
              ${uiState.score_label ? `<span>${esc(uiState.score_label)}</span>` : ""}
            </div>
          </div>
        ` : `
          <div class="delight-media-only">${coverHtml}</div>
          <div class="delight-meta">
            <span class="card-source" data-source="${d.source_platform}">${esc(getSourceLabel(d.source_platform))}</span>
            ${uiState.score_label ? `<span>${esc(uiState.score_label)}</span>` : ""}
          </div>
        `}
      </div>
    </div>`;

  if (uiState.handled) {
    tray.innerHTML += `<div class="delight-result-state" data-tone="${esc(uiState.response_tone)}">${esc(uiState.response_message)}</div>`;
  } else {
    // Action buttons
    const actions = document.createElement("div");
    actions.className = "delight-actions";
    const btns = [
      { label: "\u770B\u770B", action: "view" },
      { label: "\u559C\u6B22", action: "like" },
      { label: "\u4E0D\u611F\u5174\u8DA3", action: "reject" },
      { label: "\u804A\u4E00\u804A", action: "chat" },
    ];
    for (const b of btns) {
      const btn = document.createElement("button");
      btn.className = `btn ${b.action === "view" ? "btn-brand" : "btn-outline"}`;
      btn.textContent = b.label;
      btn.addEventListener("click", () => handleDelightAction(d, b.action));
      actions.appendChild(btn);
    }
    tray.appendChild(actions);
  }

  tray.querySelector("#delight-later")?.addEventListener("click", () => {
    skipDelightAt(idx);
  });

  if (delights.length > 1) {
    tray.querySelector("#delight-prev")?.addEventListener("click", () => {
      if (idx > 0) { patchState({ delightCurrentIndex: idx - 1 }); rerenderDelightOnly(); }
    });
    tray.querySelector("#delight-next")?.addEventListener("click", () => {
      if (idx < delights.length - 1) { patchState({ delightCurrentIndex: idx + 1 }); rerenderDelightOnly(); }
    });
  }

  $root.appendChild(tray);
}

/** Re-render only the delight tray without touching the rest of the page. */
function rerenderDelightOnly() {
  const slot = document.getElementById("delight-slot");
  if (!slot) return;
  slot.innerHTML = "";
  renderInto(slot, renderDelightTray);
}

function skipDelightAt(index) {
  const filtered = state.activeDelights.filter((_, i) => i !== index);
  const newIdx = Math.min(index, Math.max(0, filtered.length - 1));
  patchState({ activeDelights: filtered, delightCurrentIndex: newIdx });
  rerenderDelightOnly();
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

  // "view" / "like" / "reject" — call API with correct token
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
  rerenderDelightOnly();

  // Remove after brief display
  if (permanent) {
    setTimeout(() => {
      const filtered = state.activeDelights.filter(
        (item) => (item.bvid || normalizeDelightCandidate(item).bvid) !== d.bvid
      );
      const newIdx = Math.min(state.delightCurrentIndex, Math.max(0, filtered.length - 1));
      patchState({ activeDelights: filtered, delightCurrentIndex: newIdx });
      rerenderDelightOnly();
    }, 1500);
  }

  if (action === "view") {
    const url = buildContentUrl(d);
    if (url) window.open(url, "_blank");
  }
}

// ── Load More ────────────────────────────────────────────────
function renderLoadMoreRow() {
  if (state.recommendations.length === 0) return;
  const headerState = getMobileRecommendationHeaderState();
  const actions = document.createElement("div");
  actions.className = "load-more-row";
  const appendBtn = document.createElement("button");
  appendBtn.className = "btn btn-outline load-more-action";
  appendBtn.textContent = headerState.secondaryActionLabel;
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
    ? `<div class="card-cover-frame"><img class="card-cover" src="${esc(cover.src)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-error');this.remove()"></div>`
    : `<div class="card-cover-frame is-error"></div>`;

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

  // Card actions — icon style with feedback state persistence
  const actionsRow = document.createElement("div");
  actionsRow.className = "card-actions";
  actionsRow.addEventListener("click", (e) => e.stopPropagation());

  const alreadyFeedback = feedbackDone.get(item.id);

  const openBtn = createCardAction("\u{1F517} \u6253\u5F00", () => {
    reportClick({ bvid: item.bvid, title: item.title, recommendation_id: item.id, topic_label: item.topic_label, up_name: item.up_name });
    if (url) window.open(url, "_blank");
  });

  const likeBtn = createCardAction(alreadyFeedback === "like" ? "\u2705" : "\u{1F44D}", async () => {
    likeBtn.disabled = true;
    likeBtn.textContent = "\u2026";
    try {
      await submitFeedback(buildFeedbackPayload(item.id, "like"));
      feedbackDone.set(item.id, "like");
      likeBtn.textContent = "\u2705";
    } catch {
      likeBtn.disabled = false;
      likeBtn.textContent = "\u{1F44D}";
    }
  });
  if (alreadyFeedback === "like") likeBtn.disabled = true;

  const dislikeBtn = createCardAction(alreadyFeedback === "dislike" ? "\u274C" : "\u{1F44E}", async () => {
    dislikeBtn.disabled = true;
    dislikeBtn.textContent = "\u2026";
    try {
      await submitFeedback(buildFeedbackPayload(item.id, "dislike"));
      feedbackDone.set(item.id, "dislike");
      dislikeBtn.textContent = "\u274C";
    } catch {
      dislikeBtn.disabled = false;
      dislikeBtn.textContent = "\u{1F44E}";
    }
  });
  if (alreadyFeedback === "dislike") dislikeBtn.disabled = true;

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
    const normalizedRecs = recs.map(normalizeRecommendation);
    // Restore feedback state from backend so it survives page refresh.
    for (const rec of normalizedRecs) {
      if (rec.feedback_type && !feedbackDone.has(rec.id)) {
        feedbackDone.set(rec.id, rec.feedback_type);
      }
    }
    patchState({
      recommendations: normalizedRecs,
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
    rerenderHeaderOnly();
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
      rerenderHeaderOnly();
    }
  } else if (type === "delight.candidate") {
    const item = payload.data || payload;
    if (item?.title) {
      patchState({
        activeDelights: [...state.activeDelights, normalizeDelightCandidate(item)],
      });
      rerenderDelightOnly();
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
        rerenderDelightOnly();
      }
    }
  }
}
