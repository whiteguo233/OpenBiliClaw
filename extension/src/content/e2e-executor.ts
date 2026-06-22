import {
  E2E_STATE_CHANGING_ACTIONS,
  type E2EAction,
  type E2EActionExecutionResult,
  type E2EContentExecuteMessage,
  type E2EPlatform,
} from "../shared/e2e.ts";

type E2EContentExecutionResponse = {
  status: "ok" | "failed";
  actions: E2EActionExecutionResult[];
  error?: string;
};

type ElementRect = Pick<DOMRect, "width" | "height" | "top" | "left" | "bottom" | "right">;

interface ClickableElement {
  textContent?: string | null;
  click?: () => void;
  dispatchEvent?: (event: Event) => boolean;
  getAttribute?: (name: string) => string | null;
  getBoundingClientRect?: () => ElementRect;
  matches?: (selector: string) => boolean;
  parentElement?: ClickableElement | null;
  scrollIntoView?: (options?: ScrollIntoViewOptions) => void;
  scrollBy?: (options: ScrollToOptions) => void;
  scrollTop?: number;
  scrollHeight?: number;
  clientHeight?: number;
  disabled?: boolean;
}

interface QueryDocument {
  querySelectorAll: (selector: string) => Iterable<ClickableElement> | ArrayLike<ClickableElement>;
}

interface ScrollWindow {
  innerHeight: number;
  scrollBy: (options: ScrollToOptions) => void;
  dispatchEvent?: (event: Event) => boolean;
}

export interface E2EExecutorEnv {
  document?: QueryDocument;
  window?: ScrollWindow;
  sleep?: (ms: number) => Promise<void>;
}

interface PlatformRecipe {
  clickSelectors: readonly string[];
  actionSelectors?: Partial<Record<E2EAction, readonly string[]>>;
  textActions: Partial<Record<E2EAction, readonly RegExp[]>>;
}

const SNAPSHOT_WAIT_MS = 150;
const SCROLL_WAIT_MS = 300;
const CLICK_WAIT_MS = 800;
const TARGET_WAIT_MS = 8_000;
const TARGET_RETRY_MS = 500;
const TEXT_TARGET_SELECTOR = 'button, [role="button"], a, div, span';
const BUTTON_CONTROL_SELECTOR = 'button, [role="button"]';

const KNOWN_ACTIONS = new Set<E2EAction>([
  "snapshot",
  "scroll",
  "click",
  "like",
  "favorite",
  "share",
  "follow",
  "repost",
  "bookmark",
]);

const RECIPES: Record<E2EPlatform, PlatformRecipe> = {
  douyin: {
    clickSelectors: ['[data-e2e="feed-active-video"]', "video", 'a[href*="/video/"]'],
    textActions: {
      share: [/share/i, /分享/],
      like: [/like/i, /赞/],
      favorite: [/favorite/i, /收藏/],
      follow: [/follow/i, /关注/],
    },
  },
  xiaohongshu: {
    clickSelectors: ['a[href*="/explore/"]', 'a[href*="/discovery/item/"]', ".note-item"],
    actionSelectors: {
      share: [
        '[aria-label*="分享"]',
        '[title*="分享"]',
        '[class*="share" i]',
      ],
    },
    textActions: {
      share: [/share/i, /分享/],
      like: [/like/i, /赞/],
      favorite: [/collect/i, /收藏/],
      follow: [/follow/i, /关注/],
    },
  },
  twitter: {
    clickSelectors: ['article a[href*="/status/"]', 'article[data-testid="tweet"]', '[data-testid="tweet"]'],
    actionSelectors: {
      share: [
        '[data-testid="share"]',
        '[aria-label*="Share" i]',
      ],
    },
    textActions: {
      share: [/share/i],
      repost: [/repost/i, /retweet/i],
      like: [/like/i],
      favorite: [/bookmark/i],
      bookmark: [/bookmark/i],
      follow: [/follow/i],
    },
  },
};

