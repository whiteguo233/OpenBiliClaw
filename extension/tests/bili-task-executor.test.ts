/**
 * Tests for the Bilibili rendered-search DOM executor.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildBiliTaskResultPayload,
  executeBiliSearch,
  extractBiliSearchVideos,
  extractBvid,
  normalizeCountText,
  type BiliSearchVideo,
  type BiliTaskExecuteMessage,
} from "../src/content/bili/task-executor.ts";

class FakeElement {
  readonly textContent: string;
  readonly href?: string;
  readonly src?: string;
  private readonly selectorMap: Record<string, FakeElement[]>;

  constructor(opts: {
    textContent?: string;
    href?: string;
    src?: string;
    selectorMap?: Record<string, FakeElement[]>;
  } = {}) {
    this.textContent = opts.textContent ?? "";
    this.href = opts.href;
    this.src = opts.src;
    this.selectorMap = opts.selectorMap ?? {};
  }

  querySelector(selector: string): FakeElement | null {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  querySelectorAll(selector: string): FakeElement[] {
    return this.selectorMap[selector] ?? [];
  }
}

class FakeDocument {
  private readonly cards: FakeElement[];

  constructor(cards: FakeElement[]) {
    this.cards = cards;
  }

  querySelectorAll(selector: string): FakeElement[] {
    if (selector === "[data-testid='bili-video-card'], .bili-video-card, .video-list-item") {
      return this.cards;
    }
    return [];
  }
}

function card(video: Partial<BiliSearchVideo> & { href: string; title: string }): FakeElement {
  const title = new FakeElement({ textContent: video.title, href: video.href });
  const img = new FakeElement({ src: video.cover_url });
  const up = new FakeElement({ textContent: video.up_name });
  const stats = new FakeElement({ textContent: String(video.view_count ?? "") });
  return new FakeElement({
    selectorMap: {
      "a[href*='/video/']": [title],
      ".bili-video-card__info--tit, .video-title, h3": [title],
      "img": [img],
      ".bili-video-card__info--author, .up-name, [title='up主']": [up],
      ".bili-video-card__stats--item, .so-icon.watch-num, .play-text": [stats],
    },
  });
}

test("extractBvid reads canonical BV identifiers from URLs", () => {
  assert.equal(
    extractBvid("https://www.bilibili.com/video/BV1ii4y1G7w8/?spm_id_from=333"),
    "BV1ii4y1G7w8",
  );
  assert.equal(extractBvid("https://search.bilibili.com/all?keyword=x"), "");
});

test("normalizeCountText supports Chinese play-count suffixes", () => {
  assert.equal(normalizeCountText("1.2万"), 12_000);
  assert.equal(normalizeCountText("3亿"), 300_000_000);
  assert.equal(normalizeCountText("1234"), 1_234);
  assert.equal(normalizeCountText("--"), 0);
});

test("extractBiliSearchVideos reads rendered search cards with dedupe and cap", () => {
  const doc = new FakeDocument([
    card({
      href: "https://www.bilibili.com/video/BV1ii4y1G7w8",
      title: "机械键盘声音对比",
      up_name: "机械工厂",
      cover_url: "https://i0.hdslb.com/bfs/archive/demo.jpg",
      view_count: "1.2万",
    }),
    card({
      href: "https://www.bilibili.com/video/BV1ii4y1G7w8",
      title: "重复卡片",
      up_name: "重复",
      view_count: "2万",
    }),
    card({
      href: "https://www.bilibili.com/video/BV1FF411o7Lk",
      title: "20种键盘轴体打字音合集",
      up_name: "腿毛故事",
      view_count: "3456",
    }),
  ]);

  const videos = extractBiliSearchVideos(doc as unknown as Document, { limit: 1 });

  assert.deepEqual(videos, [
    {
      bvid: "BV1ii4y1G7w8",
      title: "机械键盘声音对比",
      up_name: "机械工厂",
      url: "https://www.bilibili.com/video/BV1ii4y1G7w8",
      cover_url: "https://i0.hdslb.com/bfs/archive/demo.jpg",
      view_count: 12_000,
    },
  ]);
});

test("buildBiliTaskResultPayload maps videos to ok or empty", () => {
  assert.deepEqual(buildBiliTaskResultPayload("task-1", []), {
    task_id: "task-1",
    status: "empty",
    videos: [],
  });
  assert.equal(
    buildBiliTaskResultPayload("task-1", [
      {
        bvid: "BV1FF411o7Lk",
        title: "20种键盘轴体打字音合集",
        url: "https://www.bilibili.com/video/BV1FF411o7Lk",
      },
    ]).status,
    "ok",
  );
});

test("executeBiliSearch returns empty when no rendered cards appear", async () => {
  const msg: BiliTaskExecuteMessage = {
    task_id: "task-empty",
    type: "search",
    query: "不存在的关键词",
    limit: 20,
  };
  const result = await executeBiliSearch(msg, {
    document: new FakeDocument([]) as unknown as Document,
    waitForResults: async () => false,
  });

  assert.deepEqual(result, {
    task_id: "task-empty",
    status: "empty",
    videos: [],
    debug: { rendered: false, extracted_count: 0 },
  });
});
