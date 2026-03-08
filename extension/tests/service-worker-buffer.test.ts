import test from "node:test";
import assert from "node:assert/strict";

import { shouldFlushImmediately } from "../src/background/buffer.ts";
import type { BehaviorEvent } from "../src/shared/types.ts";

function makeEvent(type: string): BehaviorEvent {
  return {
    type,
    url: "https://www.bilibili.com/video/BV1AB411c7mD",
    title: "示例视频",
    timestamp: Date.now(),
    context: {
      pageType: "video",
      viewport: { width: 1440, height: 900 },
      scrollPosition: 0,
    },
    metadata: {},
  };
}

test("shouldFlushImmediately only promotes strong-signal events", () => {
  assert.equal(shouldFlushImmediately(makeEvent("comment")), true);
  assert.equal(shouldFlushImmediately(makeEvent("coin")), true);
  assert.equal(shouldFlushImmediately(makeEvent("favorite")), true);
  assert.equal(shouldFlushImmediately(makeEvent("search")), false);
  assert.equal(shouldFlushImmediately(makeEvent("scroll")), false);
});