const ACTIVE_STATE_LABELS: Partial<Record<E2EAction, readonly RegExp[]>> = {
  like: [
    /\bunlike\b/i,
    /\bliked\b/i,
    /已赞/,
    /已点赞/,
    /取消赞/,
    /取消点赞/,
  ],
  favorite: [
    /\bunfavorite\b/i,
    /\bfavorited\b/i,
    /\bremove\s+favorite\b/i,
    /\bremove\s+bookmark\b/i,
    /\bbookmarked\b/i,
    /已收藏/,
    /取消收藏/,
  ],
  follow: [
    /\bfollowing\b/i,
    /\bunfollow\b/i,
    /已关注/,
    /取消关注/,
  ],
  repost: [
    /\bundo\s+repost\b/i,
    /\bretweeted\b/i,
    /\breposted\b/i,
    /已转发/,
    /取消转发/,
  ],
  bookmark: [
    /\bremove\s+bookmark\b/i,
    /\bbookmarked\b/i,
    /已收藏/,
    /取消收藏/,
  ],
};

const registeredExecutorTargets = new WeakMap<object, Set<E2EPlatform>>();

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function getEnv(env?: E2EExecutorEnv): Required<E2EExecutorEnv> {
  const fallbackDocument: QueryDocument = {
    querySelectorAll: () => [],
  };
  const fallbackWindow: ScrollWindow = {
    innerHeight: 0,
    scrollBy: () => {
      throw new Error("window unavailable");
    },
  };
  return {
    document: env?.document ?? (typeof document === "undefined" ? fallbackDocument : document),
    window: env?.window ?? (typeof window === "undefined" ? fallbackWindow : window),
    sleep: env?.sleep ?? defaultSleep,
  };
}

function getComputedVisibilityStyle(element: ClickableElement): Pick<
  CSSStyleDeclaration,
  "display" | "visibility" | "pointerEvents" | "opacity"
> | null {
  const getter = typeof getComputedStyle === "undefined" ? undefined : getComputedStyle;
  if (!getter) return null;

  try {
    return getter(element as Element);
  } catch {
    return null;
  }
}

function hasDisabledState(element: ClickableElement): boolean {
  const disabledAttr = element.getAttribute?.("disabled");
  const ariaDisabled = element.getAttribute?.("aria-disabled");
  return (
    element.disabled === true ||
    (disabledAttr !== undefined && disabledAttr !== null) ||
    ariaDisabled?.toLowerCase() === "true"
  );
}

function isVisible(element: ClickableElement, viewportHeight: number): boolean {
  const rect = element.getBoundingClientRect?.();
  if (!rect) return false;
  if (rect.width <= 0 || rect.height <= 0) return false;
  if (rect.bottom <= 0 || rect.top >= viewportHeight + 400) return false;
  if (hasDisabledState(element)) return false;

  const style = getComputedVisibilityStyle(element);
  if (!style) return true;
  if (style.display === "none") return false;
  if (style.visibility === "hidden" || style.visibility === "collapse") return false;
  if (style.pointerEvents === "none") return false;

  const opacity = Number.parseFloat(style.opacity);
  return !Number.isFinite(opacity) || opacity > 0;
}

function matchesSelector(element: ClickableElement, selector: string): boolean {
  try {
    return element.matches?.(selector) === true;
  } catch {
    return false;
  }
}

function hasContentSemanticsBeforeControl(
  target: ClickableElement,
  control: ClickableElement,
  contentSelectors: readonly string[],
): boolean {
  let current: ClickableElement | null | undefined = target;
  while (current) {
    const candidate: ClickableElement = current;
    if (contentSelectors.some((selector) => matchesSelector(candidate, selector))) return true;
    if (candidate === control) return false;
    current = candidate.parentElement;
  }
  return false;
}

function isUnsafeClickControl(
  target: ClickableElement,
  contentSelectors: readonly string[],
): boolean {
  let current: ClickableElement | null | undefined = target;
  while (current) {
    if (matchesSelector(current, BUTTON_CONTROL_SELECTOR)) {
      return !hasContentSemanticsBeforeControl(target, current, contentSelectors);
    }
    current = current.parentElement;
  }
  return false;
}

