import test from "node:test";
import assert from "node:assert/strict";

import {
  extractXhsSearchResponseNotes,
  isXhsSearchApiUrl,
} from "../src/shared/xhs-search-response.ts";
import {
  readXhsSearchResponseNotes,
  recordXhsSearchResponseNotes,
  resetXhsSearchResponseNotesForTest,
} from "../src/content/xhs/search-response-buffer.ts";

test("keeps available note descriptions and tag names without raw response fields", () => {
  const notes = extractXhsSearchResponseNotes({
    items: [{
      id: "69c7a7b000000000220030c9",
      access_token: "never-forward",
      note_card: {
        display_title: "有正文的笔记",
        desc: "正文中的条件和限制必须保留。",
        tag_list: [{ name: "编程", id: "private-id" }, { name: "Rust" }, { name: "编程" }],
      },
    }],
  });
  assert.equal(notes[0]?.description, "正文中的条件和限制必须保留。");
  assert.deepEqual(notes[0]?.tags, ["编程", "Rust"]);
  assert.ok(!JSON.stringify(notes).includes("never-forward"));
  assert.ok(!JSON.stringify(notes).includes("private-id"));
  recordXhsSearchResponseNotes(notes);
  notes[0]!.tags![0] = "mutated input";
  const buffered = readXhsSearchResponseNotes();
  assert.deepEqual(buffered[0]?.tags, ["编程", "Rust"]);
  assert.equal(buffered[0]?.description, "正文中的条件和限制必须保留。");
  buffered[0]!.tags!.push("mutated output");
  assert.deepEqual(readXhsSearchResponseNotes()[0]?.tags, ["编程", "Rust"]);
  resetXhsSearchResponseNotesForTest();
});

test("normalizes current snake_case XHS search response cards", () => {
  const notes = extractXhsSearchResponseNotes({
    data: {
      items: [
        {
          id: "69c7a7b000000000220030c9",
          xsec_token: "token with spaces",
          note_card: {
            display_title: "后台搜索结果",
            user: { nickname: "作者" },
            cover: { url_default: "https://example.com/cover.jpg" },
            interact_info: {
              liked_count: "12",
              collected_count: 3,
              comment_count: "4",
            },
            time: 1786000000000,
          },
        },
      ],
    },
  });

  assert.deepEqual(notes, [
    {
      url: "https://www.xiaohongshu.com/explore/69c7a7b000000000220030c9?xsec_token=token+with+spaces",
      title: "后台搜索结果",
      author: "作者",
      cover_url: "https://example.com/cover.jpg",
      like_count: 12,
      collect_count: 3,
      comment_count: 4,
      published_at: 1786000000000,
    },
  ]);
});

test("supports camelCase cards, deduplicates nested wrappers, and rejects user objects", () => {
  const wrapper = {
    id: "1111111111111111aaaaaaaa",
    xsecToken: "tok-a",
    noteCard: {
      displayTitle: "camel",
      userInfo: { nickName: "author" },
      cover: { urlDefault: "https://example.com/a.jpg" },
      interactInfo: { viewCount: "99", likedCount: 8 },
    },
  };
  const notes = extractXhsSearchResponseNotes({
    data: {
      items: [wrapper, { ...wrapper }],
      current_user: {
        id: "2222222222222222bbbbbbbb",
        nickname: "not a note",
        xsec_token: "user-token",
      },
    },
  });

  assert.equal(notes.length, 1);
  assert.equal(notes[0]?.title, "camel");
  assert.equal(notes[0]?.view_count, 99);
  assert.equal(notes[0]?.like_count, 8);
});

test("does not stringify nested values into metadata", () => {
  const notes = extractXhsSearchResponseNotes({
    note_id: "3333333333333333cccccccc",
    title: { text: "nested title" },
    cover: { url: { nested: true } },
  });
  assert.deepEqual(notes, [
    {
      url: "https://www.xiaohongshu.com/explore/3333333333333333cccccccc",
      title: "",
      author: "",
      cover_url: "",
    },
  ]);
});

test("recognizes only the XHS web search notes API", () => {
  assert.equal(
    isXhsSearchApiUrl("https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"),
    true,
  );
  assert.equal(isXhsSearchApiUrl("/api/sns/web/v2/search/notes/page"), true);
  assert.equal(isXhsSearchApiUrl("/api/sns/web/v1/feed"), false);
});

test("isolated-world search response buffer copies inputs and outputs", () => {
  resetXhsSearchResponseNotesForTest();
  const input = [
    {
      url: "https://www.xiaohongshu.com/explore/69c7a7b000000000220030c9",
      title: "one",
      author: "author",
      cover_url: "",
    },
  ];
  recordXhsSearchResponseNotes(input);
  input[0]!.title = "mutated input";
  const first = readXhsSearchResponseNotes();
  assert.equal(first[0]?.title, "one");
  first[0]!.title = "mutated output";
  assert.equal(readXhsSearchResponseNotes()[0]?.title, "one");
});
