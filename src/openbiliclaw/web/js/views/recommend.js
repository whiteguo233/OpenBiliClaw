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
  startChatTurn,
  fetchChatTurn,
  fetchChatTurns,
  saveItem,
  removeSavedItem,
  savedItemStatus,
} from "../api.js";
import { state, patchState } from "../state.js";
import {
  getCoverImageAttrs,
  getRecommendationCardKind,
  getRecommendationCoverPreloadUrls,
  getRecommendationImageLoadingAttrs,
  normalizeRecommendation,
  reconcileRecommendationReplacement,
  recommendationStats,
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
  buildRecommendationClickPayload,
  normalizeSourcePlatform,
  normalizeSavedIdentity,
  getSourceLabel,
  formatRelativeTimestamp,
  getPublishedTimeDisplay,
  getMobileChatSession,
  shouldAutoAppendRecommendations,
} from "../view-models.js";
import { openContentUrl } from "../app-launch.js";
import { createSavedMutationRegistry } from "../saved-sync-runtime.js";

const dialogueConfirmation = globalThis.OpenBiliClawDialogueConfirmation;
if (!dialogueConfirmation) {
  throw new Error("dialogue-confirmation shared helper did not load");
}
const { renderMarkdown } = dialogueConfirmation;

let $root = null;
let loaded = false;
let loading = false;
let feedbackSheet = null; // { itemId, note, submitState }
const feedbackDone = new Map(); // recId -> "like" | "dislike" | "comment"
const savedMutations = createSavedMutationRegistry();
const COVER_PRELOAD_BATCH_SIZE = 12;
const COVER_PRELOAD_WAIT_TIMEOUT_MS = 3000;
// Only the first few cards in a freshly rendered list load their covers eagerly.
// The rest use loading="lazy" and are preheated by observeScrollPreheat as they
// approach the viewport. This keeps a page append from issuing 10 eager image
// fetches at once (the main source of load-more scroll jank).
const CARD_EAGER_COVER_COUNT = 4;
const AUTO_APPEND_ROOT_MARGIN = "700px 0px 1400px 0px";
const SCROLL_PREHEAT_LOOKAHEAD = 16;
const SCROLL_PREHEAT_ROOT_MARGIN = "0px 0px 2400px 0px";
const warmedCoverUrls = new Set();
const decodedCoverUrls = new Set();
const warmingImages = new Map();
let autoAppendObserver = null;
let scrollPreheatObserver = null;
let autoAppendExhausted = false;
let autoAppendVisible = false;
let autoAppendWaitingForRefill = false;
let poolStatusVersion = 0;
let autoAppendUserArmed = false;
let autoAppendTouchY = null;
let autoAppendIntentInitialized = false;
const RECOVERY_DELAYS_MS = [1000, 2000, 4000, 8000];
let recommendationLoadState = "idle";
let runtimeStatusLoadState = "idle";
let recommendationRecoveryAttempt = 0;
let runtimeStatusRecoveryAttempt = 0;
let recommendationRecoveryTimer = null;
let runtimeStatusRecoveryTimer = null;
let recommendationRecoveryInFlight = false;
let runtimeStatusRecoveryInFlight = false;
let recommendationRecoveryPending = false;
let runtimeStatusRecoveryPending = false;
let runtimeStatusGeneration = 0;
let recommendationActionMessage = "";

// Delight auto-advance
// 轮播间隔。原值 4s 是初版占位，实测太快：一张卡的标题 + 推荐理由 + 正文摘录读下来
// 就要十几秒，还没看清就被换走。60s 给足阅读时间，想快进有拖拽和上一条 / 下一条。
// 与桌面端 web/desktop/assets/js/app.js 的同名常量保持一致。
const DELIGHT_AUTO_ADVANCE_MS = 60000;
// 拖拽死区：位移 <10px 视为点击而非滑动，整卡点击直接打开内容（与信息流普通卡片
// 一致，见 issue #126）。与桌面端 _DELIGHT_DRAG_DEAD_ZONE 保持同值。
// 10px < 位移 < 50px（DELIGHT_SWIPE_THRESHOLD）是有意留白：既不算点击也不切卡，
// 避免手指轻微拖动时误开内容。
const DELIGHT_DRAG_DEAD_ZONE = 10;
const DELIGHT_SWIPE_THRESHOLD = 50;
let _delightAutoTimer = null;
let _delightDragging = false;
let _delightSwipeStartX = 0;
let _delightNavTimer = null;

// ── Escape helper ────────────────────────────────────────────
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

export function publishedTimeHtml(item) {
  const display = getPublishedTimeDisplay(item);
  if (!display) return "";
  const title = display.title ? ` title="${esc(display.title)}"` : "";
  return `<span class="card-published-time"${title}>${esc(display.text)}</span>`;
}

export function savedIdentity(item = {}) {
  return normalizeSavedIdentity(item);
}

function announceSavedAction(message, isError = false) {
  if (!$root) return;
  let status = $root.querySelector(".saved-action-status");
  if (!status) {
    status = document.createElement("p");
    status.className = "saved-action-status";
    status.setAttribute("aria-live", "polite");
    $root.prepend(status);
  }
  if (isError) status.setAttribute("role", "alert");
  else status.removeAttribute("role");
  status.textContent = message;
}

function isSavedLocally(listKind, itemKey) {
  return savedMutations.isSaved(listKind, itemKey);
}

function toggleSavedLocally(listKind, item) {
  return savedMutations.toggle(listKind, item.item_key, {
    add: () => saveItem(listKind, item),
    remove: () => removeSavedItem(listKind, item.item_key),
  });
}

