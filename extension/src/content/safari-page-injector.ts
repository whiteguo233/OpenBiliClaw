/**
 * Safari-only page-context bridge injector.
 *
 * Safari Web Extensions have no MAIN-world content scripts and no
 * ``world`` field in ``scripting.executeScript``, so the bundled
 * ``main/*.js`` taps (Bilibili interact, XHS token/action/state, Douyin
 * fetch-tap, X GraphQL, Bangumi identity) cannot wrap the page's own
 * ``window.fetch`` / ``XMLHttpRequest`` / page globals when registered as
 * regular content scripts — they would wrap the isolated world's copies
 * instead, which never see page traffic.
 *
 * This content script runs at ``document_start`` (Safari manifest) and
 * injects those bundles as real ``<script src>`` elements into the page.
 * Because the bundles are listed in ``web_accessible_resources``, they load
 * in the page context and auto-install exactly like their Chrome/Firefox
 * MAIN-world counterparts. The existing isolated-world listeners inside
 * ``content/{bilibili,xiaohongshu,douyin,x,bangumi}.ts`` continue to receive
 * their ``window.postMessage`` streams unchanged.
 *
 * Known best-effort limit: page CSP may block script-tag injection on a few
 * sites, and the async load can miss the very first in-page request. This is
 * still strictly better than the previous Safari behaviour (silently wrapping
 * the isolated world and never capturing anything). Task-driven Douyin
 * re-injection in ``content/douyin.ts`` remains as a second chance.
 */

import { runtimeAssetCandidates } from "../shared/asset-prefix.ts";

type ExtensionRuntime = {
  getURL(path: string): string;
};

type BrowserWithRuntime = {
  browser?: { runtime?: ExtensionRuntime };
  chrome?: { runtime?: ExtensionRuntime };
};

const PAGE_SCRIPTS_BY_HOST: Array<{ hostSuffixes: string[]; scripts: string[] }> = [
  {
    hostSuffixes: ["bilibili.com"],
    scripts: ["main/bili-interact-tap.js"],
  },
  {
    hostSuffixes: ["xiaohongshu.com"],
    scripts: [
      "main/xhs-token-sniffer.js",
      "main/xhs-state-bridge.js",
      "main/xhs-action-tap.js",
    ],
  },
  {
    hostSuffixes: ["douyin.com"],
    scripts: ["main/dy-fetch-tap.js"],
  },
  {
    hostSuffixes: ["x.com", "twitter.com"],
    scripts: ["main/x-graphql-tap.js"],
  },
  {
    hostSuffixes: ["bgm.tv", "bangumi.tv"],
    scripts: ["main/bgm-identity-bridge.js"],
  },
];

const injectedScriptUrls = new Set<string>();

/** Match ``www.bilibili.com`` / ``bilibili.com`` / ``passport.bilibili.com``. */
function hostnameMatches(hostname: string, suffix: string): boolean {
  const normalizedHostname = hostname.toLowerCase();
  const normalizedSuffix = suffix.toLowerCase();
  return (
    normalizedHostname === normalizedSuffix ||
    normalizedHostname.endsWith(`.${normalizedSuffix}`)
  );
}

/** Return the page-context bundles relevant to the current hostname. */
export function scriptsForHostname(hostname: string): string[] {
  const scripts: string[] = [];
  for (const entry of PAGE_SCRIPTS_BY_HOST) {
    if (entry.hostSuffixes.some((suffix) => hostnameMatches(hostname, suffix))) {
      scripts.push(...entry.scripts);
    }
  }
  return [...new Set(scripts)];
}

function getExtensionRuntime(): ExtensionRuntime | null {
  const globalObj = globalThis as typeof globalThis & BrowserWithRuntime;
  const runtime = globalObj.browser?.runtime ?? globalObj.chrome?.runtime;
  return runtime && typeof runtime.getURL === "function" ? runtime : null;
}

function injectCandidate(
  runtime: ExtensionRuntime,
  candidates: string[],
  index: number,
): void {
  const relativePath = candidates[index];
  if (!relativePath) return;

  let url: string;
  try {
    url = runtime.getURL(relativePath);
  } catch {
    injectCandidate(runtime, candidates, index + 1);
    return;
  }

  if (injectedScriptUrls.has(url)) return;

  const script = document.createElement("script");
  script.src = url;
  script.async = false;
  script.dataset.obcSafariPageBridge = relativePath;
  script.onload = () => {
    injectedScriptUrls.add(url);
    script.remove();
  };
  script.onerror = () => {
    script.remove();
    // Fall back to the next layout candidate (e.g. dist/main/... vs main/...).
    injectCandidate(runtime, candidates, index + 1);
  };

  const parent = document.head || document.documentElement;
  if (!parent) {
    // Rare document_start edge case: the document element isn't ready yet.
    // Retry once it exists; content-script listeners are unaffected.
    document.addEventListener(
      "readystatechange",
      () => {
        (document.head || document.documentElement)?.appendChild(script);
      },
      { once: true },
    );
    return;
  }
  parent.appendChild(script);
}

function injectPageScripts(): void {
  const runtime = getExtensionRuntime();
  if (!runtime) {
    console.debug(
      "[OpenBiliClaw] Safari page-context bridge skipped: no extension runtime",
    );
    return;
  }
  const scripts = scriptsForHostname(window.location.hostname);
  for (const relativePath of scripts) {
    injectCandidate(runtime, runtimeAssetCandidates(relativePath), 0);
  }
}

// Auto-run only in a real content-script document context. Guarded so
// node:test can import ``scriptsForHostname`` without a DOM.
if (
  typeof window !== "undefined" &&
  typeof document !== "undefined" &&
  typeof window.location !== "undefined"
) {
  injectPageScripts();
}
