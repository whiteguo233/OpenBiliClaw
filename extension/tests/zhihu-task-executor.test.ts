import test from "node:test";
import assert from "node:assert/strict";

import {
  executeZhihuTask,
  normalizeZhihuActivity,
  normalizeZhihuCollectionItem,
  normalizeZhihuCreatorItem,
  normalizeZhihuFeedItem,
  normalizeZhihuHotItem,
  normalizeZhihuReadHistory,
  normalizeZhihuRelatedItem,
  normalizeZhihuSearchResult,
} from "../src/content/zhihu/task-executor.ts";

test("normalizeZhihuReadHistory maps read_history payload items", () => {
  const item = normalizeZhihuReadHistory({
    data: {
      header: { title: "浏览了回答" },
      content: { author_name: "作者", summary: "摘要" },
      action: { url: "https://www.zhihu.com/question/1/answer/2" },
      extra: {
        content_token: "2",
        content_type: "answer",
        question_token: "1",
        read_time: 1710000000,
      },
    },
  });

  assert.deepEqual(item, {
    scope: "zhihu_read_history",
    content_type: "answer",
    content_id: "2",
    question_id: "1",
    title: "浏览了回答",
    author: "作者",
    summary: "摘要",
    url: "https://www.zhihu.com/question/1/answer/2",
    interaction_time: "1710000000",
  });
});

test("normalizeZhihuActivity maps liked answers", () => {
  const item = normalizeZhihuActivity({
    id: "1710000000000",
    action_text: "赞同了回答",
    target: {
      type: "answer",
      id: "2",
      question: { id: "1", title: "问题标题" },
      author: { name: "作者" },
      voteup_count: 88,
    },
  });

  assert.equal(item?.scope, "zhihu_activity");
  assert.equal(item?.interaction_action, "赞同了回答");
  assert.equal(item?.title, "问题标题");
  assert.equal(item?.url, "https://www.zhihu.com/question/1/answer/2");
});

test("normalizeZhihuCollectionItem maps collection content", () => {
  const item = normalizeZhihuCollectionItem(
    {
      content: {
        type: "article",
        id: "9",
        title: "文章标题",
        url: "https://zhuanlan.zhihu.com/p/9",
        author: { name: "作者" },
        excerpt: "摘要",
      },
    },
    { id: "c1", name: "默认收藏" },
  );

  assert.equal(item?.scope, "zhihu_collection");
  assert.equal(item?.content_type, "article");
  assert.equal(item?.content_id, "9");
  assert.equal(item?.collection_id, "c1");
  assert.equal(item?.collection_name, "默认收藏");
});

test("normalizeZhihuSearchResult maps search answers", () => {
  const item = normalizeZhihuSearchResult(
    {
      type: "search_result",
      object: {
        type: "answer",
        id: "2",
        excerpt: "<em>回答</em>摘要",
        voteup_count: 88,
        question: { id: "1", title: "问题标题" },
        author: { name: "作者" },
      },
    },
    "AI 工程化",
  );

  assert.equal(item?.scope, "zhihu_search");
  assert.equal(item?.search_keyword, "AI 工程化");
  assert.equal(item?.content_type, "answer");
  assert.equal(item?.content_id, "2");
  assert.equal(item?.question_id, "1");
  assert.equal(item?.title, "问题标题");
  assert.equal(item?.summary, "回答摘要");
  assert.equal(item?.voteup, 88);
  assert.equal(item?.url, "https://www.zhihu.com/question/1/answer/2");
});

test("normalizeZhihuHotItem maps hot-list targets", () => {
  const item = normalizeZhihuHotItem({
    target: {
      type: "question",
      id: "10",
      title: "热榜问题",
      excerpt: "热榜摘要",
      url: "https://www.zhihu.com/question/10",
      answer_count: 12,
      follower_count: 34,
    },
  });

  assert.equal(item?.scope, "zhihu_hot");
  assert.equal(item?.source_strategy, "zhihu-hot");
  assert.equal(item?.content_type, "question");
  assert.equal(item?.content_id, "10");
  assert.equal(item?.title, "热榜问题");
  assert.equal(item?.summary, "热榜摘要");
  assert.equal(item?.favorite_count, 34);
  assert.equal(item?.comment_count, 12);
});

