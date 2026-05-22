/**
 * Tests for the shared SPA-watcher factory.
 *
 * The content scripts on Bilibili/YouTube/etc. all rely on this to
 * detect in-page navigations that swap the URL via pushState. A
 * regression here silently breaks every per-URL feature (YT repost
 * detection, behavior collector page-type changes, etc.).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  createSpaWatcher,
  type SpaWatcherEnv,
} from "../src/shared/spa-watcher.ts";

/**
 * Minimal in-memory environment that mimics the bits of window /
 * history the watcher touches. We use a synchronous scheduler so the
 * test asserts run after the watcher's callback without awaiting.
 */
function makeEnv(initialUrl: string): {
  env: SpaWatcherEnv;
  setUrl: (url: string) => void;
  fireEvent: (type: "popstate") => void;
} {
  let currentUrl = initialUrl;
  const listeners = new Map<string, Set<() => void>>();

  const env: SpaWatcherEnv = {
    history: {
      pushState(_data, _unused, url) {
        if (typeof url === "string") currentUrl = url;
      },
      replaceState(_data, _unused, url) {
        if (typeof url === "string") currentUrl = url;
      },
    },
    addEventListener(type, listener) {
      const bucket = listeners.get(type) ?? new Set();
      bucket.add(listener);
      listeners.set(type, bucket);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    getCurrentUrl: () => currentUrl,
    // Synchronous scheduler so the test sees the callback fire
    // immediately when pushState is called.
    schedule: (cb) => cb(),
  };

  return {
    env,
    setUrl: (u) => { currentUrl = u; },
    fireEvent: (type) => {
      for (const l of listeners.get(type) ?? []) l();
    },
  };
}

test("createSpaWatcher fires onChange once on install with the initial URL", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const calls: string[] = [];
  createSpaWatcher(env, (url) => calls.push(url));
  assert.deepEqual(calls, ["https://www.bilibili.com/video/BV1aaa"]);
});

test("createSpaWatcher fires onChange after pushState to a new URL", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const calls: string[] = [];
  createSpaWatcher(env, (url) => calls.push(url));
  calls.length = 0;
  env.history.pushState({}, "", "https://www.bilibili.com/video/BV1bbb");
  assert.deepEqual(calls, ["https://www.bilibili.com/video/BV1bbb"]);
});

test("createSpaWatcher de-dupes pushState to the same URL", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const calls: string[] = [];
  createSpaWatcher(env, (url) => calls.push(url));
  calls.length = 0;
  // Bilibili sometimes pushes the same URL on tab activation; we
  // shouldn't redo work for that.
  env.history.pushState({}, "", "https://www.bilibili.com/video/BV1aaa");
  assert.deepEqual(calls, []);
});

test("createSpaWatcher fires on replaceState as well", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const calls: string[] = [];
  createSpaWatcher(env, (url) => calls.push(url));
  calls.length = 0;
  env.history.replaceState({}, "", "https://www.bilibili.com/video/BV1ccc");
  assert.deepEqual(calls, ["https://www.bilibili.com/video/BV1ccc"]);
});

test("createSpaWatcher fires on popstate (back/forward)", () => {
  const { env, setUrl, fireEvent } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const calls: string[] = [];
  createSpaWatcher(env, (url) => calls.push(url));
  calls.length = 0;
  // Browser back-button updates the URL FIRST, then fires popstate.
  setUrl("https://www.bilibili.com/video/BV1ddd");
  fireEvent("popstate");
  assert.deepEqual(calls, ["https://www.bilibili.com/video/BV1ddd"]);
});

test("createSpaWatcher.uninstall restores original history methods", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const origPush = env.history.pushState;
  const watcher = createSpaWatcher(env, () => {});
  assert.notEqual(env.history.pushState, origPush, "pushState should be wrapped after install");
  watcher.uninstall();
  assert.equal(env.history.pushState, origPush, "pushState should be restored after uninstall");
});

test("createSpaWatcher: pushState wrapper still updates the URL (does not swallow the call)", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  createSpaWatcher(env, () => {});
  env.history.pushState({}, "", "https://www.bilibili.com/video/BV1eee");
  assert.equal(env.getCurrentUrl(), "https://www.bilibili.com/video/BV1eee");
});

test("createSpaWatcher.notify is a no-op when the URL hasn't changed", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const calls: string[] = [];
  const w = createSpaWatcher(env, (url) => calls.push(url));
  calls.length = 0;
  w.notify();
  w.notify();
  assert.deepEqual(calls, []);
});

test("createSpaWatcher reports the most recent URL via lastUrl", () => {
  const { env } = makeEnv("https://www.bilibili.com/video/BV1aaa");
  const w = createSpaWatcher(env, () => {});
  assert.equal(w.lastUrl, "https://www.bilibili.com/video/BV1aaa");
  env.history.pushState({}, "", "https://www.bilibili.com/video/BV1fff");
  assert.equal(w.lastUrl, "https://www.bilibili.com/video/BV1fff");
});
