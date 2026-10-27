/**
 * Tests for the TikTok content-script executor's pure helpers.
 *
 * Module isolation: zero imports from extension/src/content/dy/.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyTiktokScopeCompletion,
  finalizeTiktokTaskStatus,
  isValidTiktokScopeExecuteMessage,
  normalizeTiktokScrollRounds,
  TiktokItemSink,
  ttShouldContinueScroll,
  type TiktokBootstrapItem,
  type TiktokScope,
} from "../src/content/tt/task-executor.ts";

function makeVideoItem(
  scope: TiktokScope,
  videoId: string,
  overrides: Partial<TiktokBootstrapItem> = {},
): TiktokBootstrapItem {
  return {
    scope,
    video_id: videoId,
    url: `https://www.tiktok.com/@creator/video/${videoId}`,
    title: `t-${videoId}`,
    author_handle: "creator",
    author: "creator",
    cover_url: "",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// TiktokItemSink
// ---------------------------------------------------------------------------

test("TiktokItemSink.ingest dedups by (scope, video_id) and returns only NEW items", () => {
  const sink = new TiktokItemSink({ maxItems: 100 });
  const first = sink.ingest([makeVideoItem("tt_like", "a"), makeVideoItem("tt_like", "b")]);
  assert.deepEqual(first.map((i) => i.video_id), ["a", "b"]);
  const second = sink.ingest([makeVideoItem("tt_like", "a"), makeVideoItem("tt_like", "c")]);
  assert.deepEqual(second.map((i) => i.video_id), ["c"]);
  // Same video under a different scope is legitimately new.
  const third = sink.ingest([makeVideoItem("tt_history", "a")]);
  assert.deepEqual(third.map((i) => i.scope), ["tt_history"]);
});

test("TiktokItemSink.ingest dedups follow rows by author handle", () => {
  const sink = new TiktokItemSink({ maxItems: 100 });
  const follow: TiktokBootstrapItem = {
    scope: "tt_follow",
    video_id: "",
    url: "https://www.tiktok.com/@someone",
    title: "someone",
    author_handle: "someone",
    author: "someone",
    creator_sec_uid: "someone",
    cover_url: "",
  };
  sink.ingest([follow]);
  assert.equal(sink.ingest([{ ...follow }]).length, 0);
  // A follow row without a handle has no identity — dropped.
  assert.equal(sink.ingest([{ ...follow, author_handle: "" }]).length, 0);
});

test("TiktokItemSink.ingest drops video rows without a video id", () => {
  const sink = new TiktokItemSink({ maxItems: 100 });
  assert.equal(sink.ingest([makeVideoItem("tt_like", "")]).length, 0);
  assert.equal(sink.count(), 0);
});

test("TiktokItemSink.ingest respects the maxItems cap", () => {
  const sink = new TiktokItemSink({ maxItems: 2 });
  const ingested = sink.ingest([
    makeVideoItem("tt_collect", "a"),
    makeVideoItem("tt_collect", "b"),
    makeVideoItem("tt_collect", "c"),
  ]);
  assert.deepEqual(ingested.map((i) => i.video_id), ["a", "b"]);
  assert.equal(sink.count(), 2);
  assert.equal(sink.snapshot().length, 2);
});

// ---------------------------------------------------------------------------
// Scroll-loop stopping condition
// ---------------------------------------------------------------------------

test("ttShouldContinueScroll stops on cap, round budget, and stagnation", () => {
  assert.equal(
    ttShouldContinueScroll({
      currentCount: 300,
      maxItems: 300,
      round: 1,
      maxScrollRounds: 15,
      stagnantRounds: 0,
      maxStagnantScrollRounds: 5,
    }),
    false,
  );
  assert.equal(
    ttShouldContinueScroll({
      currentCount: 10,
      maxItems: 300,
      round: 15,
      maxScrollRounds: 15,
      stagnantRounds: 0,
      maxStagnantScrollRounds: 5,
    }),
    false,
  );
  assert.equal(
    ttShouldContinueScroll({
      currentCount: 10,
      maxItems: 300,
      round: 3,
      maxScrollRounds: 15,
      stagnantRounds: 5,
      maxStagnantScrollRounds: 5,
    }),
    false,
  );
  assert.equal(
    ttShouldContinueScroll({
      currentCount: 10,
      maxItems: 300,
      round: 3,
      maxScrollRounds: 15,
      stagnantRounds: 1,
      maxStagnantScrollRounds: 5,
    }),
    true,
  );
});

test("normalizeTiktokScrollRounds clamps to the safe 0..30 range", () => {
  assert.equal(normalizeTiktokScrollRounds(15), 15);
  assert.equal(normalizeTiktokScrollRounds(-3), 0);
  assert.equal(normalizeTiktokScrollRounds(99), 30);
  assert.equal(normalizeTiktokScrollRounds(undefined), 0);
  assert.equal(normalizeTiktokScrollRounds(NaN), 0);
});

// ---------------------------------------------------------------------------
// Execute-message validation
// ---------------------------------------------------------------------------

test("isValidTiktokScopeExecuteMessage accepts a full message", () => {
  assert.equal(
    isValidTiktokScopeExecuteMessage({
      task_id: "t1",
      scope: "tt_like",
      max_items: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    true,
  );
});

test("isValidTiktokScopeExecuteMessage rejects malformed input", () => {
  assert.equal(isValidTiktokScopeExecuteMessage(null), false);
  assert.equal(isValidTiktokScopeExecuteMessage({}), false);
  assert.equal(
    isValidTiktokScopeExecuteMessage({
      task_id: "",
      scope: "tt_like",
      max_items: 1,
      max_scroll_rounds: 1,
      max_stagnant_scroll_rounds: 1,
    }),
    false,
  );
  assert.equal(
    isValidTiktokScopeExecuteMessage({
      task_id: "t1",
      scope: "dy_like",
      max_items: 1,
      max_scroll_rounds: 1,
      max_stagnant_scroll_rounds: 1,
    }),
    false,
  );
  assert.equal(
    isValidTiktokScopeExecuteMessage({
      task_id: "t1",
      scope: "tt_like",
      max_items: "300",
      max_scroll_rounds: 1,
      max_stagnant_scroll_rounds: 1,
    }),
    false,
  );
});

// ---------------------------------------------------------------------------
// Completion classification — fail closed, explicit-empty only
// ---------------------------------------------------------------------------

test("classifyTiktokScopeCompletion fails login walls and logged-out tabs explicitly", () => {
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "login_wall",
      itemCount: 0,
      explicitEmpty: false,
      endOfFeed: false,
      navigationFailed: false,
    }),
    { status: "failed", error: "login_wall" },
  );
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "logged_out",
      itemCount: 0,
      explicitEmpty: false,
      endOfFeed: false,
      navigationFailed: false,
    }),
    { status: "failed", error: "logged_out" },
  );
  // Even rows observed under a login wall don't rescue the scope — the
  // session evidence requirement is absolute.
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "login_wall",
      itemCount: 5,
      explicitEmpty: false,
      endOfFeed: true,
      navigationFailed: false,
    }),
    { status: "failed", error: "login_wall" },
  );
});

test("classifyTiktokScopeCompletion returns ok only for terminated non-empty scopes", () => {
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "signed_in",
      itemCount: 12,
      explicitEmpty: false,
      endOfFeed: true,
      navigationFailed: false,
    }),
    { status: "ok" },
  );
  // Rows but no end marker → pagination stayed incomplete → degraded.
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "signed_in",
      itemCount: 12,
      explicitEmpty: false,
      endOfFeed: false,
      navigationFailed: false,
    }),
    { status: "degraded", error: "pagination_incomplete" },
  );
});

test("classifyTiktokScopeCompletion treats only the recognized empty state as empty", () => {
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "signed_in",
      itemCount: 0,
      explicitEmpty: true,
      endOfFeed: false,
      navigationFailed: false,
    }),
    { status: "empty" },
  );
  // Zero rows without a recognized empty state is never a pseudo-empty
  // success — it is an explicit failure.
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "signed_in",
      itemCount: 0,
      explicitEmpty: false,
      endOfFeed: false,
      navigationFailed: false,
    }),
    { status: "failed", error: "unrecognized_empty_state" },
  );
  assert.deepEqual(
    classifyTiktokScopeCompletion({
      accessState: "signed_in",
      itemCount: 0,
      explicitEmpty: false,
      endOfFeed: false,
      navigationFailed: true,
      navigationError: "history_entry_not_found",
    }),
    { status: "failed", error: "history_entry_not_found" },
  );
});

test("finalizeTiktokTaskStatus mirrors the Douyin finalize mapping", () => {
  assert.equal(finalizeTiktokTaskStatus("ok"), "ok");
  assert.equal(finalizeTiktokTaskStatus("empty"), "ok");
  assert.equal(finalizeTiktokTaskStatus("degraded"), "degraded");
  assert.equal(finalizeTiktokTaskStatus("failed"), "failed");
});


test("invalid and oversized execution budgets fail closed", () => {
  const base = { task_id: "task", scope: "tt_like", max_items: 10, max_scroll_rounds: 10, max_stagnant_scroll_rounds: 5 };
  for (const value of [NaN, Infinity, -1, 1.5, 301]) {
    assert.equal(isValidTiktokScopeExecuteMessage({ ...base, max_items: value }), false);
  }
  assert.equal(isValidTiktokScopeExecuteMessage({ ...base, max_scroll_rounds: 31 }), false);
});

test("failed scope navigation cannot be rescued by unrelated rows or empty text", () => {
  assert.deepEqual(classifyTiktokScopeCompletion({
    accessState: "signed_in", itemCount: 0, explicitEmpty: true, endOfFeed: false,
    navigationFailed: true, navigationError: "scope_tab_not_found",
  }), { status: "failed", error: "scope_tab_not_found" });
});
