import test from "node:test";
import assert from "node:assert/strict";

import { enqueueBufferedEvent, shouldFlushImmediately } from "../src/background/buffer.ts";
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
  assert.equal(shouldFlushImmediately(makeEvent("follow")), true);
  assert.equal(shouldFlushImmediately(makeEvent("share")), true);
  assert.equal(shouldFlushImmediately(makeEvent("view")), true);
  assert.equal(shouldFlushImmediately(makeEvent("click")), false);
  assert.equal(shouldFlushImmediately(makeEvent("search")), false);
  assert.equal(shouldFlushImmediately(makeEvent("scroll")), false);
});

test("enqueueBufferedEvent forwards dwell metadata verbatim", () => {
  // v0.3.x event-satisfaction: the buffer must not strip the new
  // watch_seconds / video_duration_seconds keys when the kernel emits a
  // dwell-finalised click. Storage classifies on those exact fields.
  const dwellEvent = makeEvent("click");
  dwellEvent.metadata = {
    watch_seconds: 18,
    video_duration_seconds: 60,
    dwell_source: "video_page_exit",
  };
  const buffer = enqueueBufferedEvent([], dwellEvent, 100);
  assert.equal(buffer.length, 1);
  assert.equal(buffer[0].metadata.watch_seconds, 18);
  assert.equal(buffer[0].metadata.video_duration_seconds, 60);
  assert.equal(buffer[0].metadata.dwell_source, "video_page_exit");
  assert.equal(shouldFlushImmediately(dwellEvent), true);
});
