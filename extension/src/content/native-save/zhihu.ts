import type { NativeSaveTask } from "../../shared/native-save.ts";
import { waitForNativeSaveReadiness } from "./readiness.ts";

export type ZhihuFavoriteState = "saved" | "not_saved" | null;

export interface ZhihuNativeSaveEnvironment {
  currentUrl: string;
  hasVisibleLoginOverlay(): boolean;
  isUnavailable(): boolean;
  favoriteState(): ZhihuFavoriteState;
  clickFavorite(): void;
  rateLimitFingerprint(): string;
  sleep(ms: number): Promise<void>;
}

const EXACT_TARGET = "知乎收藏";
const TYPED_ID = /^(question|answer|article):([0-9]+)$/;
const CONFIRM_ATTEMPTS = 20;
const CONFIRM_INTERVAL_MS = 100;
const IDENTITY_SELECTOR = [
  "[data-question-id]", "[data-za-question-id]", "[data-zop-questionid]",
  "[data-answer-id]", "[data-za-answer-id]", "[data-zop-answerid]",
  "[data-article-id]", "[data-zop-articleid]", "[data-zop]",
].join(", ");
const RATE_SELECTOR = "[role='alert'], .Toast, .Modal-toast, .Notification";
const RATE_PATTERN = /(?:操作频繁|请求频繁|稍后再试|风险|风控|rate limit|too many requests|risk control|429)/i;

function pageIdentity(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" || url.username || url.password || url.port ||
      url.hash || url.search
    ) return null;
    const host = url.hostname.toLowerCase();
    if (host === "www.zhihu.com" || host === "zhihu.com") {
      const answer = /^\/question\/[0-9]+\/answer\/([0-9]+)\/?$/.exec(url.pathname);
      if (answer) return `answer:${answer[1]}`;
      const question = /^\/question\/([0-9]+)\/?$/.exec(url.pathname);
      if (question) return `question:${question[1]}`;
      const article = /^\/p\/([0-9]+)\/?$/.exec(url.pathname);
      if (article) return `article:${article[1]}`;
    }
    if (host === "zhuanlan.zhihu.com") {
      const article = /^\/p\/([0-9]+)\/?$/.exec(url.pathname);
      if (article) return `article:${article[1]}`;
    }
    return null;
  } catch {
    return null;
  }
}

function pageRouteKey(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" || url.username || url.password || url.port ||
      url.hash || url.search
    ) return null;
    const host = url.hostname.toLowerCase();
    if (host === "www.zhihu.com" || host === "zhihu.com") {
      const answer = /^\/question\/([0-9]+)\/answer\/([0-9]+)\/?$/.exec(url.pathname);
      if (answer) return `answer:${answer[2]}@question:${answer[1]}`;
    }
    const identity = pageIdentity(value);
    return identity ? `${identity}@${host}` : null;
  } catch {
    return null;
  }
}

function isEffectivelyVisible(element: HTMLElement, root: Document): boolean {
  const view = root.defaultView ?? element.ownerDocument?.defaultView;
  let current: HTMLElement | null = element;
  while (current) {
    if (
      current.hidden || current.hasAttribute?.("hidden") || current.hasAttribute?.("inert") ||
      current.getAttribute?.("aria-hidden") === "true" || current.style?.display === "none" ||
      current.style?.visibility === "hidden"
    ) return false;
    if (view) {
      const style = view.getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden") return false;
    }
    current = current.parentElement;
  }
  return true;
}

function identityFromElement(element: HTMLElement): string | null {
  const direct: ReadonlyArray<[string, string]> = [
    ["question", "data-question-id"], ["question", "data-za-question-id"],
    ["question", "data-zop-questionid"], ["answer", "data-answer-id"],
    ["answer", "data-za-answer-id"], ["answer", "data-zop-answerid"],
    ["article", "data-article-id"], ["article", "data-zop-articleid"],
  ];
  for (const [kind, attribute] of direct) {
    const id = element.getAttribute?.(attribute) ?? "";
    if (/^[0-9]+$/.test(id)) return `${kind}:${id}`;
  }
  const raw = element.getAttribute?.("data-zop");
  if (!raw) return null;
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    const kind = String(data.type ?? data.itemType ?? "").toLowerCase();
    const id = String(data.itemId ?? data.id ?? "");
    return /^(?:question|answer|article)$/.test(kind) && /^[0-9]+$/.test(id)
      ? `${kind}:${id}`
      : null;
  } catch {
    return null;
  }
}

function closestIdentity(element: HTMLElement): string | null {
  let current: HTMLElement | null = element;
  while (current) {
    const identity = identityFromElement(current);
    if (identity) return identity;
    current = current.parentElement;
  }
  return null;
}

function visibleText(element: HTMLElement): string {
  return (
    element.getAttribute?.("aria-label") || element.getAttribute?.("title") ||
    element.textContent || ""
  ).replace(/\u200b/g, "").trim();
}

function isSupported(task: NativeSaveTask, currentUrl: string): boolean {
  const match = TYPED_ID.exec(task.content_id);
  if (!match) return false;
  const kind = match[1];
  return task.platform === "zhihu" && task.platform_slug === "zhihu" &&
    task.item_key === `zhihu:${task.content_id}` && task.content_type === kind &&
    pageIdentity(task.content_url) === task.content_id &&
    pageIdentity(currentUrl) === task.content_id &&
    pageRouteKey(task.content_url) === pageRouteKey(currentUrl);
}

function hasTargetContract(task: NativeSaveTask): boolean {
  return task.target_label === EXACT_TARGET && task.resolved_action === "favorite" &&
    (task.requested_action === "favorite" || task.requested_action === "watch_later");
}

