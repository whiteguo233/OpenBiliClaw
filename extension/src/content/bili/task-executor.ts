/**
 * Bilibili rendered-search content-script executor.
 *
 * Reads already-rendered search result cards from search.bilibili.com. The
 * page itself performs Bilibili's signed/WBI search with the user's real
 * browser session; this script only extracts visible metadata.
 */

const CARD_SELECTOR = "[data-testid='bili-video-card'], .bili-video-card, .video-list-item";
const RESULT_WAIT_MS = 8_000;
const RESULT_POLL_MS = 300;

const TITLE_SELECTOR = ".bili-video-card__info--tit, .video-title, h3";
const UP_SELECTOR = ".bili-video-card__info--author, .up-name, [title='up主']";
const STATS_SELECTOR = ".bili-video-card__stats--item, .so-icon.watch-num, .play-text";
const DESC_SELECTOR = ".bili-video-card__info--desc, .des, .description";
const DURATION_SELECTOR = ".bili-video-card__stats__duration, .duration, .so-imgTag_rb";

export interface BiliSearchVideo {
  bvid?: string;
  title: string;
  up_name?: string;
  url: string;
  cover_url?: string;
  duration?: number;
  view_count?: number;
  like_count?: number;
  description?: string;
}

export interface BiliTaskExecuteMessage {
  task_id: string;
  type: "search";
  query?: string;
  limit?: number;
  page_size?: number;
}

export interface BiliTaskResultPayload {
  task_id: string;
  status: "ok" | "empty" | "failed";
  videos: BiliSearchVideo[];
  error?: string;
  debug?: Record<string, unknown>;
}

interface ExecuteDeps {
  document?: Document;
  waitForResults?: (doc: Document) => Promise<boolean>;
}

type ElementLike = Element & {
  href?: string;
  src?: string;
  textContent?: string | null;
};

function textFrom(el: unknown): string {
  const text = (el as { textContent?: unknown } | null)?.textContent;
  return typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
}

function attrFrom(el: unknown, name: string): string {
  const obj = el as { getAttribute?: (name: string) => string | null; href?: unknown; src?: unknown } | null;
  if (!obj) return "";
  if (name === "href" && typeof obj.href === "string") return obj.href;
  if (name === "src" && typeof obj.src === "string") return obj.src;
  if (typeof obj.getAttribute === "function") {
    return obj.getAttribute(name) ?? "";
  }
  return "";
}

function first(root: ParentNode, selector: string): ElementLike | null {
  try {
    return root.querySelector(selector) as ElementLike | null;
  } catch {
    return null;
  }
}

function all(root: ParentNode, selector: string): ElementLike[] {
  try {
    return Array.from(root.querySelectorAll(selector)) as ElementLike[];
  } catch {
    return [];
  }
}

function normalizeUrl(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("//")) return `https:${trimmed}`;
  if (trimmed.startsWith("/video/")) return `https://www.bilibili.com${trimmed}`;
  return trimmed;
}

function imageUrl(img: ElementLike | null): string {
  if (!img) return "";
  return normalizeUrl(
    attrFrom(img, "src") ||
      attrFrom(img, "data-src") ||
      attrFrom(img, "data-lazy-src") ||
      attrFrom(img, "data-original"),
  );
}

export function extractBvid(href: string): string {
  const match = href.match(/BV[0-9A-Za-z]{10}/);
  return match?.[0] ?? "";
}

export function normalizeCountText(text: string): number {
  const cleaned = text.replace(/,/g, "").replace(/\s+/g, "");
  const match = cleaned.match(/([0-9]+(?:\.[0-9]+)?)(万|亿)?/);
  if (!match) return 0;
  const value = Number(match[1]);
  if (!Number.isFinite(value)) return 0;
  const unit = match[2] ?? "";
  if (unit === "亿") return Math.floor(value * 100_000_000);
  if (unit === "万") return Math.floor(value * 10_000);
  return Math.floor(value);
}

function parseDurationSeconds(text: string): number {
  const parts = text.match(/\d+/g);
  if (!parts || parts.length === 0) return 0;
  const nums = parts.map((p) => Number(p));
  if (nums.some((n) => !Number.isFinite(n))) return 0;
  if (nums.length >= 3) return nums[0]! * 3600 + nums[1]! * 60 + nums[2]!;
  if (nums.length === 2) return nums[0]! * 60 + nums[1]!;
  return nums[0]!;
}

function cardTitle(card: ElementLike, anchor: ElementLike): string {
  const titleEl = first(card, TITLE_SELECTOR);
  return (
    attrFrom(titleEl, "title") ||
    textFrom(titleEl) ||
    attrFrom(anchor, "title") ||
    textFrom(anchor)
  );
}

