import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { isTapAuthoritativeAction } from "../src/shared/behavior.ts";
import { twitterAdapter } from "../src/shared/platforms/twitter.ts";
import { bilibiliAdapter } from "../src/shared/platforms/bilibili.ts";
import { redditAdapter } from "../src/shared/platforms/reddit.ts";
import { xiaohongshuAdapter } from "../src/shared/platforms/xiaohongshu.ts";

const kernelSource = readFileSync(
  new URL("../src/content/kernel.ts", import.meta.url),
  "utf8",
);

test("collector observes clicks in capture phase so stopped platform events are still captured", () => {
  assert.match(
    kernelSource,
    /document\.addEventListener\("click",\s*\(event\) => \{[\s\S]*?\},\s*\{\s*capture:\s*true\s*\}\s*\);/,
  );
});

test("click path treats a pressed like/favorite/follow control as a retraction", () => {
  // Clicking an already-active control withdraws the action → emit a
  // neutral retraction feedback event instead of the positive event.
  assert.match(kernelSource, /actionHint\.pressed === true/);
  assert.match(kernelSource, /feedback_type:\s*"retraction"/);
  assert.match(kernelSource, /retracted_action:/);
});

test("DOM dislikes track click parity: even clicks are retractions, not new dislikes (issue 205)", () => {
  // Bilibili dislike controls expose no pressed state, so the kernel tracks
  // per-control click parity: odd = dislike, even = cancellation retraction.
  assert.match(kernelSource, /feedback_type === "dislike"/);
  assert.match(kernelSource, /DISLIKE_TOGGLE_RESET_MS/);
  assert.match(kernelSource, /state\.clicks % 2 === 0/);
  assert.match(kernelSource, /retracted_action: actionType/);
});

test("click path suppresses tap-authoritative actions on both the retraction and positive branches", () => {
  // On X the GraphQL tap emits the authoritative like/favorite/share/comment
  // AND retraction; the DOM path must only suppress, never double-emit — this
  // kills both the positive double-count and the "opened the menu = an event"
  // false actions (codex r2 findings 2/4).
  assert.match(kernelSource, /isTapAuthoritativeAction\(adapter,\s*"retraction"\)/);
  assert.match(kernelSource, /isTapAuthoritativeAction\(adapter,\s*actionType\)/);
});

test("tapAuthoritativeActions suppression matrix: declared actions suppress, others emit", () => {
  // X declares all five engagement actions as tap-authoritative.
  for (const action of ["like", "favorite", "share", "comment", "retraction"]) {
    assert.equal(isTapAuthoritativeAction(twitterAdapter, action), true, action);
  }
  // Non-strong / undeclared actions are never suppressed (still DOM-emitted).
  for (const action of ["view", "scroll", "coin", "hover"]) {
    assert.equal(isTapAuthoritativeAction(twitterAdapter, action), false, action);
  }
});

test("a non-tap platform never suppresses any DOM action", () => {
  // reddit has no MAIN-world tap, so every action flows through the DOM path.
  for (const action of ["like", "favorite", "share", "comment", "retraction", "view"]) {
    assert.equal(isTapAuthoritativeAction(redditAdapter, action), false, action);
  }
});

test("bilibili DOM like/favorite/coin/retraction clicks emit zero events when its tap owns them", () => {
  // The bili-interact-tap emits successful network writes. The kernel's two
  // isTapAuthoritativeAction guards therefore turn both positive action clicks
  // and withdrawals into zero DOM emissions, including Bilibili's class-only
  // pressed controls that do not expose aria-pressed.
  for (const action of ["comment", "like", "favorite", "coin", "retraction"]) {
    assert.equal(isTapAuthoritativeAction(bilibiliAdapter, action), true, action);
  }
  // share / follow still have no Bilibili tap and remain DOM-sourced.
  for (const action of ["share", "follow"]) {
    assert.equal(isTapAuthoritativeAction(bilibiliAdapter, action), false, action);
  }
});

test("xiaohongshu suppresses DOM like/favorite/retraction (its action tap owns them), not comment/share", () => {
  // The xhs-action-tap emits the authoritative like/favorite from the write
  // endpoints and their withdrawals as retractions; the icon-button DOM path
  // must not double-count nor misfire. comment / share have no tap on xhs and
  // still flow through the DOM.
  for (const action of ["like", "favorite", "retraction"]) {
    assert.equal(isTapAuthoritativeAction(xiaohongshuAdapter, action), true, action);
  }
  for (const action of ["comment", "share", "view", "scroll"]) {
    assert.equal(isTapAuthoritativeAction(xiaohongshuAdapter, action), false, action);
  }
});

test("video play begins a dwell segment; pause and ended end it", () => {
  assert.match(kernelSource, /addEventListener\("play",[\s\S]*?beginSegment\(\)/);
  assert.match(kernelSource, /addEventListener\("pause",[\s\S]*?endSegment\(\)/);
  assert.match(kernelSource, /addEventListener\("ended",[\s\S]*?endSegment\(\)/);
});

test("video listeners begin a segment at bind time when the element is already playing", () => {
  assert.match(kernelSource, /!video\.paused && !video\.ended[\s\S]*?beginSegment\(\)/);
});

test("seek events are only emitted shortly after a real pointer/keyboard input", () => {
  // Programmatic seeks (watch-progress restore, episode/part switch) fire
  // `seeked` without a preceding user gesture and must not be recorded.
  assert.match(kernelSource, /lastSeekInputAt\s*=\s*0/);
  assert.match(kernelSource, /addEventListener\(\s*"pointerdown",\s*markSeekInput/);
  assert.match(kernelSource, /addEventListener\(\s*"keydown",\s*markSeekInput/);
  assert.match(kernelSource, /Date\.now\(\)\s*-\s*lastSeekInputAt\s*>\s*1500/);
});

test("late-rendered <video> is retried with a bounded, navigation-cancelled loop", () => {
  assert.match(kernelSource, /_VIDEO_ATTACH_RETRY_MS\s*=\s*500/);
  assert.match(kernelSource, /_VIDEO_ATTACH_MAX_RETRIES\s*=\s*20/);
  assert.match(kernelSource, /cancelVideoAttachRetry\(\)/);
});

test("dwell entry is generalized across tracked page types via dwellPageTypes", () => {
  assert.match(kernelSource, /enterDwellIfTrackedPage/);
  assert.match(kernelSource, /adapter\.dwellPageTypes \?\? \["video"\]/);
});

test("visibilitychange drives visible-mode dwell segments", () => {
  assert.match(kernelSource, /addEventListener\(\s*"visibilitychange"/);
  assert.match(kernelSource, /handleVisibilityChange\(document\.hidden\)/);
});

test("visible-mode entry begins a segment only when the tab is not hidden", () => {
  assert.match(kernelSource, /!document\.hidden/);
});

test("entering a tracked non-video page emits a view event with content_id", () => {
  assert.match(kernelSource, /createEvent\("view"/);
});

test("navigation to a search result page emits a URL-derived search event", () => {
  // Kernel calls the adapter's extractSearchQuery on nav to a search page,
  // routed through the shared dedup guard.
  assert.match(kernelSource, /maybeEmitUrlSearch/);
  assert.match(kernelSource, /extractSearchQuery/);
  assert.match(kernelSource, /isDuplicateSearch/);
});