function hydrateSavedLocally(listKind, item, update) {
  return savedMutations.hydrate(
    listKind,
    item.item_key,
    () => savedItemStatus(listKind, item.item_key),
  ).then(() => update(isSavedLocally(listKind, item.item_key)));
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

  // Recommendation cards — hide disliked items
  const recs = state.recommendations.filter((r) => feedbackDone.get(r.id) !== "dislike" && r.feedback_type !== "dislike");
  if (recs.length === 0 && !loading) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div class="empty-state-icon">\u{1F30A}</div><div class="empty-state-text">${esc(recommendationEmptyMessage())}</div>`;
    if (
      recommendationLoadState === "failed-exhausted" ||
      runtimeStatusLoadState === "failed-exhausted"
    ) {
      const retry = document.createElement("button");
      retry.className = "btn btn-outline";
      retry.type = "button";
      retry.textContent = "重新加载";
      retry.addEventListener("click", restartFailedRecoveries);
      empty.appendChild(retry);
    }
    frag.appendChild(empty);
  }

  for (const [index, item] of recs.entries()) {
    frag.appendChild(renderCard(item, index));
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
  void warmRecommendationCovers(recs);
  observeScrollPreheat();
  observeAutoAppendSentinel();
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

  if (recommendationActionMessage) {
    const notice = document.createElement("div");
    notice.className = "recommend-action-note";
    notice.textContent = recommendationActionMessage;
    header.appendChild(notice);
  }

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

function rerenderRuntimeDependentChrome() {
  rerenderHeaderOnly();
  const emptyText = document.querySelector(".empty-state .empty-state-text");
  if (emptyText) {
    emptyText.textContent = recommendationEmptyMessage();
  }
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

// ── Delight Inline Chat Helpers ──────────────────────────────
const DELIGHT_POLL_INTERVAL_MS = 1200;
const DELIGHT_POLL_DEADLINE_MS = 180_000;
const activeDelightPolls = new Map(); // turnId -> timeoutId

function createClientTurnId(prefix = "turn") {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  return `${prefix}-${String(random).replace(/[^a-zA-Z0-9_-]/g, "")}`;
}

function pollDelightTurn(turnId, bvid) {
  if (!turnId || activeDelightPolls.has(turnId)) return;
  const startedAt = Date.now();

  async function tick() {
    try {
      const turn = await fetchChatTurn(turnId);
      if (turn.status === "completed" || turn.status === "failed") {
        activeDelightPolls.delete(turnId);
        applyTurnResult(bvid, turnId, turn);
        return;
      }
    } catch { /* retry until deadline */ }
    if (Date.now() - startedAt > DELIGHT_POLL_DEADLINE_MS) {
      activeDelightPolls.delete(turnId);
      return;
    }
    activeDelightPolls.set(turnId, setTimeout(tick, DELIGHT_POLL_INTERVAL_MS));
  }

  activeDelightPolls.set(turnId, 0);
  void tick();
}

/** Update a specific turn in a delight's turns array after polling completes. */
function applyTurnResult(bvid, turnId, turn) {
  const updated = state.activeDelights.map((item) => {
    const norm = normalizeDelightCandidate(item);
    if (norm.bvid !== bvid) return item;
    const turns = (norm.turns || []).map((t) => {
      if (t.turn_id !== turnId) return t;
      return { ...t, reply: turn.reply || "", status: turn.status, error: turn.error || "" };
    });
    const lastCompleted = turn.status === "completed";
    return {
      ...item,
      turns,
      state: lastCompleted ? "chatted" : (turn.status === "failed" ? norm.state : "chatting"),
      response_message: lastCompleted ? "这句已经记下，后面会更会试探。" : (turn.status === "failed" ? "这句还没发出去，稍后再试。" : norm.response_message),
      chat_reply: lastCompleted ? (turn.reply || "") : norm.chat_reply,
    };
  });
  patchState({ activeDelights: updated });
  rerenderDelightOnly();
}

/** Send a chat message for a delight inline chat turn. */
async function sendDelightChat(d, message) {
  const turnId = createClientTurnId("delight");
  const userTurn = { turn_id: turnId, message, reply: "", status: "pending", error: "" };

  // Optimistically append user+pending turn
  const updated = state.activeDelights.map((item) => {
    const norm = normalizeDelightCandidate(item);
    if (norm.bvid !== d.bvid) return item;
    return {
      ...item,
      turns: [...norm.turns, userTurn],
      draft: "",
      state: "chatting",
      response_message: "阿B 正在品你这句话。",
      chat_turn_id: turnId,
    };
  });
  patchState({ activeDelights: updated });
  rerenderDelightOnly();

  try {
    const turn = await startChatTurn({
      turnId,
      ...getMobileChatSession("delight"),
      subjectId: d.bvid,
      subjectTitle: d.title,
      message,
    });
    if (turn.status === "completed" || turn.status === "failed") {
      applyTurnResult(d.bvid, turnId, turn);
    } else {
      pollDelightTurn(turnId, d.bvid);
    }
  } catch {
    // Mark the turn as failed locally
    applyTurnResult(d.bvid, turnId, { reply: "", status: "failed", error: "网络错误" });
  }
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
  const textFallback = esc(d.body_text || d.title || "一条文字推荐");
  const coverHtml = cover
    ? `<span class="delight-thumb"><img src="${esc(cover.src)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-fallback');this.remove()"></span>`
    : `<span class="delight-thumb is-fallback is-text-card"><span class="delight-thumb-text">${textFallback}</span></span>`;
  const reasonText = d.delight_reason || d.delight_hook || "";
  const statsText = recommendationStats(d);
  const publishedHtml = publishedTimeHtml(d);

  tray.innerHTML = `
    ${delights.length > 1 ? `
      <div class="delight-corner-nav">
        <button class="delight-inline-nav" id="delight-prev" type="button" ${idx <= 0 ? "disabled" : ""}>\u2039</button>
        <span class="delight-inline-counter">${idx + 1}/${delights.length}</span>
        <button class="delight-inline-nav" id="delight-next" type="button" ${idx >= delights.length - 1 ? "disabled" : ""}>\u203A</button>
      </div>
    ` : ""}
    ${!uiState.handled ? `<button class="delight-later-btn" id="delight-later" type="button" title="\u770B\u8FC7\u4E86\uFF0C\u4E0D\u518D\u63A8\u8350" aria-label="\u770B\u8FC7\u4E86\uFF0C\u4E0D\u518D\u63A8\u8350">${X_SVG_ICON}</button>` : ""}
    <div class="delight-compact">
      <div class="delight-kicker-line">
        <span class="delight-tag">\u60CA\u559C\u63A8\u8350</span>
        ${d.delight_hook ? `<span class="delight-hook-badge">${esc(d.delight_hook)}</span>` : ""}
      </div>
      <div class="delight-feature-copy">
        <div class="delight-title">${esc(d.title)}</div>
        ${statsText ? `<div class="card-stats delight-stats">${esc(statsText)}</div>` : ""}
        ${reasonText ? `
          <div class="delight-reason-wrap">
            ${coverHtml}
            <div class="delight-reason"><span class="delight-reason-label">\u63A8\u8350\u539F\u56E0</span>${esc(reasonText)}</div>
            <div class="delight-meta">
              <span class="card-source" data-source="${d.source_platform}">${esc(getSourceLabel(d.source_platform))}</span>
              ${uiState.score_label ? `<span>${esc(uiState.score_label)}</span>` : ""}
              ${publishedHtml}
            </div>
          </div>
        ` : `
          <div class="delight-media-only">${coverHtml}</div>
          <div class="delight-meta">
            <span class="card-source" data-source="${d.source_platform}">${esc(getSourceLabel(d.source_platform))}</span>
            ${uiState.score_label ? `<span>${esc(uiState.score_label)}</span>` : ""}
            ${publishedHtml}
          </div>
        `}
      </div>
    </div>`;

  if (uiState.show_status) {
    tray.innerHTML += `<div class="delight-result-state" data-tone="${esc(uiState.response_tone)}">${esc(uiState.response_message)}</div>`;
  }
  if (uiState.show_actions) {
    // Action buttons
    const actions = document.createElement("div");
    actions.className = "delight-actions";
    const btns = [
      { label: "\u770B\u770B", action: "view" },
      { label: "\u559C\u6B22", action: "like" },
      { label: "\u2606", action: "watch-later" },
      { label: "\u2661", action: "favorite" },
      { label: "\u4E0D\u611F\u5174\u8DA3", action: "reject" },
      { label: "\u804A\u4E00\u804A", action: "chat" },
    ];
    for (const b of btns) {
      const btn = document.createElement("button");
      btn.className = `btn ${b.action === "view" ? "btn-brand" : "btn-outline"}`;
      btn.dataset.delightAction = b.action;
      // 稍后再看 = 时钟 / 收藏 = 星星，紧凑 SVG 图标按钮，状态走 aria-pressed。
      if (b.action === "watch-later" || b.action === "favorite") {
        btn.classList.add("delight-save-toggle");
        btn.classList.add(b.action === "favorite" ? "favorite-btn" : "watch-later-btn");
        btn.innerHTML = b.action === "favorite" ? STAR_SVG_ICON : CLOCK_SVG_ICON;
        btn.setAttribute("aria-pressed", "false");
      } else {
        btn.textContent = b.label;
      }
      if (b.action === "watch-later") {
        const savedItem = savedIdentity(d);
        btn.title = "稍后再看";
        btn.addEventListener("click", async () => {
          if (savedMutations.isBusy("watch_later", savedItem.item_key)) return;
          btn.disabled = true;
          const wasSaved = isSavedLocally("watch_later", savedItem.item_key);
          announceSavedAction(wasSaved ? "正在从本地稍后再看移除…" : "正在保存到本地稍后再看…");
          btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
          try {
            await toggleSavedLocally("watch_later", savedItem);
            announceSavedAction(wasSaved ? "已从本地稍后再看移除；平台记录不变。" : "已保存到本地，平台同步状态可在稍后页查看。");
          } catch {
            btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
            announceSavedAction("本地稍后再看操作失败，请重试。", true);
          } finally {
            btn.disabled = false;
          }
        });
        if (isSavedLocally("watch_later", savedItem.item_key)) btn.setAttribute("aria-pressed", "true");
        void hydrateSavedLocally("watch_later", savedItem, (saved) => {
          btn.setAttribute("aria-pressed", saved ? "true" : "false");
        });
      } else if (b.action === "favorite") {
        const savedItem = savedIdentity(d);
        btn.title = "收藏";
        btn.addEventListener("click", async () => {
          if (savedMutations.isBusy("favorite", savedItem.item_key)) return;
          btn.disabled = true;
          const wasSaved = isSavedLocally("favorite", savedItem.item_key);
          announceSavedAction(wasSaved ? "正在从本地收藏移除…" : "正在保存到本地收藏…");
          btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
          try {
            await toggleSavedLocally("favorite", savedItem);
            announceSavedAction(wasSaved ? "已从本地收藏移除；平台记录不变。" : "已保存到本地，平台同步状态可在收藏页查看。");
          } catch {
            btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
            announceSavedAction("本地收藏操作失败，请重试。", true);
          } finally {
            btn.disabled = false;
          }
        });
        if (isSavedLocally("favorite", savedItem.item_key)) btn.setAttribute("aria-pressed", "true");
        void hydrateSavedLocally("favorite", savedItem, (saved) => {
          btn.setAttribute("aria-pressed", saved ? "true" : "false");
        });
      } else {
        btn.addEventListener("click", () => handleDelightAction(d, b.action));
        if (b.action === "like") {
          btn.setAttribute("aria-pressed", uiState.like_pressed ? "true" : "false");
          btn.disabled = uiState.like_disabled;
        }
      }
      actions.appendChild(btn);
    }
    tray.appendChild(actions);
  }

  // ── Chat bubbles (turns history) ────────────────────────────
  const turns = d.turns || [];
  if (turns.length > 0) {
    const bubbleArea = document.createElement("div");
    bubbleArea.className = "delight-chat-area";
    for (const t of turns) {
      // User bubble
      const userBubble = document.createElement("div");
      userBubble.className = "delight-chat-bubble user";
      userBubble.textContent = t.message;
      bubbleArea.appendChild(userBubble);
      // AI bubble
      const aiBubble = document.createElement("div");
      if (t.status === "pending") {
        aiBubble.className = "delight-chat-bubble assistant thinking";
        aiBubble.textContent = "阿B 正在品你这句话…";
      } else if (t.status === "failed") {
        aiBubble.className = "delight-chat-bubble assistant error";
        aiBubble.textContent = t.error || "这句还没发出去，稍后再试。";
      } else {
        aiBubble.className = "delight-chat-bubble assistant";
        aiBubble.classList.add("chat-markdown");
        aiBubble.innerHTML = renderMarkdown(t.reply || "");
      }
      bubbleArea.appendChild(aiBubble);
    }
    tray.appendChild(bubbleArea);
  }

  // ── Inline composer ─────────────────────────────────────────
  if (d.composer_open) {
    const composer = document.createElement("div");
    composer.className = "delight-composer";
    let sendInitiated = false;

    const input = document.createElement("textarea");
    input.className = "delight-composer-input";
    input.rows = 2;
    input.placeholder = "\u804A\u804A\u8FD9\u6761\u63A8\u8350\u2026";
    input.value = d.draft || "";
    input.addEventListener("input", () => {
      // Save draft in-place without re-rendering
      const cur = state.activeDelights[state.delightCurrentIndex];
      if (cur && normalizeDelightCandidate(cur).bvid === d.bvid) {
        cur.draft = input.value;
      }
    });

    const sendBtn = document.createElement("button");
    sendBtn.className = "btn btn-brand delight-composer-send";
    sendBtn.textContent = "\u53D1\u51FA\u53BB";
    sendBtn.addEventListener("click", () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      sendInitiated = true;
      sendDelightChat(d, text);
    });

    // Collapse the composer back to the action buttons when focus leaves it (the
    // user opened it then changed their mind). The draft is saved to state so
    // reopening restores it; a real send is guarded so tapping send isn't lost.
    input.addEventListener("blur", (e) => {
      if (e.relatedTarget && composer.contains(e.relatedTarget)) return;
      setTimeout(() => {
        if (sendInitiated) return;
        if (composer.contains(document.activeElement)) return;
        const cur = state.activeDelights[state.delightCurrentIndex];
        if (!cur || normalizeDelightCandidate(cur).bvid !== d.bvid) return;
        if (!normalizeDelightCandidate(cur).composer_open) return;
        const updated = state.activeDelights.map((item) => {
          if (normalizeDelightCandidate(item).bvid !== d.bvid) return item;
          return { ...item, composer_open: false, draft: input.value };
        });
        patchState({ activeDelights: updated });
        rerenderDelightOnly();
      }, 120);
    });

    // Allow Enter to send (Shift+Enter for newline)
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendBtn.click();
      }
    });

    composer.appendChild(input);
    composer.appendChild(sendBtn);
    tray.appendChild(composer);

    // Focus the textarea after DOM insertion
    requestAnimationFrame(() => input.focus());
  }

  tray.querySelector("#delight-later")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await respondToDelight(d.bvid, "dismiss", d.title);
      skipDelightAt(idx);
    } catch {
      button.disabled = false;
      button.title = "操作失败，请重试";
      button.setAttribute("aria-label", "操作失败，请重试");
    }
  });

  if (delights.length > 1) {
    tray.querySelector("#delight-prev")?.addEventListener("click", () => {
      navigateDelight(idx <= 0 ? delights.length - 1 : idx - 1);
    });
    tray.querySelector("#delight-next")?.addEventListener("click", () => {
      navigateDelight(idx >= delights.length - 1 ? 0 : idx + 1);
    });
  }

  // 交互元素阻止事件冒泡，避免触发拖拽
  tray.querySelectorAll("button, [data-delight], input, select, textarea, .delight-composer, .delight-corner-nav, .delight-inline-nav").forEach((el) => {
    el.addEventListener("pointerdown", (e) => e.stopPropagation());
  });
  // 整卡点击打开内容（issue #126：惊喜卡此前只有「看看」可跳转，和下方信息流卡片
  // 的整卡点击不一致）。已反馈过（show_actions=false）、正在输入聊天、或拿不到
  // URL 时不接管点击。
  const canTapOpenDelight = Boolean(uiState.show_actions && !d.composer_open && buildContentUrl(d));
  if (canTapOpenDelight) {
    tray.style.cursor = "pointer";
  }

  // 指针拖拽切换
  tray.addEventListener("pointerdown", (e) => {
    _stopDelightAutoAdvance();
    _delightDragging = true;
    _delightSwipeStartX = e.clientX;
    tray.setPointerCapture(e.pointerId);
    tray.classList.add("is-dragging");
    e.preventDefault();
  });
  tray.addEventListener("pointermove", (e) => {
    if (!_delightDragging) return;
    const dx = e.clientX - _delightSwipeStartX;
    const maxDrag = tray.offsetWidth * 0.3;
    const clamped = Math.max(-maxDrag, Math.min(maxDrag, dx));
    const atEdge = (dx > 0 && state.delightCurrentIndex === 0) || (dx < 0 && state.delightCurrentIndex >= delights.length - 1);
    const factor = atEdge ? 0.25 : 1;
    tray.style.setProperty("--drag-offset", `${clamped * factor}px`);
  });
  tray.addEventListener("pointerup", (e) => {
    if (!_delightDragging) return;
    _delightDragging = false;
    tray.classList.remove("is-dragging");
    tray.releasePointerCapture(e.pointerId);
    const dx = e.clientX - _delightSwipeStartX;
    if (Math.abs(dx) >= DELIGHT_SWIPE_THRESHOLD) {
      if (dx > 0) {
        navigateDelight(state.delightCurrentIndex <= 0 ? delights.length - 1 : state.delightCurrentIndex - 1);
      } else {
        navigateDelight(state.delightCurrentIndex >= delights.length - 1 ? 0 : state.delightCurrentIndex + 1);
      }
    } else if (Math.abs(dx) < DELIGHT_DRAG_DEAD_ZONE && canTapOpenDelight) {
      // 死区内松手 = 点击整卡，等同「看看」。按钮 / 输入框在 pointerdown 就
      // stopPropagation，不会进到这里，所以不会和反馈操作打架。
      handleDelightAction(d, "view");
    } else {
      _startDelightAutoAdvance();
    }
    tray.style.removeProperty("--drag-offset");
  });
  tray.addEventListener("pointercancel", () => {
    _delightDragging = false;
    tray.classList.remove("is-dragging");
    tray.style.removeProperty("--drag-offset");
  });

  $root.appendChild(tray);
  if (delights.length > 1) {
    _startDelightAutoAdvance();
    _initDelightVisibilityObserver();
  }
}

let _delightVisibilityObserver = null;
function _initDelightVisibilityObserver() {
  if (_delightVisibilityObserver) return;
  const slot = document.getElementById("delight-slot");
  if (!slot) return;
  _delightVisibilityObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) _startDelightAutoAdvance();
      else _stopDelightAutoAdvance();
    }
  }, { threshold: 0.3 });
  _delightVisibilityObserver.observe(slot);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) _stopDelightAutoAdvance();
    else {
      const rect = slot.getBoundingClientRect();
      if (rect.top < window.innerHeight && rect.bottom > 0) _startDelightAutoAdvance();
    }
  });
}

/** Navigate to a delight index with fade-out/in + height FLIP animation. */
function navigateDelight(newIndex) {
  const slot = document.getElementById("delight-slot");
  if (!slot) return;
  const oldTray = slot.querySelector(".delight-tray");
  if (!oldTray) {
    patchState({ delightCurrentIndex: newIndex });
    rerenderDelightOnly();
    return;
  }
  const oldH = oldTray.offsetHeight;

  // 取消上一次未完成的导航动画
  if (_delightNavTimer) { clearTimeout(_delightNavTimer); _delightNavTimer = null; }

  // 仅文本内容渐入渐出，缩略图和按钮保持不动
  _stopDelightAutoAdvance();
  const textEls = oldTray.querySelectorAll(".delight-title, .delight-stats, .delight-reason, .delight-meta, .delight-kicker-line, .delight-hook-badge");
  textEls.forEach((el) => {
    el.style.transition = "opacity 200ms ease";
    el.style.opacity = "0";
  });

  _delightNavTimer = setTimeout(() => {
    _delightNavTimer = null;
    patchState({ delightCurrentIndex: newIndex });
    slot.innerHTML = "";
    renderInto(slot, renderDelightTray);
    const newTray = slot.querySelector(".delight-tray");
    if (!newTray) return;
    const newTextEls = newTray.querySelectorAll(".delight-title, .delight-stats, .delight-reason, .delight-meta, .delight-kicker-line, .delight-hook-badge");
    const newH = newTray.offsetHeight;

    newTextEls.forEach((el) => { el.style.opacity = "0"; el.style.transition = "none"; });
    newTray.offsetHeight;
    const needsHeight = oldH > 0 && Math.abs(newH - oldH) >= 0.5;
    if (needsHeight) {
      newTray.style.height = `${oldH}px`;
      newTray.classList.add("is-height-animating");
      newTray.offsetHeight;
    }
    const duration = 200;
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min((now - t0) / duration, 1);
      const ease = 1 - (1 - p) * (1 - p);
      if (needsHeight) newTray.style.height = `${oldH + (newH - oldH) * ease}px`;
      const op = Math.min(p * 1.4, 1);
      newTextEls.forEach((el) => { el.style.opacity = `${op}`; });
      if (p < 1) requestAnimationFrame(step);
      else {
        if (needsHeight) { newTray.style.removeProperty("height"); newTray.classList.remove("is-height-animating"); }
        newTextEls.forEach((el) => { el.style.removeProperty("opacity"); el.style.removeProperty("transition"); });
      }
    };
    requestAnimationFrame(step);
  }, 200);
}
function rerenderDelightOnly() {
  const slot = document.getElementById("delight-slot");
  if (!slot) return;
  const oldTray = slot.querySelector(".delight-tray");
  const oldH = oldTray ? oldTray.offsetHeight : 0;
  slot.innerHTML = "";
  renderInto(slot, renderDelightTray);
  const newTray = slot.querySelector(".delight-tray");
  if (newTray && oldH > 0) {
    const newH = newTray.offsetHeight;
    if (Math.abs(newH - oldH) >= 0.5) {
      newTray.style.height = `${oldH}px`;
      newTray.classList.add("is-height-animating");
      newTray.offsetHeight;
      const duration = 200;
      const t0 = performance.now();
      const step = (now) => {
        const p = Math.min((now - t0) / duration, 1);
        const ease = 1 - (1 - p) * (1 - p);
        newTray.style.height = `${oldH + (newH - oldH) * ease}px`;
        if (p < 1) requestAnimationFrame(step);
        else {
          newTray.style.removeProperty("height");
          newTray.classList.remove("is-height-animating");
        }
      };
      requestAnimationFrame(step);
    }
  }
}

function delightUserEngaged() {
  const active = state.activeDelights[state.delightCurrentIndex];
  const normalized = active ? normalizeDelightCandidate(active) : null;
  const input = document.querySelector(".delight-composer-input");
  const focused = document.activeElement === input;
  const draft = String(input?.value || normalized?.draft || "").trim();
  return Boolean(normalized?.composer_open || focused || draft);
}

function _startDelightAutoAdvance() {
  _stopDelightAutoAdvance();
  if (state.activeDelights.length < 2) return;
  _delightAutoTimer = setInterval(() => {
    if (delightUserEngaged()) return;
    const next = state.delightCurrentIndex + 1;
    navigateDelight(next >= state.activeDelights.length ? 0 : next);
  }, DELIGHT_AUTO_ADVANCE_MS);
}

function _stopDelightAutoAdvance() {
  if (_delightAutoTimer !== null) {
    clearInterval(_delightAutoTimer);
    _delightAutoTimer = null;
  }
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
    // Toggle inline composer on the delight — never navigate to chat tab.
    const updated = state.activeDelights.map((item) => {
      const norm = normalizeDelightCandidate(item);
      if (norm.bvid === d.bvid) {
        return { ...item, composer_open: !norm.composer_open };
      }
      return item;
    });
    patchState({ activeDelights: updated });
    rerenderDelightOnly();
    return;
  }

  // "view" / "like" / "reject" — call API with correct token
  if (apiResponse) {
    try {
      await respondToDelight(d.bvid, apiResponse, d.title);
    } catch {
      // 失败不假装成功：like/reject 保持卡片原状（不 mark sent、不移除），
      // 显示重试提示并恢复按钮；其它动作继续 best-effort。
      if (action === "like" || action === "reject") {
        const updated = state.activeDelights.map((item) =>
          (item.bvid || normalizeDelightCandidate(item).bvid) === d.bvid
            ? { ...item, response_message: "\u64CD\u4F5C\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5", response_tone: "error" }
            : item
        );
        patchState({ activeDelights: updated });
        rerenderDelightOnly();
        setTimeout(() => {
          const cur = state.activeDelights[state.delightCurrentIndex];
          if (!cur || normalizeDelightCandidate(cur).bvid !== d.bvid) return;
          const cleared = state.activeDelights.map((item) =>
            (item.bvid || normalizeDelightCandidate(item).bvid) === d.bvid
              ? { ...item, response_message: "", response_tone: "" }
              : item
          );
          patchState({ activeDelights: cleared });
          rerenderDelightOnly();
        }, 3000);
        return;
      }
      /* Other legacy actions remain best-effort. */
    }
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
    if (url) openContentUrl(url);
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

function waitForCoverPreload(promises, timeoutMs) {
  if (promises.length === 0) return Promise.resolve();
  return Promise.race([
    Promise.all(promises),
    new Promise((resolve) => setTimeout(resolve, timeoutMs)),
  ]);
}

function warmRecommendationCovers(
  items,
  { start = 0, limit = COVER_PRELOAD_BATCH_SIZE, waitForDecode = false } = {},
) {
  if (typeof Image === "undefined") return Promise.resolve();
  const urls = getRecommendationCoverPreloadUrls(items, { start, limit });
  const pending = [];
  for (const src of urls) {
    if (warmedCoverUrls.has(src)) continue;
    warmedCoverUrls.add(src);

    const img = new Image();
    const cleanup = () => warmingImages.delete(src);
    const markDecoded = () => { cleanup(); decodedCoverUrls.add(src); };
    const loaded = new Promise((resolve) => {
      img.onload = () => { markDecoded(); resolve(); };
      img.onerror = () => { cleanup(); resolve(); };
    });
    img.decoding = "async";
    img.loading = "eager";
    warmingImages.set(src, img);
    img.src = src;
    let ready = loaded;
    if (typeof img.decode === "function") {
      ready = img.decode().then(markDecoded).catch(cleanup);
    }
    if (waitForDecode) pending.push(ready);
  }
  if (!waitForDecode) return Promise.resolve();
  return waitForCoverPreload(pending, COVER_PRELOAD_WAIT_TIMEOUT_MS);
}

function disconnectScrollPreheatObserver() {
  if (!scrollPreheatObserver) return;
  scrollPreheatObserver.disconnect();
  scrollPreheatObserver = null;
}

function observeScrollPreheat() {
  disconnectScrollPreheatObserver();
  if (!$root || typeof IntersectionObserver === "undefined") return;

  const cards = $root.querySelectorAll(".card");
  if (!cards.length) return;

  scrollPreheatObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      scrollPreheatObserver.unobserve(entry.target);

      // Find this card's index and preheat the next batch ahead
      const allCards = Array.from($root.querySelectorAll(".card"));
      const idx = allCards.indexOf(entry.target);
      if (idx < 0) continue;

      const recs = state.recommendations || [];
      const start = Math.min(idx + 1, recs.length);
      if (start < recs.length) {
        warmRecommendationCovers(recs, { start, limit: SCROLL_PREHEAT_LOOKAHEAD });
      }
    }
  }, {
    root: null,
    rootMargin: SCROLL_PREHEAT_ROOT_MARGIN,
    threshold: 0,
  });

  cards.forEach((card) => scrollPreheatObserver.observe(card));
}

function disconnectAutoAppendObserver() {
  if (!autoAppendObserver) return;
  autoAppendObserver.disconnect();
  autoAppendObserver = null;
}

function maybeAutoAppend() {
  if (!autoAppendVisible || !shouldAutoAppendRecommendations({
    loading, autoAppendExhausted, activeTab: state.activeTab,
    userArmed: autoAppendUserArmed,
  })) return;
  autoAppendUserArmed = false;
  void handleAppend();
}

function observeAutoAppendSentinel() {
  disconnectAutoAppendObserver();
  autoAppendVisible = false;
  if (!$root || typeof IntersectionObserver === "undefined") return;
  const loadMoreRow = $root.querySelector(".load-more-row");
  if (!loadMoreRow) return;
  autoAppendObserver = new IntersectionObserver((entries) => {
    autoAppendVisible = entries.some((entry) => entry.isIntersecting);
    maybeAutoAppend();
  }, {
    root: document.getElementById("app"),
    rootMargin: AUTO_APPEND_ROOT_MARGIN,
    threshold: 0,
  });
  autoAppendObserver.observe(loadMoreRow);
}

function resetAutoAppendIntent() {
  autoAppendUserArmed = false;
  autoAppendTouchY = null;
}

function armAutoAppendIntent() {
  if (state.activeTab !== "recommend") return;
  autoAppendUserArmed = true;
  maybeAutoAppend();
}

function initAutoAppendIntent() {
  if (autoAppendIntentInitialized) return;
  const container = document.getElementById("app");
  if (!container) return;
  autoAppendIntentInitialized = true;

  container.addEventListener("wheel", (event) => {
    if (event.deltaY > 0) armAutoAppendIntent();
  }, { passive: true });

  container.addEventListener("touchstart", (event) => {
    autoAppendTouchY = event.touches?.[0]?.clientY ?? null;
  }, { passive: true });

  container.addEventListener("touchmove", (event) => {
    const y = event.touches?.[0]?.clientY ?? null;
    if (autoAppendTouchY !== null && y !== null && autoAppendTouchY - y > 12) {
      armAutoAppendIntent();
    }
    autoAppendTouchY = y;
  }, { passive: true });

  window.addEventListener("keydown", (event) => {
    if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) {
      armAutoAppendIntent();
    }
  }, { passive: true });
}

// ── Recommendation Card ──────────────────────────────────────
function renderCard(rawItem, index = 0) {
  const item = normalizeRecommendation(rawItem);
  const card = document.createElement("div");
  card.className = "card";
  const url = buildContentUrl(item);
  const cardMedia = getRecommendationCardKind(item);
  if (cardMedia.kind === "text") card.classList.add("is-text-only");
  const imageAttrs = getRecommendationImageLoadingAttrs(index, {
    eagerCount: CARD_EAGER_COVER_COUNT,
  });
  const publishedHtml = publishedTimeHtml(item);

  let coverHtml;
  if (cardMedia.kind === "text") {
    // No-cover text card for text-first sources (X tweet/thread): render
    // the body text instead of a thumbnail — never an <img> node.
    coverHtml = `<div class="card-cover-frame is-text-card"><p class="card-cover-text">${esc(cardMedia.text)}</p></div>`;
  } else {
    const cover = getCoverImageAttrs(cardMedia.coverUrl);
    coverHtml = cover
      ? `<div class="card-cover-frame"><img class="card-cover" src="${esc(cover.src)}" alt="" loading="${esc(imageAttrs.loading)}" fetchpriority="${esc(imageAttrs.fetchPriority)}" decoding="async" onerror="this.parentElement.classList.add('is-error');this.remove()"></div>`
      : `<div class="card-cover-frame is-error"></div>`;
  }

  card.innerHTML = `
    ${coverHtml}
    <div class="card-body">
      <div class="card-title">${esc(item.title)}</div>
      <div class="card-meta">
        <span class="card-source" data-source="${item.source_platform}">${esc(getSourceLabel(item.source_platform))}</span>
        ${item.up_name ? `<span>${esc(item.up_name)}</span>` : ""}
        ${item.topic_label ? `<span style="color:var(--text-muted)">${esc(item.topic_label)}</span>` : ""}
        ${publishedHtml}
      </div>
      ${recommendationStats(item) ? `<div class="card-stats">${esc(recommendationStats(item))}</div>` : ""}
      ${item.expression ? `<div class="card-expression">${esc(item.expression)}</div>` : ""}
    </div>`;

  // Card actions — icon style with feedback state persistence
  const actionsRow = document.createElement("div");
  actionsRow.className = "card-actions";
  actionsRow.addEventListener("click", (e) => e.stopPropagation());

  const alreadyFeedback = feedbackDone.get(item.id);

  const openBtn = createCardAction(
    "打开",
    () => {
      reportClick(buildRecommendationClickPayload(item, url));
      if (url) openContentUrl(url);
    },
    { ariaLabel: "打开", iconHtml: LINK_SVG_ICON, showText: true },
  );

  const likeBtn = createCardAction(
    "",
    async () => {
      likeBtn.disabled = true;
      likeBtn.innerHTML = MORE_SVG_ICON;
      try {
        await submitFeedback(buildFeedbackPayload(item.id, "like"));
        feedbackDone.set(item.id, "like");
        likeBtn.innerHTML = CHECK_SVG_ICON;
      } catch {
        likeBtn.disabled = false;
        likeBtn.innerHTML = THUMBS_UP_SVG_ICON;
      }
    },
    { ariaLabel: "喜欢", iconHtml: alreadyFeedback === "like" ? CHECK_SVG_ICON : THUMBS_UP_SVG_ICON },
  );
  if (alreadyFeedback === "like") likeBtn.disabled = true;

  const dislikeBtn = createCardAction(
    "",
    async () => {
      dislikeBtn.disabled = true;
      dislikeBtn.innerHTML = MORE_SVG_ICON;
      try {
        await submitFeedback(buildFeedbackPayload(item.id, "dislike"));
        feedbackDone.set(item.id, "dislike");
        // Remove the card from the list with a brief fade-out.
        card.style.transition = "opacity 0.3s ease, max-height 0.3s ease";
        card.style.opacity = "0";
        card.style.maxHeight = card.offsetHeight + "px";
        card.style.overflow = "hidden";
        setTimeout(() => {
          card.style.maxHeight = "0";
          card.style.marginBottom = "0";
          card.style.padding = "0";
        }, 150);
        setTimeout(() => {
          card.remove();
          patchState({
            recommendations: state.recommendations.filter((r) => r.id !== item.id),
          });
        }, 450);
      } catch {
        dislikeBtn.disabled = false;
        dislikeBtn.innerHTML = THUMBS_DOWN_SVG_ICON;
      }
    },
    {
      ariaLabel: "不感兴趣",
      iconHtml: alreadyFeedback === "dislike" ? X_SVG_ICON : THUMBS_DOWN_SVG_ICON,
    },
  );
  if (alreadyFeedback === "dislike") dislikeBtn.disabled = true;

  const commentBtn = createCardAction(
    "",
    () => {
      feedbackSheet = { itemId: item.id, note: "", submitState: "idle" };
      renderFeedbackSheet();
    },
    { ariaLabel: "聊一聊", iconHtml: MESSAGE_SVG_ICON },
  );

  const savedItem = savedIdentity(item);
  const savedNow = isSavedLocally("watch_later", savedItem.item_key);
  const starBtn = createCoverChip(
    CLOCK_SVG_ICON,
    "watch-later-btn",
    async () => {
      if (savedMutations.isBusy("watch_later", savedItem.item_key)) return;
      starBtn.disabled = true;
      const wasSaved = isSavedLocally("watch_later", savedItem.item_key);
      announceSavedAction(wasSaved ? "正在从本地稍后再看移除…" : "正在保存到本地稍后再看…");
      // optimistic toggle
      setChipState(starBtn, !wasSaved, wasSaved ? "☆" : "★");
      try {
        await toggleSavedLocally("watch_later", savedItem);
        announceSavedAction(wasSaved ? "已从本地稍后再看移除；平台记录不变。" : "已保存到本地，平台同步状态可在稍后页查看。");
      } catch {
        // revert on failure
        setChipState(starBtn, wasSaved, wasSaved ? "★" : "☆");
        announceSavedAction("本地稍后再看操作失败，请重试。", true);
      } finally {
        starBtn.disabled = false;
      }
    },
    { label: "稍后再看", pressedLabel: "取消稍后再看" },
  );
  setChipState(starBtn, savedNow, savedNow ? "★" : "☆");
  // lazy-load real state from backend
  void hydrateSavedLocally("watch_later", savedItem, (saved) => {
    setChipState(starBtn, saved, saved ? "★" : "☆");
  });

  const favNow = isSavedLocally("favorite", savedItem.item_key);
  const favBtn = createCoverChip(
    STAR_SVG_ICON,
    "favorite-btn",
    async () => {
      if (savedMutations.isBusy("favorite", savedItem.item_key)) return;
      favBtn.disabled = true;
      const wasSaved = isSavedLocally("favorite", savedItem.item_key);
      announceSavedAction(wasSaved ? "正在从本地收藏移除…" : "正在保存到本地收藏…");
      setChipState(favBtn, !wasSaved, wasSaved ? "♡" : "♥");
      try {
        await toggleSavedLocally("favorite", savedItem);
        announceSavedAction(wasSaved ? "已从本地收藏移除；平台记录不变。" : "已保存到本地，平台同步状态可在收藏页查看。");
      } catch {
        setChipState(favBtn, wasSaved, wasSaved ? "♥" : "♡");
        announceSavedAction("本地收藏操作失败，请重试。", true);
      } finally {
        favBtn.disabled = false;
      }
    },
    { label: "收藏", pressedLabel: "取消收藏" },
  );
  setChipState(favBtn, favNow, favNow ? "♥" : "♡");
  void hydrateSavedLocally("favorite", savedItem, (saved) => {
    setChipState(favBtn, saved, saved ? "♥" : "♡");
  });

  // Save chips overlay the cover top-right — keeps the bottom action bar light
  // (看看 / 喜欢 / 不感兴趣 / 聊一聊) instead of cramming 6 buttons in one row.
  const coverFrame = card.querySelector(".card-cover-frame");
  if (coverFrame) {
    const coverActions = document.createElement("div");
    coverActions.className = "cover-actions";
    coverActions.appendChild(starBtn);
    coverActions.appendChild(favBtn);
    coverFrame.appendChild(coverActions);
  }

  actionsRow.appendChild(openBtn);
  actionsRow.appendChild(likeBtn);
  actionsRow.appendChild(dislikeBtn);
  actionsRow.appendChild(commentBtn);
  card.appendChild(actionsRow);

  // Whole card click (except action row)
  if (url) {
    card.style.cursor = "pointer";
    card.addEventListener("click", () => {
      reportClick(buildRecommendationClickPayload(item, url));
      openContentUrl(url);
    });
  }

  return card;
}

function createCardAction(label, handler, { ariaLabel = "", iconHtml = "", showText = false } = {}) {
  const btn = document.createElement("button");
  btn.className = "card-action-btn";
  btn.type = "button";
  if (ariaLabel) {
    btn.setAttribute("aria-label", ariaLabel);
    btn.title = ariaLabel;
  }
  if (iconHtml) {
    btn.innerHTML = `${iconHtml}${showText && label ? `<span>${esc(label)}</span>` : ""}`;
  } else {
    btn.textContent = label;
  }
  btn.addEventListener("click", handler);
  return btn;
}

const LINK_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.1 0l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.1 0l-2 2a5 5 0 0 0 7.1 7.1l1.1-1.1"/></svg>';
const THUMBS_UP_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>';
const THUMBS_DOWN_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg>';
const MESSAGE_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>';
const CHECK_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
const X_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
const MORE_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>';

// 稍后再看 = 时钟（一眼看懂"待会看"）；收藏 = 星星。SVG 图标族统一，
// 选中态由 aria-pressed + CSS 驱动（时钟变色、星星填充），不做字形替换。
const CLOCK_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>';
const STAR_SVG_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>';

function createCoverChip(iconHtml, cls, handler, { label = "", pressedLabel = "" } = {}) {
  const btn = document.createElement("button");
  btn.className = "cover-chip " + cls;
  btn.type = "button";
  btn.innerHTML = iconHtml;
  if (label) {
    btn.dataset.label = label;
    btn.dataset.pressedLabel = pressedLabel || label;
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }
  btn.setAttribute("aria-pressed", "false");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    handler();
  });
  return btn;
}

function setChipState(btn, pressed) {
  btn.setAttribute("aria-pressed", pressed ? "true" : "false");
  const label = pressed ? btn.dataset.pressedLabel : btn.dataset.label;
  if (label) {
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }
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
function applyCommittedPoolStatus(status) {
  if (!status || typeof status.pool_available_count !== "number") return false;
  const version = Number(status.pool_status_version) || 0;
  if (version && version < poolStatusVersion) return true;
  const previousAvailable = Number(state.runtimeStatus?.pool_available_count) || 0;
  poolStatusVersion = Math.max(poolStatusVersion, version);
  runtimeStatusGeneration += 1;
  clearRuntimeStatusRecovery();
  patchState({ runtimeStatus: mergeRuntimeStatusEvent(state.runtimeStatus, status) });
  rerenderRuntimeDependentChrome();
  if (status.pool_available_count > previousAvailable && autoAppendWaitingForRefill) {
    autoAppendWaitingForRefill = false;
    autoAppendExhausted = false;
    autoAppendUserArmed = true;
    maybeAutoAppend();
  }
  return true;
}

async function handleReshuffle() {
  if (loading) return;
  loading = true;
  resetAutoAppendIntent();
  render();
  try {
    const excludedBvids = state.recommendations.map((item) => item?.bvid).filter(Boolean);
    const result = await reshuffleRecommendations(excludedBvids);
    const inventoryApplied = applyCommittedPoolStatus(result.pool_status);
    const replacement = reconcileRecommendationReplacement(
      state.recommendations,
      result.items || [],
    );
    if (replacement.preserved) {
      recommendationActionMessage = "这次暂时没换出新内容，已保留当前推荐。";
      clearRecommendationRecovery("ready");
      autoAppendExhausted = true;
      autoAppendWaitingForRefill = true;
    } else {
      recommendationActionMessage = "";
      applyRecommendationSnapshot(replacement.items, { replace: true });
    }
    const requestGeneration = runtimeStatusGeneration;
    if (!inventoryApplied) void fetchRuntimeStatus()
      .then((status) => applyRuntimeStatusSnapshot(status, requestGeneration))
      .catch(() => {});
  } catch {
    recommendationActionMessage = "换一批失败，请稍后重试。当前推荐已保留。";
  } finally {
    loading = false;
    render();
  }
}

async function handleAppend() {
  if (loading) return;
  loading = true;
  resetAutoAppendIntent();

  // Disable the button inline instead of full re-render.
  const loadMoreRow = $root.querySelector(".load-more-row");
  const appendBtnEl = loadMoreRow?.querySelector("button");
  if (appendBtnEl) { appendBtnEl.disabled = true; appendBtnEl.textContent = "\u52A0\u8F7D\u4E2D\u2026"; }

  try {
    const startIndex = state.recommendations.length;
    const existing = state.recommendations.map((i) => i.bvid).filter(Boolean);
    const result = await appendRecommendations(existing);
    const newItems = (result.items || []).map(normalizeRecommendation);
    // Apply inventory before setting the empty-page latch so this response
    // cannot count as its own refill notification.
    const inventoryApplied = applyCommittedPoolStatus(result.pool_status);
    if (!inventoryApplied) {
      const requestGeneration = runtimeStatusGeneration;
      void fetchRuntimeStatus()
        .then((status) => applyRuntimeStatusSnapshot(status, requestGeneration))
        .catch(() => {});
    }
    autoAppendExhausted = newItems.length === 0 || result.has_more === false;
    autoAppendWaitingForRefill = autoAppendExhausted;
    if (newItems.length > 0 && recommendationActionMessage) {
      recommendationActionMessage = "";
    }
    patchState({ recommendations: [...state.recommendations, ...newItems] });

    // Append new cards before the load-more row without rebuilding existing ones.
    // Do not wait for cover images to decode before inserting: that made both
    // tap-to-load-more and scroll auto-append feel stuck for up to 3 extra
    // seconds after the API returned.
    const currentLoadMoreRow = $root.querySelector(".load-more-row");
    if (currentLoadMoreRow) {
      for (const [offset, item] of newItems.entries()) {
        const card = renderCard(item, startIndex + offset);
        $root.insertBefore(card, currentLoadMoreRow);
        if (scrollPreheatObserver) scrollPreheatObserver.observe(card);
      }
    }

    // Warm only the first few new covers in the background; the tail is
    // loading="lazy" and will be preheated as it approaches the viewport.
    void warmRecommendationCovers(newItems, { limit: CARD_EAGER_COVER_COUNT });
  } catch {
    // Transient failures should not permanently disable auto-append. Show the
    // error in the header and let the next scroll/click retry.
    autoAppendExhausted = false;
    recommendationActionMessage = "\u52A0\u8F7D\u66F4\u591A\u5931\u8D25\uFF0C\u8BF7\u7A0D\u540E\u91CD\u8BD5\u3002";
  }

  loading = false;
  // Inventory events can rebuild the header while loading is true. Always
  // release its reshuffle button when append finishes.
  rerenderHeaderOnly();
  // Restore button state.
  const currentAppendButton = $root.querySelector(".load-more-row button");
  if (currentAppendButton) {
    currentAppendButton.disabled = false;
    const headerState = getMobileRecommendationHeaderState();
    currentAppendButton.textContent = headerState.secondaryActionLabel;
  }
  observeAutoAppendSentinel();
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

// ── Delight Chat Hydration ───────────────────────────────────
/** Fetch durable delight turns and backfill into each delight's turns array. */
async function hydrateDelightTurns() {
  try {
    const payload = await fetchChatTurns({ ...getMobileChatSession("delight"), limit: 200 });
    const items = payload.items || [];
    if (items.length === 0) return;

    // Group turns by subject_id (bvid)
    const byBvid = new Map();
    for (const turn of items) {
      if (!turn.subject_id) continue;
      let arr = byBvid.get(turn.subject_id);
      if (!arr) { arr = []; byBvid.set(turn.subject_id, arr); }
      arr.push({
        turn_id: turn.turn_id,
        message: turn.message || "",
        reply: turn.reply || "",
        status: turn.status || "pending",
        error: turn.error || "",
      });
    }

    // Merge into existing delights
    let changed = false;
    const updated = state.activeDelights.map((item) => {
      const norm = normalizeDelightCandidate(item);
      const serverTurns = byBvid.get(norm.bvid);
      if (!serverTurns) return item;
      changed = true;
      // Merge: keep local turns that aren't on server yet, then overlay server data
      const localIds = new Set((norm.turns || []).map((t) => t.turn_id));
      const merged = serverTurns.map((st) => {
        const local = (norm.turns || []).find((t) => t.turn_id === st.turn_id);
        return local ? { ...local, ...st } : st;
      });
      // Append any local-only turns (optimistic sends not yet on server)
      for (const lt of (norm.turns || [])) {
        if (!serverTurns.some((st) => st.turn_id === lt.turn_id)) merged.push(lt);
      }
      const lastTurn = merged[merged.length - 1];
      const lastReply = lastTurn?.status === "completed" ? lastTurn.reply : norm.chat_reply;
      return { ...item, turns: merged, chat_reply: lastReply || norm.chat_reply };
    });

    if (changed) {
      patchState({ activeDelights: updated });
      rerenderDelightOnly();
    }

    // Resume polling for any pending turns
    for (const turn of items) {
      if (turn.status === "pending" && turn.subject_id) {
        pollDelightTurn(turn.turn_id, turn.subject_id);
      }
    }
  } catch { /* best-effort hydration */ }
}

// ── Load ─────────────────────────────────────────────────────
function rememberRecommendationFeedback(normalizedRecs) {
  for (const rec of normalizedRecs) {
    if (rec.feedback_type && !feedbackDone.has(rec.id)) {
      feedbackDone.set(rec.id, rec.feedback_type);
    }
  }
}

function recommendationEmptyMessage() {
  if (recommendationLoadState === "failed") {
    return "推荐加载失败，正在重试。";
  }
  if (recommendationLoadState === "failed-exhausted") {
    return "推荐加载失败，点一下重新加载。";
  }
  if (runtimeStatusLoadState === "failed") {
    return "库存状态同步失败，正在重试。";
  }
  if (runtimeStatusLoadState === "failed-exhausted") {
    return "库存状态同步失败，点一下重新加载。";
  }
  return getReadyRecommendationHint(state.runtimeStatus).message;
}

function clearRecommendationRecovery(nextState) {
  if (recommendationRecoveryTimer !== null) {
    clearTimeout(recommendationRecoveryTimer);
    recommendationRecoveryTimer = null;
  }
  recommendationRecoveryAttempt = 0;
  recommendationRecoveryPending = false;
  recommendationLoadState = nextState;
}

function clearRuntimeStatusRecovery(nextState = "ready") {
  if (runtimeStatusRecoveryTimer !== null) {
    clearTimeout(runtimeStatusRecoveryTimer);
    runtimeStatusRecoveryTimer = null;
  }
  runtimeStatusRecoveryAttempt = 0;
  runtimeStatusRecoveryPending = false;
  runtimeStatusLoadState = nextState;
}

function applyRecommendationSnapshot(recs, { replace = false } = {}) {
  const normalizedRecs = recs.map(normalizeRecommendation);
  if (normalizedRecs.length > 0) {
    recommendationLoadState = "ready";
    recommendationActionMessage = "";
  } else {
    recommendationLoadState = "empty-success";
  }
  clearRecommendationRecovery(recommendationLoadState);
  if (!replace && state.recommendations.length > 0) return;
  autoAppendExhausted = false;
  autoAppendWaitingForRefill = false;
  resetAutoAppendIntent();
  rememberRecommendationFeedback(normalizedRecs);
  patchState({ recommendations: normalizedRecs });
}

function applyRuntimeStatusSnapshot(status, requestGeneration) {
  if (requestGeneration !== runtimeStatusGeneration) return false;
  if (!status) throw new Error("runtime status unavailable");
  runtimeStatusGeneration += 1;
  clearRuntimeStatusRecovery();
  patchState({ runtimeStatus: normalizeRuntimeStatus(status) });
  rerenderRuntimeDependentChrome();
  return true;
}

function scheduleRecommendationRecovery() {
  if (state.recommendations.length > 0) {
    clearRecommendationRecovery("ready");
    return;
  }
  if (recommendationLoadState !== "failed") return;
  if (recommendationRecoveryInFlight) {
    recommendationRecoveryPending = true;
    return;
  }
  if (recommendationRecoveryTimer !== null) return;
  if (recommendationRecoveryAttempt >= RECOVERY_DELAYS_MS.length) {
    recommendationLoadState = "failed-exhausted";
    render();
    return;
  }
  const delayMs = RECOVERY_DELAYS_MS[recommendationRecoveryAttempt];
  recommendationRecoveryTimer = setTimeout(() => {
    recommendationRecoveryTimer = null;
    recommendationRecoveryAttempt += 1;
    void runRecommendationRecovery();
  }, delayMs);
}

async function runRecommendationRecovery() {
  if (state.recommendations.length > 0) {
    clearRecommendationRecovery("ready");
    return;
  }
  if (recommendationLoadState !== "failed") return;
  if (recommendationRecoveryInFlight) {
    recommendationRecoveryPending = true;
    return;
  }
  recommendationRecoveryInFlight = true;
  try {
    const recs = await fetchRecommendations();
    applyRecommendationSnapshot(recs);
  } catch {
    recommendationLoadState = "failed";
  } finally {
    recommendationRecoveryInFlight = false;
    render();
    if (recommendationRecoveryPending) recommendationRecoveryPending = false;
    scheduleRecommendationRecovery();
  }
}

function scheduleRuntimeStatusRecovery() {
  if (runtimeStatusLoadState !== "failed") return;
  if (runtimeStatusRecoveryInFlight) {
    runtimeStatusRecoveryPending = true;
    return;
  }
  if (runtimeStatusRecoveryTimer !== null) return;
  if (runtimeStatusRecoveryAttempt >= RECOVERY_DELAYS_MS.length) {
    runtimeStatusLoadState = "failed-exhausted";
    render();
    return;
  }
  const delayMs = RECOVERY_DELAYS_MS[runtimeStatusRecoveryAttempt];
  runtimeStatusRecoveryTimer = setTimeout(() => {
    runtimeStatusRecoveryTimer = null;
    runtimeStatusRecoveryAttempt += 1;
    void runRuntimeStatusRecovery();
  }, delayMs);
}

async function runRuntimeStatusRecovery() {
  if (runtimeStatusLoadState !== "failed") return;
  if (runtimeStatusRecoveryInFlight) {
    runtimeStatusRecoveryPending = true;
    return;
  }
  runtimeStatusRecoveryInFlight = true;
  const requestGeneration = runtimeStatusGeneration;
  try {
    applyRuntimeStatusSnapshot(await fetchRuntimeStatus(), requestGeneration);
  } catch {
    if (requestGeneration !== runtimeStatusGeneration) return;
    runtimeStatusLoadState = "failed";
  } finally {
    runtimeStatusRecoveryInFlight = false;
    if (runtimeStatusRecoveryPending) runtimeStatusRecoveryPending = false;
    scheduleRuntimeStatusRecovery();
    rerenderRuntimeDependentChrome();
  }
}

function restartFailedRecoveries() {
  let recommendationRestarted = false;
  let runtimeRestarted = false;
  if (
    state.recommendations.length === 0 &&
    (recommendationLoadState === "failed" || recommendationLoadState === "failed-exhausted")
  ) {
    if (recommendationRecoveryTimer !== null) clearTimeout(recommendationRecoveryTimer);
    recommendationRecoveryTimer = null;
    recommendationRecoveryAttempt = 0;
    recommendationLoadState = "failed";
    scheduleRecommendationRecovery();
    recommendationRestarted = true;
  }
  if (runtimeStatusLoadState === "failed" || runtimeStatusLoadState === "failed-exhausted") {
    if (runtimeStatusRecoveryTimer !== null) clearTimeout(runtimeStatusRecoveryTimer);
    runtimeStatusRecoveryTimer = null;
    runtimeStatusRecoveryAttempt = 0;
    runtimeStatusLoadState = "failed";
    scheduleRuntimeStatusRecovery();
    runtimeRestarted = true;
  }
  if (recommendationRestarted) render();
  else if (runtimeRestarted) rerenderRuntimeDependentChrome();
}

async function loadData() {
  loading = true;
  recommendationLoadState = "loading";
  render();
  try {
    applyRecommendationSnapshot(await fetchRecommendations(), { replace: true });
  } catch {
    recommendationLoadState = "failed";
    scheduleRecommendationRecovery();
  }
  loading = false;
  render();
  void hydrateRecommendSideChannels();
}

function hydrateRecommendSideChannels() {
  if (runtimeStatusLoadState !== "ready") runtimeStatusLoadState = "loading";
  const requestGeneration = runtimeStatusGeneration;
  fetchRuntimeStatus()
    .then((status) => applyRuntimeStatusSnapshot(status, requestGeneration))
    .catch(() => {
      if (requestGeneration !== runtimeStatusGeneration) return;
      runtimeStatusLoadState = "failed";
      scheduleRuntimeStatusRecovery();
      rerenderRuntimeDependentChrome();
    });

  fetchActivityFeed({ limit: 5 })
    .then((activityFeed) => {
      patchState({ activityFeed });
      rerenderHeaderOnly();
    })
    .catch(() => {});

  fetchDelightBatch()
    .then((delights) => {
      patchState({
        activeDelights: delights.map(normalizeDelightCandidate),
        delightCurrentIndex: 0,
      });
      rerenderDelightOnly();
      void hydrateDelightTurns();
    })
    .catch(() => {});
}

// ── Public API ───────────────────────────────────────────────
export function initRecommendView(root) {
  $root = root;
  if (!loaded) {
    loaded = true;
    initPullRefresh();
    initAutoAppendIntent();
    loadData();
  } else {
    restartFailedRecoveries();
  }
  // Tab switch back preserves existing cards. Only failed empty resources
  // start a fresh bounded recovery round.
}

export function onStreamConnect() {
  restartFailedRecoveries();
}

export function onStreamEvent(payload) {
  const type = payload?.type || payload?.event;
  if (type === "refresh.pool_updated") {
    // Merge pool status only. Do not replace recommendation cards here:
    // users may have appended older cards that /api/recommendations would not
    // return in its latest top window.
    const poolEvent = { ...(payload.data || payload), type };
    const version = Number(poolEvent.pool_status_version) || 0;
    if (version && version < poolStatusVersion) return;
    applyCommittedPoolStatus(poolEvent);
    patchState({ runtimeStatus: mergeRuntimeStatusEvent(state.runtimeStatus, poolEvent) });
    if (typeof poolEvent?.pool_available_count === "number") {
      runtimeStatusGeneration += 1;
      clearRuntimeStatusRecovery();
    }
    rerenderRuntimeDependentChrome();
    if (
      state.recommendations.length === 0 &&
      recommendationLoadState === "failed-exhausted"
    ) {
      recommendationRecoveryAttempt = 0;
      recommendationLoadState = "failed";
    }
    if (
      state.recommendations.length === 0 &&
      recommendationLoadState === "failed"
    ) {
      scheduleRecommendationRecovery();
    }
  } else if (type === "refresh.started" || type === "refresh.strategy" || type === "refresh.failed") {
    const event = { ...(payload.data || payload), type };
    runtimeStatusGeneration += 1;
    patchState({ runtimeEvent: event, runtimeStatus: mergeRuntimeStatusEvent(state.runtimeStatus, event) });
    rerenderRuntimeDependentChrome();
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
      // 用户正在惊喜卡的输入框里打字时不重建 DOM——textarea 会失焦、手机
      // 键盘收起（field report 2026-07-05）。draft 已实时存进 state，队列
      // 数据照常更新，用户发送 / 收起后的下一次交互自然刷新。
      const typingInComposer = document.activeElement?.classList?.contains(
        "delight-composer-input",
      );
      if (!typingInComposer) rerenderDelightOnly();
    }
  } else if (type === "delight.liked") {
    // Positive feedback should keep the card visible across clients.
    const data = payload.data || payload;
    const bvid = data?.bvid || data?.domain;
    if (bvid) {
      const updated = state.activeDelights.map((d) =>
        (d.bvid || normalizeDelightCandidate(d).bvid) === bvid
          ? { ...d, state: "liked", response_message: data?.message || "好，这类多来点。" }
          : d
      );
      patchState({ activeDelights: updated });
      rerenderDelightOnly();
    }
  } else if (type === "delight.disliked") {
    // Negative feedback from another client removes this delight locally.
    const bvid = (payload.data || payload)?.bvid || (payload.data || payload)?.domain;
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