test("normalizeZhihuHotItem preserves large question ids from URL strings", () => {
  const questionId = "2053435015258804659";
  const item = normalizeZhihuHotItem({
    target: {
      type: "question",
      id: Number(questionId),
      title: "凡人动画大电影票房问题",
      url: `https://www.zhihu.com/question/${questionId}`,
    },
  });

  assert.equal(item?.content_id, questionId);
  assert.equal(item?.url, `https://www.zhihu.com/question/${questionId}`);
});

test("normalizeZhihuFeedItem maps recommendation feed answers", () => {
  const item = normalizeZhihuFeedItem({
    target: {
      type: "answer",
      id: "12",
      excerpt: "首页摘要",
      question: { id: "11", title: "首页问题" },
      author: { name: "作者 F", url_token: "author-f" },
      voteup_count: 99,
    },
  });

  assert.equal(item?.scope, "zhihu_feed");
  assert.equal(item?.source_strategy, "zhihu-feed");
  assert.equal(item?.content_type, "answer");
  assert.equal(item?.content_id, "12");
  assert.equal(item?.question_id, "11");
  assert.equal(item?.author, "作者 F");
  assert.equal(item?.author_url, "https://www.zhihu.com/people/author-f");
  assert.equal(item?.url, "https://www.zhihu.com/question/11/answer/12");
});

test("normalizeZhihuCreatorItem maps member articles", () => {
  const item = normalizeZhihuCreatorItem(
    {
      type: "article",
      id: "13",
      title: "作者文章",
      excerpt: "文章摘要",
      url: "https://zhuanlan.zhihu.com/p/13",
      author: { name: "作者 C", url_token: "creator-c" },
    },
    "https://www.zhihu.com/people/creator-c",
  );

  assert.equal(item?.scope, "zhihu_creator");
  assert.equal(item?.source_strategy, "zhihu-creator");
  assert.equal(item?.content_type, "article");
  assert.equal(item?.content_id, "13");
  assert.equal(item?.author_url, "https://www.zhihu.com/people/creator-c");
});

test("normalizeZhihuRelatedItem maps question feed answers", () => {
  const item = normalizeZhihuRelatedItem(
    {
      target: {
        type: "answer",
        id: "15",
        excerpt: "相关摘要",
        question: { id: "14", title: "相关问题" },
        author: { name: "作者 R" },
      },
    },
    "https://www.zhihu.com/question/14",
  );

  assert.equal(item?.scope, "zhihu_related");
  assert.equal(item?.source_strategy, "zhihu-related");
  assert.equal(item?.content_type, "answer");
  assert.equal(item?.content_id, "15");
  assert.equal(item?.question_id, "14");
  assert.equal(item?.url, "https://www.zhihu.com/question/14/answer/15");
});

test("executeZhihuTask reports login_required when Zhihu redirects to signin", async () => {
  const originalFetch = globalThis.fetch;
  const originalLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: { href: "https://www.zhihu.com/signin?next=%2F" },
  });
  globalThis.fetch = async () =>
    new Response('{"error":{"code":400,"message":"请求错误"}}', {
      status: 400,
      headers: { "content-type": "application/json" },
    });

  try {
    const result = await executeZhihuTask({
      task_id: "task-login",
      scopes: ["zhihu_read_history"],
    });

    assert.equal(result.status, "failed");
    assert.equal(result.error, "zhihu_login_required");
    assert.equal(result.debug?.login_required, true);
    assert.equal(result.debug?.current_url, "https://www.zhihu.com/signin?next=%2F");
    assert.equal(result.debug?.http_status, 400);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalLocation) {
      Object.defineProperty(globalThis, "location", originalLocation);
    } else {
      Reflect.deleteProperty(globalThis, "location");
    }
  }
});

