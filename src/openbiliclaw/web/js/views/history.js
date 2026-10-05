/** Bounded content history: clicked, surfaced-but-unopened, and recently removed. */

import {
  fetchContentHistory,
  reportClick,
  saveItem,
} from "../api.js";
import { openContentUrl } from "../app-launch.js";
import { buildContentUrl, getCoverImageAttrs } from "../view-models.js";

const PAGE_SIZE = 12;
const SECTIONS = [
  {
    category: "clicked",
    eyebrow: "Opened",
    title: "主动点开过",
    description: "你明确选择打开的内容，最近一次操作排在前面。",
  },
  {
    category: "shown",
    eyebrow: "Passed by",
    title: "出现过，但没点开",
    description: "曾进入推荐列表、但近 30 天没有打开记录的内容。",
  },
  {
    category: "removed",
    eyebrow: "Recently removed",
    title: "最近移除",
    description: "从保存列表移除、忽略或标记不感兴趣的内容。",
  },
];

const HISTORY_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/></svg>';
const IMAGE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';
const RESTORE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>';

let $root = null;
let refreshGeneration = 0;
let lastRefreshAt = 0;
const categoryState = Object.fromEntries(SECTIONS.map(({ category }) => [
  category,
  {
    items: [],
    total: 0,
    nextCursor: "",
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
    notice: "",
    refreshRequired: false,
  },
]));

function reconcileHistoryPage({
  items = [],
  incomingItems = [],
  incomingTotal = 0,
  nextCursor = "",
  hasMore = false,
  append = false,
}) {
  const current = Array.isArray(items) ? items : [];
  const incoming = Array.isArray(incomingItems) ? incomingItems : [];
  const normalizedTotal = Math.max(0, Number(incomingTotal) || 0);
  const seen = new Set();
  const merged = [];
  const addItem = (item) => {
    const itemKey = String(item?.item_key || "").trim();
    if (!itemKey || seen.has(itemKey)) return;
    seen.add(itemKey);
    merged.push(item);
  };
  if (append) current.forEach(addItem);
  incoming.forEach(addItem);
  const normalizedNextCursor = hasMore ? String(nextCursor || "").trim() : "";
  return {
    items: merged,
    total: normalizedTotal,
    nextCursor: normalizedNextCursor,
    hasMore: Boolean(hasMore && normalizedNextCursor),
  };
}

function esc(value) {
  const span = document.createElement("span");
  span.textContent = value == null ? "" : String(value);
  return span.innerHTML;
}

function platformName(value) {
  const names = {
    bilibili: "B 站", youtube: "YouTube", douyin: "抖音", xiaohongshu: "小红书",
    twitter: "X", github: "GitHub", zhihu: "知乎", reddit: "Reddit", bangumi: "Bangumi",
  };
  return names[String(value || "").toLowerCase()] || String(value || "内容");
}

function eventLabel(item, category) {
  if (category === "clicked") return "点开";
  if (category === "shown") return "出现";
  const labels = {
    watch_later: "从稍后再看移除",
    favorite: "从收藏移除",
    dismiss: "已忽略",
    dislike: "不感兴趣",
  };
  return labels[item.context] || "已移除";
}

function removedContexts(item) {
  const contexts = Array.isArray(item?.contexts) ? item.contexts : [];
  if (contexts.length) {
    return contexts.filter((entry) => entry && typeof entry.context === "string");
  }
  if (item?.context) {
    item.contexts = [{
      context: item.context,
      occurred_at: item.occurred_at,
      restored: item.restored === true,
      restoring: item.restoring === true,
    }];
    return item.contexts;
  }
  return [];
}

function contextLabel(context) {
  return eventLabel({ context }, "removed");
}

function contextRestoreLabel(context) {
  return context === "favorite" ? "重新收藏" : "重新加入稍后";
}