function queryClickTarget(
  documentLike: QueryDocument,
  selectors: readonly string[],
  viewportHeight: number,
): ClickableElement | null {
  for (const selector of selectors) {
    const elements = Array.from(documentLike.querySelectorAll(selector));
    const target = elements.find((element) => (
      isVisible(element, viewportHeight) &&
      !isUnsafeClickControl(element, selectors)
    ));
    if (target) return target;
  }
  return null;
}

function queryActionSelectorTarget(
  documentLike: QueryDocument,
  selectors: readonly string[] | undefined,
  viewportHeight: number,
): ClickableElement | null {
  if (!selectors || selectors.length === 0) return null;
  for (const selector of selectors) {
    const elements = Array.from(documentLike.querySelectorAll(selector));
    const target = elements.find((element) => isVisible(element, viewportHeight));
    if (target) return target;
  }
  return null;
}

function elementLabel(element: ClickableElement): string {
  return [
    element.getAttribute?.("aria-label"),
    element.getAttribute?.("title"),
    element.textContent,
  ]
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .trim();
}

function findTextTarget(
  documentLike: QueryDocument,
  patterns: readonly RegExp[],
  action: E2EAction,
  viewportHeight: number,
): ClickableElement | null {
  const activePatterns = ACTIVE_STATE_LABELS[action] ?? [];
  const elements = Array.from(documentLike.querySelectorAll(TEXT_TARGET_SELECTOR));
  return elements.find((element) => {
    if (!isVisible(element, viewportHeight)) return false;
    const label = elementLabel(element);
    return (
      patterns.some((pattern) => pattern.test(label)) &&
      !activePatterns.some((pattern) => pattern.test(label))
    );
  }) ?? null;
}

function dispatchSyntheticClick(element: ClickableElement): void {
  const event = typeof MouseEvent === "undefined"
    ? new Event("click", { bubbles: true, cancelable: true })
    : new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      view: window,
    });
  element.dispatchEvent?.(event);
}

function clickElement(element: ClickableElement, forceSyntheticEvent = false): void {
  element.scrollIntoView?.({ block: "center", inline: "center", behavior: "smooth" });
  if (typeof element.click === "function") {
    element.click();
    if (forceSyntheticEvent) {
      dispatchSyntheticClick(element);
    }
    return;
  }
  dispatchSyntheticClick(element);
}

function queryScrollableTarget(
  documentLike: QueryDocument,
  viewportHeight: number,
): ClickableElement | null {
  const elements = Array.from(documentLike.querySelectorAll("*"));
  let best: { element: ClickableElement; score: number } | null = null;

  for (const element of elements) {
    if (!isVisible(element, viewportHeight)) continue;
    const scrollHeight = element.scrollHeight ?? 0;
    const clientHeight = element.clientHeight ?? 0;
    if (clientHeight < 120 || scrollHeight <= clientHeight + 120) continue;

    const rect = element.getBoundingClientRect?.();
    const visibleHeight = rect
      ? Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, 0)
      : 0;
    const score = Math.max(0, visibleHeight) + Math.min(scrollHeight - clientHeight, viewportHeight);
    if (!best || score > best.score) {
      best = { element, score };
    }
  }

  return best?.element ?? null;
}

function dispatchScrollEvent(target: { dispatchEvent?: (event: Event) => boolean }): void {
  if (typeof Event === "undefined") return;
  target.dispatchEvent?.(new Event("scroll", { bubbles: false, cancelable: false }));
}

function performScroll(runtime: Required<E2EExecutorEnv>): void {
  const amount = Math.max(300, runtime.window.innerHeight * 0.75);
  const target = queryScrollableTarget(runtime.document, runtime.window.innerHeight || 800);

  if (target) {
    const before = target.scrollTop ?? 0;
    if (typeof target.scrollBy === "function") {
      target.scrollBy({ top: amount, behavior: "smooth" });
    } else {
      target.scrollTop = before + amount;
    }
    dispatchScrollEvent(target);
    return;
  }

  runtime.window.scrollBy({
    top: amount,
    behavior: "smooth",
  });
  dispatchScrollEvent(runtime.window);
}