export function extractBiliSearchVideos(
  doc: Document,
  opts: { limit?: number } = {},
): BiliSearchVideo[] {
  const limit = Math.max(0, Math.floor(opts.limit ?? 20));
  const videos: BiliSearchVideo[] = [];
  const seen = new Set<string>();

  for (const card of all(doc, CARD_SELECTOR)) {
    if (videos.length >= limit) break;
    const anchor = first(card, "a[href*='/video/']");
    if (!anchor) continue;

    const url = normalizeUrl(attrFrom(anchor, "href"));
    const bvid = extractBvid(url);
    const title = cardTitle(card, anchor);
    if (!title && !bvid) continue;

    const key = bvid || url || title;
    if (!key || seen.has(key)) continue;
    seen.add(key);

    const statsText = all(card, STATS_SELECTOR)
      .map((el) => textFrom(el))
      .find((text) => normalizeCountText(text) > 0) ?? "";
    const durationText = textFrom(first(card, DURATION_SELECTOR));
    const duration = parseDurationSeconds(durationText);

    const video: BiliSearchVideo = {
      ...(bvid ? { bvid } : {}),
      title,
      ...(textFrom(first(card, UP_SELECTOR)) ? { up_name: textFrom(first(card, UP_SELECTOR)) } : {}),
      url: url || (bvid ? `https://www.bilibili.com/video/${bvid}` : ""),
      ...(imageUrl(first(card, "img")) ? { cover_url: imageUrl(first(card, "img")) } : {}),
      ...(duration > 0 ? { duration } : {}),
      ...(normalizeCountText(statsText) > 0 ? { view_count: normalizeCountText(statsText) } : {}),
      ...(textFrom(first(card, DESC_SELECTOR)) ? { description: textFrom(first(card, DESC_SELECTOR)) } : {}),
    };
    videos.push(video);
  }

  return videos;
}

export function buildBiliTaskResultPayload(
  taskId: string,
  videos: BiliSearchVideo[],
  debug?: Record<string, unknown>,
): BiliTaskResultPayload {
  return {
    task_id: taskId,
    status: videos.length > 0 ? "ok" : "empty",
    videos,
    ...(debug ? { debug } : {}),
  };
}

async function waitForRenderedResults(doc: Document): Promise<boolean> {
  if (all(doc, CARD_SELECTOR).length > 0) return true;
  return new Promise((resolve) => {
    let settled = false;
    let observer: MutationObserver | null = null;
    let interval: ReturnType<typeof setInterval> | null = null;

    const finish = (ready: boolean): void => {
      if (settled) return;
      settled = true;
      observer?.disconnect();
      if (interval !== null) clearInterval(interval);
      resolve(ready);
    };

    try {
      observer = new MutationObserver(() => {
        if (all(doc, CARD_SELECTOR).length > 0) finish(true);
      });
      observer.observe(doc.body ?? doc.documentElement, { childList: true, subtree: true });
    } catch {
      observer = null;
    }

    interval = setInterval(() => {
      if (all(doc, CARD_SELECTOR).length > 0) finish(true);
    }, RESULT_POLL_MS);

    setTimeout(() => {
      finish(all(doc, CARD_SELECTOR).length > 0);
    }, RESULT_WAIT_MS);
  });
}

export async function executeBiliSearch(
  msg: BiliTaskExecuteMessage,
  deps: ExecuteDeps = {},
): Promise<BiliTaskResultPayload> {
  const doc = deps.document ?? document;
  const rendered = await (deps.waitForResults ?? waitForRenderedResults)(doc);
  const videos = extractBiliSearchVideos(doc, { limit: msg.limit ?? msg.page_size ?? 20 });
  return buildBiliTaskResultPayload(msg.task_id, videos, {
    rendered,
    extracted_count: videos.length,
  });
}

export function installBiliMessageListener(): void {
  if (typeof chrome === "undefined" || !chrome.runtime?.onMessage) return;
  chrome.runtime.onMessage.addListener(
    (
      message: { action?: string; data?: BiliTaskExecuteMessage },
      _sender,
      sendResponse,
    ) => {
      if (message.action !== "BILI_TASK_EXECUTE") return false;
      void executeBiliSearch(message.data as BiliTaskExecuteMessage)
        .then((result) => {
          chrome.runtime.sendMessage({ action: "BILI_TASK_RESULT", data: result });
          sendResponse({ ok: true });
        })
        .catch((error: unknown) => {
          const taskId = String(message.data?.task_id ?? "");
          chrome.runtime.sendMessage({
            action: "BILI_TASK_RESULT",
            data: {
              task_id: taskId,
              status: "failed",
              videos: [],
              error: error instanceof Error ? error.message : String(error),
            },
          });
          sendResponse({ ok: false });
        });
      return true;
    },
  );
}
