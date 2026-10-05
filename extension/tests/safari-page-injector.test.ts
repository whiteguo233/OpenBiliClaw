import test from "node:test";
import assert from "node:assert/strict";

import { scriptsForHostname } from "../src/content/safari-page-injector.ts";

test("Safari page injector maps bilibili hosts to the interact tap", () => {
  assert.deepEqual(scriptsForHostname("www.bilibili.com"), [
    "main/bili-interact-tap.js",
  ]);
  assert.deepEqual(scriptsForHostname("bilibili.com"), ["main/bili-interact-tap.js"]);
  assert.deepEqual(scriptsForHostname("passport.bilibili.com"), [
    "main/bili-interact-tap.js",
  ]);
  assert.deepEqual(scriptsForHostname("notbilibili.com"), []);
});

test("Safari page injector maps xiaohongshu to token, state, and action taps", () => {
  assert.deepEqual(scriptsForHostname("www.xiaohongshu.com"), [
    "main/xhs-token-sniffer.js",
    "main/xhs-state-bridge.js",
    "main/xhs-action-tap.js",
  ]);
});

test("Safari page injector handles x/twitter and bangumi aliases", () => {
  assert.deepEqual(scriptsForHostname("x.com"), ["main/x-graphql-tap.js"]);
  assert.deepEqual(scriptsForHostname("twitter.com"), ["main/x-graphql-tap.js"]);
  assert.deepEqual(scriptsForHostname("bangumi.tv"), ["main/bgm-identity-bridge.js"]);
  assert.deepEqual(scriptsForHostname("bgm.tv"), ["main/bgm-identity-bridge.js"]);
});