function hasNewRateLimit(before: string, after: string): boolean {
  const baseline = new Set(before.split("\n").filter(Boolean));
  return after.split("\n").filter(Boolean).some((event) => !baseline.has(event));
}

async function waitForSaved(env: ZhihuNativeSaveEnvironment): Promise<boolean> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    if (env.favoriteState() === "saved") return true;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

/** Save one exact typed Zhihu item through the current global favorite toggle. */
export async function saveZhihu(
  task: NativeSaveTask,
  env: ZhihuNativeSaveEnvironment = createZhihuBrowserEnvironment(),
): Promise<unknown> {
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const rateBefore = env.rateLimitFingerprint();
  await waitForNativeSaveReadiness(
    () => env.hasVisibleLoginOverlay() || env.isUnavailable() || env.favoriteState() !== null,
    env.sleep,
  );
  if (env.hasVisibleLoginOverlay()) return { status: "login_required" };
  if (env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  const state = env.favoriteState();
  if (state === "saved") return { status: "already_synced" };
  if (state !== "not_saved") {
    return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
      ? { status: "rate_limited" }
      : { status: "failed", error_code: "native_control_not_found" };
  }
  try {
    env.clickFavorite();
  } catch {
    return { status: "failed", error_code: "native_request_rejected" };
  }
  if (await waitForSaved(env)) return { status: "synced" };
  if (env.hasVisibleLoginOverlay()) return { status: "login_required" };
  return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
    ? { status: "rate_limited" }
    : { status: "failed", error_code: "native_confirmation_not_observed" };
}

/** Verify persisted global favorite state after reload without clicking any control. */
export async function verifyZhihu(
  task: NativeSaveTask,
  env: ZhihuNativeSaveEnvironment = createZhihuBrowserEnvironment(),
): Promise<unknown> {
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const rateBefore = env.rateLimitFingerprint();
  await waitForNativeSaveReadiness(
    () => env.hasVisibleLoginOverlay() || env.isUnavailable() || env.favoriteState() !== null,
    env.sleep,
  );
  if (env.hasVisibleLoginOverlay()) return { status: "login_required" };
  if (env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  const state = env.favoriteState();
  if (state === "saved") return { status: "already_synced" };
  if (state === "not_saved") {
    return { status: "failed", error_code: "native_confirmation_not_observed" };
  }
  return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
    ? { status: "rate_limited" }
    : { status: "failed", error_code: "native_control_not_found" };
}

export function createZhihuBrowserEnvironment(
  root: Document = document,
  currentUrl: string = location.href,
): ZhihuNativeSaveEnvironment {
  const currentIdentity = pageIdentity(currentUrl);
  const rateElementIds = new WeakMap<Element, number>();
  let nextRateElementId = 1;
  let activeContainer: HTMLElement | null = null;

  const targetContainer = (): HTMLElement | null => {
    if (!currentIdentity) return null;
    const matches = Array.from(root.querySelectorAll<HTMLElement>(IDENTITY_SELECTOR)).filter(
      (element) => identityFromElement(element) === currentIdentity &&
        isEffectivelyVisible(element, root),
    );
    if (matches.length !== 1) return null;
    return matches[0];
  };

  const favoriteControls = (): HTMLElement[] => {
    const container = targetContainer();
    if (!container || !currentIdentity) return [];
    return Array.from(container.querySelectorAll<HTMLElement>(
      "button, [role='button']",
    )).filter((element) => isEffectivelyVisible(element, root) &&
      closestIdentity(element) === currentIdentity &&
      ["收藏", "已收藏"].includes(visibleText(element)));
  };

  const favoriteState = (): ZhihuFavoriteState => {
    const controls = favoriteControls();
    if (controls.length !== 1) return null;
    return visibleText(controls[0]) === "已收藏" ? "saved" : "not_saved";
  };

  return {
    currentUrl,
    hasVisibleLoginOverlay() {
      return Array.from(root.querySelectorAll<HTMLElement>(
        "[data-testid='login-modal'], .SignFlow, .Modal-wrapper .Login-content, [role='dialog'] form[action*='signin']",
      )).some((element) => isEffectivelyVisible(element, root));
    },
    isUnavailable() {
      return Array.from(root.querySelectorAll<HTMLElement>(
        ".ErrorPage, .NotFound, [data-testid='content-unavailable']",
      )).some((element) => isEffectivelyVisible(element, root) &&
        /(?:不存在|已删除|内容不可用|页面不存在|not found|deleted|unavailable)/i.test(
          element.textContent ?? "",
        ));
    },
    favoriteState,
    clickFavorite() {
      const container = targetContainer();
      const controls = favoriteControls();
      if (!container || controls.length !== 1 || visibleText(controls[0]) !== "收藏") {
        throw new Error("exact unsaved favorite control not found");
      }
      activeContainer = container;
      controls[0].click();
    },
    rateLimitFingerprint() {
      const container = activeContainer ?? targetContainer();
      if (!container) return "";
      const events = Array.from(container.querySelectorAll<HTMLElement>(RATE_SELECTOR)).filter(
        (element) => isEffectivelyVisible(element, root) &&
          (closestIdentity(element) === null || closestIdentity(element) === currentIdentity) &&
          RATE_PATTERN.test(element.textContent ?? ""),
      );
      return events.map((element) => {
        let id = rateElementIds.get(element);
        if (id === undefined) {
          id = nextRateElementId;
          nextRateElementId += 1;
          rateElementIds.set(element, id);
        }
        return `${id}:${(element.textContent ?? "").trim()}`;
      }).sort().join("\n");
    },
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  };
}