test("executeZhihuTask runs search tasks through search_v3", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    assert.ok(url.includes("/api/v4/search_v3"));
    return new Response(
      JSON.stringify({
        data: [
          {
            type: "search_result",
            object: {
              type: "article",
              id: "9",
              title: "文章标题",
              excerpt: "文章摘要",
              url: "https://zhuanlan.zhihu.com/p/9",
              author: { name: "作者" },
            },
          },
        ],
        paging: { is_end: true },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  };

  try {
    const result = await executeZhihuTask({
      task_id: "task-search",
      type: "search",
      keywords: ["AI 工程化"],
      max_items_per_keyword: 5,
    });

    assert.equal(result.status, "ok");
    assert.equal(result.items.length, 1);
    assert.equal(result.items[0]?.scope, "zhihu_search");
    assert.equal(result.items[0]?.search_keyword, "AI 工程化");
    assert.equal(result.scope_counts.zhihu_search, 1);
    assert.equal(calls.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("executeZhihuTask preserves large numeric ids from raw Zhihu JSON", async () => {
  const originalFetch = globalThis.fetch;
  const questionId = "2053435015258804659";
  globalThis.fetch = async (input: RequestInfo | URL) => {
    const url = String(input);
    assert.ok(url.startsWith("/api/v3/feed/topstory/hot-lists/total"));
    return new Response(
      `{"data":[{"target":{"type":"question","id":${questionId},"title":"凡人动画大电影票房问题"}}],"paging":{"is_end":true}}`,
      { headers: { "content-type": "application/json" } },
    );
  };

  try {
    const result = await executeZhihuTask({ task_id: "task-hot-large-id", type: "hot", max_items: 1 });

    assert.equal(result.items[0]?.content_id, questionId);
    assert.equal(result.items[0]?.url, `https://www.zhihu.com/question/${questionId}`);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("executeZhihuTask runs hot feed creator and related discovery tasks", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.startsWith("/api/v3/feed/topstory/hot-lists/total")) {
      return Response.json({
        data: [
          { target: { type: "question", id: "10", title: "热榜问题", url: "https://www.zhihu.com/question/10" } },
        ],
        paging: { is_end: true },
      });
    }
    if (url.startsWith("/api/v3/feed/topstory/recommend")) {
      return Response.json({
        data: [
          {
            target: {
              type: "answer",
              id: "12",
              question: { id: "11", title: "首页问题" },
              author: { name: "作者 F" },
            },
          },
        ],
        paging: { is_end: true },
      });
    }
    if (url.startsWith("/api/v4/members/creator-c/articles")) {
      return Response.json({
        data: [
          { type: "article", id: "13", title: "作者文章", url: "https://zhuanlan.zhihu.com/p/13" },
        ],
        paging: { is_end: true },
      });
    }
    if (url.startsWith("/api/v4/members/creator-c/answers")) {
      return Response.json({ data: [], paging: { is_end: true } });
    }
    if (url.startsWith("/api/v4/questions/14/feeds")) {
      return Response.json({
        data: [
          {
            target: {
              type: "answer",
              id: "15",
              question: { id: "14", title: "相关问题" },
              author: { name: "作者 R" },
            },
          },
        ],
        paging: { is_end: true },
      });
    }
    return new Response("not found", { status: 404 });
  };

  try {
    const hot = await executeZhihuTask({ task_id: "hot", type: "hot", max_items: 5 });
    const feed = await executeZhihuTask({ task_id: "feed", type: "feed", max_items: 5 });
    const creator = await executeZhihuTask({
      task_id: "creator",
      type: "creator",
      creator_urls: ["https://www.zhihu.com/people/creator-c"],
      max_items_per_creator: 5,
    });
    const related = await executeZhihuTask({
      task_id: "related",
      type: "related",
      related_urls: ["https://www.zhihu.com/question/14"],
      max_items_per_seed: 5,
    });

    assert.equal(hot.items[0]?.scope, "zhihu_hot");
    assert.equal(feed.items[0]?.scope, "zhihu_feed");
    assert.equal(creator.items[0]?.scope, "zhihu_creator");
    assert.equal(related.items[0]?.scope, "zhihu_related");
    assert.equal(hot.scope_counts.zhihu_hot, 1);
    assert.equal(feed.scope_counts.zhihu_feed, 1);
    assert.equal(creator.scope_counts.zhihu_creator, 1);
    assert.equal(related.scope_counts.zhihu_related, 1);
    assert.ok(calls.some((url) => url.includes("/hot-lists/total")));
    assert.ok(calls.some((url) => url.includes("/topstory/recommend")));
    assert.ok(calls.some((url) => url.includes("/members/creator-c/articles")));
    assert.ok(calls.some((url) => url.includes("/questions/14/feeds")));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("executeZhihuTask auto-detects the current member and reads activity plus favlists", async () => {
  const originalFetch = globalThis.fetch;
  const seenUrls: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    seenUrls.push(url);
    if (url === "/api/v4/me") {
      return Response.json({ url_token: "current-user" });
    }
    if (url.startsWith("/api/v3/moments/current-user/activities")) {
      return new Response('{"error":{"code":10003,"message":"请求参数异常，请升级客户端后重试。"}}', {
        status: 403,
      });
    }
    if (url.startsWith("/api/v4/members/current-user/activities")) {
      return Response.json({
        data: [
          {
            id: "1710000000000",
            action_text: "赞同了回答",
            target: {
              type: "answer",
              id: "2",
              question: { id: "1", title: "问题标题" },
              author: { name: "作者" },
            },
          },
        ],
        paging: { is_end: true },
      });
    }
    if (url === "/api/v4/members/current-user/favlists?offset=0&limit=20") {
      return Response.json({
        data: [{ id: "fav1", title: "默认收藏夹" }],
        paging: { is_end: true },
      });
    }
    if (url === "/api/v4/favlists/fav1/items?offset=0&limit=20") {
      return Response.json({
        data: [
          {
            content: {
              type: "article",
              id: "9",
              title: "文章标题",
              url: "https://zhuanlan.zhihu.com/p/9",
              author: { name: "作者" },
            },
          },
        ],
        paging: { is_end: true },
      });
    }
    return new Response("not found", { status: 404 });
  };

  try {
    const result = await executeZhihuTask({
      task_id: "task-auto",
      scopes: ["zhihu_activity", "zhihu_collection"],
    });

    assert.equal(result.status, "ok");
    assert.equal(result.scope_counts.zhihu_activity_like, 1);
    assert.equal(result.scope_counts.zhihu_collection, 1);
    assert.equal(result.debug?.current_member_url_token, "current-user");
    assert.ok(seenUrls.includes("/api/v4/me"));
    assert.ok(seenUrls.includes("/api/v4/members/current-user/favlists?offset=0&limit=20"));
    assert.ok(seenUrls.includes("/api/v4/favlists/fav1/items?offset=0&limit=20"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("executeZhihuTask applies separate caps to activity likes and favorites", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.startsWith("/api/v3/moments/demo/activities")) {
      return Response.json({
        data: [
          {
            id: "1",
            action_text: "赞同了回答",
            target: {
              type: "answer",
              id: "like1",
              question: { id: "q1", title: "点赞 1" },
              author: { name: "作者" },
            },
          },
          {
            id: "2",
            action_text: "赞同了回答",
            target: {
              type: "answer",
              id: "like2",
              question: { id: "q2", title: "点赞 2" },
              author: { name: "作者" },
            },
          },
          {
            id: "3",
            action_text: "赞同了回答",
            target: {
              type: "answer",
              id: "like3",
              question: { id: "q3", title: "点赞 3" },
              author: { name: "作者" },
            },
          },
          {
            id: "4",
            action_text: "收藏了回答",
            target: {
              type: "answer",
              id: "fav1",
              question: { id: "q4", title: "收藏 1" },
              author: { name: "作者" },
            },
          },
          {
            id: "5",
            action_text: "收藏了回答",
            target: {
              type: "answer",
              id: "fav2",
              question: { id: "q5", title: "收藏 2" },
              author: { name: "作者" },
            },
          },
          {
            id: "6",
            action_text: "收藏了回答",
            target: {
              type: "answer",
              id: "fav3",
              question: { id: "q6", title: "收藏 3" },
              author: { name: "作者" },
            },
          },
        ],
        paging: { is_end: true },
      });
    }
    return new Response("not found", { status: 404 });
  };

  try {
    const result = await executeZhihuTask({
      task_id: "task-activity-caps",
      profile_slug: "demo",
      scopes: ["zhihu_activity"],
      max_items_per_scope: 2,
    });

    assert.equal(result.status, "ok");
    assert.equal(result.scope_counts.zhihu_activity_like, 2);
    assert.equal(result.scope_counts.zhihu_activity_favorite, 2);
    assert.equal(result.items.length, 4);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
