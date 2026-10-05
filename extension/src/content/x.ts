/**
 * OpenBiliClaw — X (Twitter) content script entry (isolated world).
 *
 * Injected into x.com / twitter.com pages. Two responsibilities:
 *
 *   1. Wire the generic collector kernel to the twitter adapter
 *      (`startCollector`) for navigation / click / search / scroll
 *      context — same as bilibili / xiaohongshu.
 *
 *   2. Listen for the MAIN-world GraphQL tap's `postMessage`
 *      (`source: "obc-x-tap"`) and forward each captured engagement as a
 *      BEHAVIOR_EVENT to the service worker → backend `/api/events`.
 *
 * The MAIN-world tap (`dist/main/x-graphql-tap.js`) runs at
 * document_start in `world: MAIN` (see manifest.json) and observes the
 * user's own like / bookmark / repost / reply / open-tweet calls. It
 * never mutates the page's requests.
 */

import { twitterAdapter } from "../shared/platforms/twitter.ts";
import { registerE2EExecutor } from "./e2e-executor.ts";
import type {
  CapturedXRequest,
  XEngagement,
  XEventType,
} from "../main/x-graphql-tap.js";
import type { BehaviorEvent } from "../shared/types.js";
import { installNativeSaveExecutor } from "./native-save/runtime.ts";
import { shouldStartPassiveCollector } from "./native-save/task-mode.ts";
import { saveX } from "./native-save/x.ts";
import { COMMENT_TEXT_MAX_CHARS, sanitizeUserText } from "../shared/text-sanitize.ts";

// Keep CapturedXRequest referenced so the type import survives tree-shaking
// (the tap and this file share the same engagement contract).
export type { CapturedXRequest };

// Strength recorded for a retraction — matches the backend's 0.2 default.
const RETRACTION_SIGNAL_STRENGTH = 0.2;

/** Best-effort current page href (safe under node --test where window is absent). */
function currentHref(): string {
  return typeof window !== "undefined" ? window.location.href : "";
}

/** Map an engagement to the canonical x.com tweet URL (best effort). */
function tweetUrl(engagement: XEngagement): string {
  if (engagement.tweet_id) {
    return `https://x.com/i/status/${engagement.tweet_id}`;
  }
  if (engagement.user_id) {
    return `https://x.com/i/user/${engagement.user_id}`;
  }
  return currentHref();
}

/**
 * Normalize an XEngagement into the unified BehaviorEvent forwarded to
 * `/api/events`. A `retraction` (unlike / unbookmark / unretweet) becomes a
 * neutral `feedback` event carrying `feedback_type="retraction"`, the
 * withdrawn action, and an explicit 0.2 signal strength; every other
 * engagement is passed through unchanged. Pure — no side effects.
 */
export function buildEventFromEngagement(engagement: XEngagement): BehaviorEvent {
  const url = tweetUrl(engagement);
  const metadata: Record<string, unknown> = {};
  if (engagement.tweet_id) metadata.tweet_id = engagement.tweet_id;
  if (engagement.user_id) metadata.user_id = engagement.user_id;

  let type: string = engagement.type as XEventType;
  if (engagement.type === "retraction") {
    type = "feedback";
    metadata.feedback_type = "retraction";
    if (engagement.retracted_action) {
      metadata.retracted_action = engagement.retracted_action;
    }
    metadata.signal_strength = RETRACTION_SIGNAL_STRENGTH;
  } else if (engagement.type === "comment" && typeof engagement.text === "string") {
    // Extension-side sanitization (first layer); the backend repeats it.
    const cleaned = sanitizeUserText(engagement.text, COMMENT_TEXT_MAX_CHARS);
    if (cleaned) {
      metadata.comment_text = cleaned;
      metadata.comment_kind = "comment";
    }
  }

  const href = currentHref();
  const hasWindow = typeof window !== "undefined";
  const hasDocument = typeof document !== "undefined";
  return {
    type,
    url,
    title: hasDocument ? document.title || "" : "",
    timestamp: Date.now(),
    source_platform: twitterAdapter.sourcePlatform,
    context: {
      pageType: twitterAdapter.detectPageType(href),
      viewport: {
        width: hasWindow ? window.innerWidth : 0,
        height: hasWindow ? window.innerHeight : 0,
      },
      scrollPosition: hasWindow ? window.scrollY : 0,
    },
    metadata,
  };
}

function sendEvent(event: BehaviorEvent): void {
  try {
    chrome.runtime.sendMessage({ action: "BEHAVIOR_EVENT", data: event });
  } catch {
    // best effort — never break the page
  }
}

export function isXEngagement(value: unknown): value is XEngagement {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.type !== "string") return false;
  const known: readonly string[] = [
    "like",
    "favorite",
    "share",
    "comment",
    "view",
    "follow",
    "retraction",
  ];
  if (!known.includes(v.type)) return false;
  // Must carry at least one target id.
  return typeof v.tweet_id === "string" || typeof v.user_id === "string";
}

// ── MAIN-world tap bridge (isolated world receiver) ─────────────────────
// Side effects (collector kernel, E2E executor, message bridge) run only in
// a real browser; guarded so this module imports cleanly under node --test.
if (typeof window !== "undefined" && typeof chrome !== "undefined") {
  // Dynamic import so node:test's static analysis doesn't pull the DOM-heavy
  // kernel graph into this module (mirrors content/douyin.ts).
  void shouldStartPassiveCollector().then((shouldStart) => {
    if (!shouldStart) return;
    void import("./kernel.js").then(({ startCollector }) => {
      startCollector(twitterAdapter);
    });
  });
  registerE2EExecutor("twitter");
  installNativeSaveExecutor("twitter", saveX);

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data as { source?: string; engagement?: unknown } | null;
    if (!data || data.source !== "obc-x-tap") return;
    if (!isXEngagement(data.engagement)) return;
    sendEvent(buildEventFromEngagement(data.engagement));
  });

  console.log(
    "[OpenBiliClaw] X (Twitter) behavior collector initialized on",
    twitterAdapter.detectPageType(window.location.href),
    "page",
  );
}