function findActionTarget(
  runtime: Required<E2EExecutorEnv>,
  recipe: PlatformRecipe,
  action: E2EAction,
): ClickableElement | null {
  if (action === "click") {
    return queryClickTarget(runtime.document, recipe.clickSelectors, runtime.window.innerHeight);
  }

  return queryActionSelectorTarget(
    runtime.document,
    recipe.actionSelectors?.[action],
    runtime.window.innerHeight,
  ) ?? findTextTarget(
    runtime.document,
    recipe.textActions[action] ?? [],
    action,
    runtime.window.innerHeight,
  );
}

async function waitForActionTarget(
  runtime: Required<E2EExecutorEnv>,
  recipe: PlatformRecipe,
  action: E2EAction,
): Promise<ClickableElement | null> {
  let waitedMs = 0;
  do {
    const target = findActionTarget(runtime, recipe, action);
    if (target) return target;
    await runtime.sleep(TARGET_RETRY_MS);
    waitedMs += TARGET_RETRY_MS;
  } while (waitedMs < TARGET_WAIT_MS);

  return null;
}

function failedResult(action: E2EAction, detail: string, error?: string): E2EActionExecutionResult {
  return {
    action,
    status: "failed",
    detail,
    ...(error ? { error } : {}),
  };
}

function isExecuteMessage(value: unknown, platform: E2EPlatform): value is E2EContentExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as Partial<E2EContentExecuteMessage>;
  return (
    message.action === "OBC_E2E_EXECUTE" &&
    message.platform === platform &&
    typeof message.runId === "string" &&
    message.runId.length > 0 &&
    Array.isArray(message.actions) &&
    message.actions.every((action) => KNOWN_ACTIONS.has(action)) &&
    typeof message.allowStateChanging === "boolean"
  );
}

export async function executeAction(
  platform: E2EPlatform,
  action: E2EAction,
  allowStateChanging: boolean,
  env?: E2EExecutorEnv,
): Promise<E2EActionExecutionResult> {
  try {
    const runtime = getEnv(env);
    if (action === "snapshot") {
      await runtime.sleep(SNAPSHOT_WAIT_MS);
      return { action, status: "ok", detail: "snapshot", executed: true };
    }

    if (action === "scroll") {
      performScroll(runtime);
      await runtime.sleep(SCROLL_WAIT_MS);
      return { action, status: "ok", detail: "scrolled" };
    }

    if (E2E_STATE_CHANGING_ACTIONS.has(action) && !allowStateChanging) {
      return {
        action,
        status: "skipped",
        detail: "state_changing_action_blocked",
      };
    }

    const recipe = RECIPES[platform];
    const target = await waitForActionTarget(runtime, recipe, action);

    if (!target) {
      return failedResult(action, "target_not_found");
    }

    clickElement(target, action === "share");
    await runtime.sleep(CLICK_WAIT_MS);
    return { action, status: "ok", detail: "clicked" };
  } catch (error) {
    return failedResult(
      action,
      "execution_error",
      error instanceof Error ? error.message : String(error),
    );
  }
}

async function executeActions(message: E2EContentExecuteMessage): Promise<E2EContentExecutionResponse> {
  const actions: E2EActionExecutionResult[] = [];
  for (const action of message.actions) {
    actions.push(await executeAction(message.platform, action, message.allowStateChanging));
  }
  const failed = actions.find((result) => result.status === "failed");
  return failed
    ? { status: "failed", actions, error: failed.error ?? failed.detail }
    : { status: "ok", actions };
}

export function registerE2EExecutor(platform: E2EPlatform): void {
  if (typeof chrome === "undefined" || !chrome.runtime?.onMessage) return;

  const onMessage = chrome.runtime.onMessage;
  const registeredPlatforms = registeredExecutorTargets.get(onMessage) ?? new Set<E2EPlatform>();
  if (registeredPlatforms.has(platform)) return;
  registeredPlatforms.add(platform);
  registeredExecutorTargets.set(onMessage, registeredPlatforms);

  onMessage.addListener((message: unknown, _sender, sendResponse) => {
    if (!isExecuteMessage(message, platform)) return false;

    void executeActions(message)
      .then(sendResponse)
      .catch((error: unknown) => {
        sendResponse({
          status: "failed",
          actions: [],
          error: error instanceof Error ? error.message : String(error),
        });
      });

    return true;
  });
}