function formatTime(value) {
  const text = String(value || "").trim();
  if (!text) return "时间未知";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
    ? `${text.replace(" ", "T")}Z`
    : text;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function itemUrl(item) {
  return String(item.content_url || buildContentUrl({
    ...item,
    bvid: item.content_id,
  }) || "").trim();
}

function historyCardHtml(item, category, index) {
  const title = String(item.title || item.body_text || "这条内容暂时没有标题").trim();
  const url = itemUrl(item);
  const cover = getCoverImageAttrs(item.cover_url);
  const fallbackMedia = `${IMAGE_ICON}<span class="history-card-fallback-label">${esc(platformName(item.source_platform))}</span>`;
  const media = cover
    ? `<img src="${esc(cover.src)}" alt="${esc(title)} 的封面" loading="lazy" fetchpriority="low" decoding="async" onerror="this.parentElement.classList.add('is-fallback');this.remove()">${fallbackMedia}`
    : fallbackMedia;
  const contexts = category === "removed" ? removedContexts(item) : [];
  const contextsHtml = contexts.length
    ? `<div class="history-contexts" aria-label="移除原因">${contexts.map((entry) => {
      const restorable = ["watch_later", "favorite"].includes(entry.context);
      return `<div class="history-context-row">
        <span class="history-context-copy"><span>${esc(contextLabel(entry.context))}</span><time>${esc(formatTime(entry.occurred_at))}</time></span>
        ${restorable ? `<button class="history-restore" type="button" data-history-restore="${index}" data-history-context="${esc(entry.context)}"${entry.restored ? " disabled" : entry.restoring ? ' aria-disabled="true"' : ""}>${RESTORE_ICON}<span>${entry.restoring ? "恢复中…" : entry.restored ? "已恢复" : contextRestoreLabel(entry.context)}</span></button>` : ""}
      </div>`;
    }).join("")}</div>`
    : "";
  return `
    <article class="history-card" data-history-item-key="${esc(item.item_key)}">
      <button class="history-card-open" type="button" data-history-open="${category}" data-index="${index}"${url ? "" : " disabled"} aria-label="打开：${esc(title)}">
        <span class="history-card-media${cover ? "" : " is-fallback"}">${media}</span>
        <span class="history-card-copy">
          <strong>${esc(title)}</strong>
          <span class="history-card-author">${esc(item.author_name || platformName(item.source_platform))}</span>
          <span class="history-card-meta"><span>${esc(category === "removed" ? `${contexts.length || 1} 项记录` : eventLabel(item, category))}</span><time>${esc(formatTime(item.occurred_at))}</time></span>
        </span>
      </button>
      ${contextsHtml}
      ${item.error ? `<span class="history-card-error" role="alert">${esc(item.error)}</span>` : ""}
    </article>`;
}

function historySectionHtml(section) {
  const page = categoryState[section.category];
  const count = page.loading && page.items.length === 0 ? "读取中" : `${page.total} 条`;
  let body = "";
  if (page.error && page.items.length === 0) {
    body = `<div class="history-empty" role="alert"><p>${esc(page.error)}</p><button class="btn btn-outline" type="button" data-history-retry="${section.category}">重试</button></div>`;
  } else if (page.loading && page.items.length === 0) {
    body = '<div class="history-empty" role="status">正在整理这段历史…</div>';
  } else if (page.items.length === 0) {
    body = '<div class="history-empty">近 30 天还没有这类记录。</div>';
  } else {
    body = `<div class="history-list">${page.items.map((item, index) => historyCardHtml(item, section.category, index)).join("")}</div>`;
  }
  const message = page.items.length && (page.error || page.notice)
    ? `<p class="history-page-message ${page.error ? "is-error" : "is-notice"}" role="${page.error ? "alert" : "status"}">${esc(page.error || page.notice)}</p>`
    : "";
  const refreshingExisting = page.loading && page.items.length > 0;
  const showAction = refreshingExisting
    || page.refreshRequired
    || (page.error && page.items.length)
    || page.hasMore;
  const actionLabel = refreshingExisting
    ? "刷新中…"
    : page.loadingMore
    ? "加载中…"
    : page.refreshRequired
      ? "重试刷新列表"
      : page.error
        ? "重试加载更多"
        : "加载更多";
  const actionAttribute = page.refreshRequired || refreshingExisting
    ? "data-history-retry"
    : "data-history-more";
  const more = showAction
    ? `<button class="history-more btn btn-outline" type="button" ${actionAttribute}="${section.category}"${page.loading || page.loadingMore ? ' aria-disabled="true"' : ""}>${actionLabel}</button>`
    : "";
  return `
    <section class="history-section" data-history-category="${section.category}" aria-labelledby="history-${section.category}-title">
      <div class="history-section-head">
        <div><p class="eyebrow">${esc(section.eyebrow)}</p><h2 id="history-${section.category}-title" tabindex="-1">${esc(section.title)}</h2></div>
        <span class="history-count">${esc(count)}</span>
      </div>
      <p class="history-section-description">${esc(section.description)}</p>
      ${body}${message}${more}
    </section>`;
}

function render() {
  if (!$root) return;
  $root.innerHTML = `
    <div class="history-view">
      <header class="history-head">
        <span class="history-head-icon">${HISTORY_ICON}</span>
        <div><p class="eyebrow">Your trail</p><h1>历史记录</h1><p>只保留近 30 天。封面按需懒加载，不会一次拉取整月图片。</p></div>
        <button class="history-refresh btn btn-outline" type="button" aria-label="刷新历史记录">刷新</button>
      </header>
      <div class="history-sections">${SECTIONS.map(historySectionHtml).join("")}</div>
    </div>`;
  wireInteractions();
}

function historyScrollContainer() {
  return document.getElementById("app") || document.scrollingElement;
}

function historyFocusToken(token) {
  const container = historyScrollContainer();
  return {
    ...token,
    scrollTop: Number(container?.scrollTop) || 0,
  };
}

function restoreHistoryFocus(token, { preferAction = true } = {}) {
  if (!$root || !token) return;
  if (token.action === "refresh") {
    const refresh = $root.querySelector(".history-refresh");
    const container = historyScrollContainer();
    if (container) container.scrollTop = token.scrollTop;
    refresh?.focus({ preventScroll: true });
    if (container) container.scrollTop = token.scrollTop;
    return;
  }
  const section = [...$root.querySelectorAll("[data-history-category]")]
    .find((entry) => entry.dataset.historyCategory === token.category);
  if (!section) return;
  const card = token.itemKey
    ? [...section.querySelectorAll("[data-history-item-key]")]
      .find((entry) => entry.dataset.historyItemKey === token.itemKey)
    : null;
  let target = null;
  if (card && preferAction && token.context) {
    target = [...card.querySelectorAll("[data-history-restore]")].find((button) => (
      button.dataset.historyContext === token.context && !button.disabled
    ));
  }
  if (card && !target) {
    target = card.querySelector("[data-history-restore]:not(:disabled):not([aria-disabled='true'])")
      || card.querySelector("[data-history-open]:not(:disabled)");
  }
  if (!target && token.action) {
    target = section.querySelector(`[data-history-${token.action}]`)
      || section.querySelector("[data-history-more], [data-history-retry]");
  }
  target ||= section.querySelector("h2[tabindex='-1']");
  const container = historyScrollContainer();
  if (container) container.scrollTop = token.scrollTop;
  target?.focus({ preventScroll: true });
  if (container) container.scrollTop = token.scrollTop;
}

function openHistoryItem(category, index) {
  const item = categoryState[category]?.items[index];
  if (!item) return;
  const url = itemUrl(item);
  if (!url) return;
  const clickReport = reportClick({
    recommendation_id: item.recommendation_id,
    bvid: item.content_id,
    content_id: item.content_id,
    content_url: url,
    source_platform: item.source_platform,
    title: item.title,
    up_name: item.author_name,
  });
  if (category === "shown") {
    void clickReport.then((reported) => {
      if (reported) return refreshHistory();
      return undefined;
    });
  }
  openContentUrl(url);
}

async function restoreRemoved(index, contextName) {
  const page = categoryState.removed;
  const item = page.items[index];
  const context = removedContexts(item).find((entry) => entry.context === contextName);
  if (!item || !context || context.restoring || context.restored) return;
  if (!["watch_later", "favorite"].includes(context.context)) return;
  const focusToken = historyFocusToken({
    category: "removed",
    itemKey: String(item.item_key || ""),
    context: context.context,
  });
  context.restoring = true;
  render();
  restoreHistoryFocus(focusToken);
  let restored = false;
  try {
    await saveItem(context.context, item);
    context.restored = true;
    restored = true;
    if (item.context === context.context) item.restored = true;
    item.error = "";
  } catch (error) {
    item.error = error?.message || "恢复失败，请稍后重试。";
  } finally {
    context.restoring = false;
    render();
    restoreHistoryFocus(focusToken, { preferAction: !restored });
  }
}

function wireInteractions() {
  $root?.querySelector(".history-refresh")?.addEventListener("click", () => {
    void refreshHistory(historyFocusToken({ action: "refresh" }));
  });
  $root?.querySelectorAll("[data-history-open]").forEach((button) => {
    button.addEventListener("click", () => {
      openHistoryItem(button.dataset.historyOpen, Number(button.dataset.index));
    });
  });
  $root?.querySelectorAll("[data-history-restore]").forEach((button) => {
    button.addEventListener("click", () => void restoreRemoved(
      Number(button.dataset.historyRestore),
      button.dataset.historyContext,
    ));
  });
  $root?.querySelectorAll("[data-history-more]").forEach((button) => {
    button.addEventListener("click", () => void loadCategory(
      button.dataset.historyMore,
      true,
      refreshGeneration,
      historyFocusToken({ category: button.dataset.historyMore, action: "more" }),
    ));
  });
  $root?.querySelectorAll("[data-history-retry]").forEach((button) => {
    button.addEventListener("click", () => void loadCategory(
      button.dataset.historyRetry,
      false,
      refreshGeneration,
      historyFocusToken({ category: button.dataset.historyRetry, action: "retry" }),
    ));
  });
}

async function loadCategory(category, append, generation = refreshGeneration, focusToken = null) {
  const page = categoryState[category];
  if (!page || page.loading || page.loadingMore) return;
  if (append) page.loadingMore = true;
  else page.loading = true;
  page.error = "";
  page.notice = "";
  page.refreshRequired = false;
  render();
  restoreHistoryFocus(focusToken);
  try {
    const data = await fetchContentHistory(category, PAGE_SIZE, append ? page.nextCursor : "");
    if (generation !== refreshGeneration) return;
    const reconciled = reconcileHistoryPage({
      items: page.items,
      incomingItems: data.items,
      incomingTotal: data.total,
      nextCursor: data.next_cursor,
      hasMore: data.has_more,
      append,
    });
    page.items = reconciled.items;
    page.total = reconciled.total;
    page.nextCursor = reconciled.nextCursor;
    page.hasMore = reconciled.hasMore;
  } catch (error) {
    if (generation !== refreshGeneration) return;
    page.error = error?.message || "历史记录加载失败，请稍后重试。";
  } finally {
    if (generation !== refreshGeneration) return;
    page.loading = false;
    page.loadingMore = false;
    render();
    restoreHistoryFocus(focusToken);
  }
}

async function refreshHistory(focusToken = null) {
  refreshGeneration += 1;
  const generation = refreshGeneration;
  lastRefreshAt = Date.now();
  for (const page of Object.values(categoryState)) {
    page.items = [];
    page.total = 0;
    page.nextCursor = "";
    page.hasMore = false;
    page.loading = false;
    page.loadingMore = false;
    page.error = "";
    page.notice = "";
    page.refreshRequired = false;
  }
  await Promise.allSettled(SECTIONS.map(({ category }) => (
    loadCategory(category, false, generation, focusToken)
  )));
  restoreHistoryFocus(focusToken);
}

export function initHistoryView(root) {
  $root = root;
  render();
  if (Date.now() - lastRefreshAt > 5_000) void refreshHistory();
}
