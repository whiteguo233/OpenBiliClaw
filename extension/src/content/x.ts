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

import { startCollector } from "./kernel.js";
import { twitterAdapter } from "../shared/platforms/twitter.js";
import { registerE2EExecutor } from "./e2e-executor.ts";
import type {
  CapturedXRequest,
  XEngagement,
  XEventType,
} from "../main/x-graphql-tap.js";
import type { BehaviorEvent } from "../shared/types.js";

// Keep CapturedXRequest referenced so the type import survives tree-shaking
// (the tap and this file share the same engagement contract).
export type { CapturedXRequest };

startCollector(twitterAdapter);
registerE2EExecutor("twitter");

/** Map an engagement to the canonical x.com tweet URL (best effort). */
function tweetUrl(engagement: XEngagement): string {
  if (engagement.tweet_id) {
    return `https://x.com/i/status/${engagement.tweet_id}`;
  }
  if (engagement.user_id) {
    return `https://x.com/i/user/${engagement.user_id}`;
  }
  return window.location.href;
}

function buildEvent(engagement: XEngagement): BehaviorEvent {
  const url = tweetUrl(engagement);
  const metadata: Record<string, unknown> = {};
  if (engagement.tweet_id) metadata.tweet_id = engagement.tweet_id;
  if (engagement.user_id) metadata.user_id = engagement.user_id;
  return {
    type: engagement.type as XEventType,
    url,
    title: document.title || "",
    timestamp: Date.now(),
    source_platform: twitterAdapter.sourcePlatform,
    context: {
      pageType: twitterAdapter.detectPageType(window.location.href),
      viewport: { width: window.innerWidth, height: window.innerHeight },
      scrollPosition: window.scrollY,
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

function isXEngagement(value: unknown): value is XEngagement {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.type !== "string") return false;
  const known: readonly string[] = ["like", "favorite", "share", "comment", "view", "follow"];
  if (!known.includes(v.type)) return false;
  // Must carry at least one target id.
  return typeof v.tweet_id === "string" || typeof v.user_id === "string";
}

// ── MAIN-world tap bridge (isolated world receiver) ─────────────────────
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data as { source?: string; engagement?: unknown } | null;
  if (!data || data.source !== "obc-x-tap") return;
  if (!isXEngagement(data.engagement)) return;
  sendEvent(buildEvent(data.engagement));
});

console.log(
  "[OpenBiliClaw] X (Twitter) behavior collector initialized on",
  twitterAdapter.detectPageType(window.location.href),
  "page",
);
