/**
 * Phase 7 E2E (extension side): simulate a Xiaohongshu strong-signal click
 * flowing through the kernel's event-construction path into the background
 * buffer's flush decision.
 *
 * This exercises the real production helpers end-to-end without needing a
 * DOM runtime:
 *   1. `xiaohongshuAdapter.inferActionType` turns a "点赞" label into "like"
 *   2. `createBehaviorEvent` stamps it with `source_platform: "xiaohongshu"`
 *      and the correct note_id metadata
 *   3. `shouldFlushImmediately` recognises it as a strong signal
 *   4. `enqueueBufferedEvent` places it into the buffer alongside bilibili
 *      traffic without dedupe collision, so both sources survive a batch.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  createBehaviorEvent,
} from "../src/shared/behavior.ts";
import { xiaohongshuAdapter } from "../src/shared/platforms/xiaohongshu.ts";
import { bilibiliAdapter } from "../src/shared/platforms/bilibili.ts";
import {
  enqueueBufferedEvent,
  shouldFlushImmediately,
} from "../src/background/buffer.ts";
import type { BehaviorEvent, PlatformAdapter } from "../src/shared/types.ts";

type FakeWindow = Pick<Window, "location" | "innerWidth" | "innerHeight" | "scrollY">;
type FakeDocument = Pick<Document, "title" | "querySelector">;

function fakeWindow(url: string): FakeWindow {
  return {
    location: { href: url } as Location,
    innerWidth: 1440,
    innerHeight: 900,
    scrollY: 0,
  };
}

function fakeDocument(title: string): FakeDocument {
  return {
    title,
    querySelector() {
      return null;
    },
  } as FakeDocument;
}

function buildEvent(
  adapter: PlatformAdapter,
  url: string,
  title: string,
  type: string,
  metadata: Record<string, unknown> = {},
): BehaviorEvent {
  return createBehaviorEvent(
    type,
    fakeWindow(url) as Window,
    fakeDocument(title) as Document,
    adapter,
    metadata,
    { snapshot: false },
  );
}

test("xhs 点赞 click produces a strong-signal event that carries source_platform", () => {
  const actionType = xiaohongshuAdapter.inferActionType({
    text: "点赞",
    ariaLabel: null,
    className: "",
  });
  assert.equal(actionType, "like");

  const url = "https://www.xiaohongshu.com/explore/69dea966000000001a0280ad";
  const event = buildEvent(xiaohongshuAdapter, url, "一篇小红书笔记", "like");

  assert.equal(event.type, "like");
  assert.equal(event.source_platform, "xiaohongshu");
  assert.equal(event.url, url);
  assert.equal(event.metadata.note_id, "69dea966000000001a0280ad");
  assert.equal(event.metadata.content_id, "69dea966000000001a0280ad");

  // Background buffer recognises like as a strong signal → immediate flush.
  assert.equal(shouldFlushImmediately(event), true);
});

test("bilibili and xhs strong signals coexist in the buffer without dedupe collision", () => {
  const bilibiliLike = buildEvent(
    bilibiliAdapter,
    "https://www.bilibili.com/video/BV1AAAAAAAAA",
    "B 站视频",
    "like",
  );
  const xhsLike = buildEvent(
    xiaohongshuAdapter,
    "https://www.xiaohongshu.com/explore/69dea966000000001a0280ad",
    "小红书笔记",
    "like",
  );

  let buffer: BehaviorEvent[] = [];
  buffer = enqueueBufferedEvent(buffer, bilibiliLike, 100);
  buffer = enqueueBufferedEvent(buffer, xhsLike, 100);

  assert.equal(buffer.length, 2);
  const platforms = buffer.map((item) => item.source_platform).sort();
  assert.deepEqual(platforms, ["bilibili", "xiaohongshu"]);

  // Both are strong signals → batched flush should fire on either arrival.
  assert.equal(shouldFlushImmediately(buffer[0]!), true);
  assert.equal(shouldFlushImmediately(buffer[1]!), true);
});

test("xhs non-action clicks remain plain clicks with no forced flush", () => {
  const url = "https://www.xiaohongshu.com/explore/69dea966000000001a0280ad";
  const share = xiaohongshuAdapter.inferActionType({
    text: "更多",
    ariaLabel: null,
    className: "",
  });
  assert.equal(share, null);

  const event = buildEvent(xiaohongshuAdapter, url, "笔记", "click");
  assert.equal(event.type, "click");
  assert.equal(event.source_platform, "xiaohongshu");
  assert.equal(shouldFlushImmediately(event), false);
});
