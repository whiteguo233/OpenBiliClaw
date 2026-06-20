import test from "node:test";
import assert from "node:assert/strict";

import { extractDouyinSearchItemsFromDocument } from "../src/content/dy/dom-extractor.ts";

class FakeElement {
  readonly textContent: string;
  readonly href: string;
  readonly src: string;
  private readonly attrs: Record<string, string>;
  private readonly selectorMap: Record<string, FakeElement[]>;
  private readonly closestElement?: FakeElement;

  constructor(opts: {
    textContent?: string;
    href?: string;
    src?: string;
    attrs?: Record<string, string>;
    selectorMap?: Record<string, FakeElement[]>;
    closestElement?: FakeElement;
  } = {}) {
    this.textContent = opts.textContent ?? "";
    this.href = opts.href ?? "";
    this.src = opts.src ?? "";
    this.attrs = opts.attrs ?? {};
    this.selectorMap = opts.selectorMap ?? {};
    this.closestElement = opts.closestElement;
  }

  closest(): FakeElement {
    return this.closestElement ?? this;
  }

  querySelector(selector: string): FakeElement | null {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  querySelectorAll(selector: string): FakeElement[] {
    if (selector.includes("[aria-label]")) {
      return this.selectorMap.metrics ?? [];
    }
    return this.selectorMap[selector] ?? [];
  }

  getAttribute(name: string): string | null {
    if (name === "href") return this.href || this.attrs[name] || null;
    if (name === "src") return this.src || this.attrs[name] || null;
    return this.attrs[name] ?? null;
  }
}

class FakeDocument {
  private readonly anchors: FakeElement[];

  constructor(anchors: FakeElement[]) {
    this.anchors = anchors;
  }

  querySelectorAll(selector: string): FakeElement[] {
    if (selector === 'a[href*="/video/"]') return this.anchors;
    return [];
  }
}

test("extractDouyinSearchItemsFromDocument reads visible metric chips", () => {
  const title = new FakeElement({ textContent: "猫咪搜索结果" });
  const author = new FakeElement({ textContent: "作者A" });
  const image = new FakeElement({ attrs: { src: "https://p3.douyinpic.com/cover.jpg" } });
  const card = new FakeElement({
    selectorMap: {
      'p[class*="title"]': [title],
      '[class*="author-name"]': [author],
      "img": [image],
      metrics: [
        new FakeElement({ textContent: "播放 1.2万" }),
        new FakeElement({ textContent: "点赞 42" }),
        new FakeElement({ textContent: "收藏 1,234" }),
        new FakeElement({ textContent: "评论 3k" }),
        new FakeElement({ textContent: "分享 9" }),
      ],
    },
  });
  const anchor = new FakeElement({
    href: "/video/1234567890",
    closestElement: card,
  });

  const items = extractDouyinSearchItemsFromDocument(
    new FakeDocument([anchor]) as unknown as Document,
    "https://www.douyin.com/",
    5,
  );

  assert.deepEqual(items, [
    {
      scope: "dy_search",
      aweme_id: "1234567890",
      url: "https://www.douyin.com/video/1234567890",
      title: "猫咪搜索结果",
      author: "作者A",
      author_sec_uid: "",
      cover_url: "https://p3.douyinpic.com/cover.jpg",
      view_count: 12_000,
      like_count: 42,
      collect_count: 1_234,
      comment_count: 3_000,
      share_count: 9,
    },
  ]);
});
