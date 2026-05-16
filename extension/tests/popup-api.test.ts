import test from "node:test";
import assert from "node:assert/strict";

import {
  appendRecommendations,
  fetchPendingDelight,
  fetchActivityFeed,
  fetchConfig,
  fetchProfileSummary,
  reshuffleRecommendations,
  updateConfig,
} from "../popup/popup-api.js";

test("reshuffleRecommendations posts to reshuffle endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 11,
              bvid: "BV1NEW",
              title: "新的一批",
              up_name: "UPA",
              cover_url: "//i0.hdslb.com/bfs/archive/new-cover.jpg",
              expression: "先给你捞一条新的。",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await reshuffleRecommendations();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/reshuffle");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(result, {
    items: [
      {
        id: 11,
        bvid: "BV1NEW",
        title: "新的一批",
        up_name: "UPA",
        cover_url: "https://i0.hdslb.com/bfs/archive/new-cover.jpg",
        expression: "先给你捞一条新的。",
        topic_label: "",
        presented: false,
        content_id: "BV1NEW",
        content_url: "",
        source_platform: "bilibili",
      },
    ],
  });
});

test("appendRecommendations posts excluded bvids to append endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 21,
              bvid: "BV1APPEND",
              title: "追加的一条",
              up_name: "UPB",
              cover_url: "http://i0.hdslb.com/bfs/archive/append-cover.jpg",
              expression: "",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await appendRecommendations(["BV1A", "BV1B"]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/append");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ excluded_bvids: ["BV1A", "BV1B"] }));
  assert.deepEqual(result, {
    items: [
      {
        id: 21,
        bvid: "BV1APPEND",
        title: "追加的一条",
        up_name: "UPB",
        cover_url: "https://i0.hdslb.com/bfs/archive/append-cover.jpg",
        expression: "",
        topic_label: "",
        presented: false,
        content_id: "BV1APPEND",
        content_url: "",
        source_platform: "bilibili",
      },
    ],
  });
});

test("fetchRecommendations normalizes cover urls from the recommend endpoint", async () => {
  globalThis.fetch = async () => ({
    ok: true,
    async json() {
      return {
        items: [
          {
            id: 31,
            bvid: "BV1FETCH",
            title: "初始推荐",
            up_name: "UPC",
            cover_url: "http://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
            expression: "",
            topic_label: "",
            presented: 0,
          },
        ],
      };
    },
  });

  const { fetchRecommendations } = await import("../popup/popup-api.js");
  const result = await fetchRecommendations();

  assert.deepEqual(result, [
    {
      id: 31,
      bvid: "BV1FETCH",
      title: "初始推荐",
      up_name: "UPC",
      cover_url: "https://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
      expression: "",
      topic_label: "",
      presented: false,
      content_id: "BV1FETCH",
      content_url: "",
      source_platform: "bilibili",
    },
  ]);
});

test("fetchActivityFeed loads popup activity summaries", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          live_summary: "正在补候选",
          headline: "阿B 刚记下了你最近更吃深拆",
          items: [],
        };
      },
    };
  };

  const result = await fetchActivityFeed();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/activity-feed");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(result, {
    live_summary: "正在补候选",
    headline: "阿B 刚记下了你最近更吃深拆",
    items: [],
  });
});

test("fetchPendingDelight loads the current pending delight candidate", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          item: {
            bvid: "BV1DELIGHT",
            title: "你可能会意外喜欢的这条",
            delight_reason: "它和你最近的节奏不完全一样，但入口很对味。",
            delight_score: 0.78,
            delight_hook: "换个方向试试",
            cover_url: "//i0.hdslb.com/bfs/archive/delight-cover.jpg",
          },
        };
      },
    };
  };

  const result = await fetchPendingDelight();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/delight/pending");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(result, {
    bvid: "BV1DELIGHT",
    title: "你可能会意外喜欢的这条",
    delight_reason: "它和你最近的节奏不完全一样，但入口很对味。",
    delight_score: 0.78,
    delight_hook: "换个方向试试",
    cover_url: "//i0.hdslb.com/bfs/archive/delight-cover.jpg",
  });
});

test("fetchProfileSummary forwards limit and cursor for cognition history pagination", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          initialized: true,
          recent_cognition_updates: [],
          has_more_cognition_updates: false,
          next_cognition_cursor: "",
        };
      },
    };
  };

  await fetchProfileSummary({ limit: 3, cursor: "6" });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/profile-summary?limit=3&cursor=6");
  assert.equal(calls[0].options.method, "GET");
});

test("fetchConfig sends GET to /config with reveal_keys", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          language: "zh",
          llm: {
            default_provider: "gemini",
            gemini: { api_key: "test-key", model: "gemini-2.5-flash" },
            embedding: {
              provider: "gemini",
              model: "gemini-embedding-001",
              similarity_threshold: 0.85,
            },
          },
        };
      },
    };
  };

  const result = await fetchConfig();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config?reveal_keys=true");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(result.llm.default_provider, "gemini");
  assert.equal(result.llm.gemini.api_key, "test-key");
  assert.equal(result.llm.embedding.provider, "gemini");
  assert.equal(result.llm.embedding.model, "gemini-embedding-001");
  assert.equal(result.llm.embedding.similarity_threshold, 0.85);
});

test("updateConfig sends PUT with embedding config", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          config: { language: "zh", llm: { embedding: { provider: "openai", model: "text-embedding-3-small", similarity_threshold: 0.78 } } },
          message: "配置已保存。",
          reloaded: true,
        };
      },
    };
  };

  const payload = {
    llm: {
      default_provider: "openai",
      openai: { api_key: "sk-test", model: "gpt-4o" },
      embedding: {
        provider: "openai",
        model: "text-embedding-3-small",
        similarity_threshold: 0.78,
      },
    },
  };

  const result = await updateConfig(payload);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config");
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");

  const sentBody = JSON.parse(calls[0].options.body);
  assert.equal(sentBody.llm.embedding.provider, "openai");
  assert.equal(sentBody.llm.embedding.model, "text-embedding-3-small");
  assert.equal(sentBody.llm.embedding.similarity_threshold, 0.78);
  assert.equal(sentBody.llm.openai.api_key, "sk-test");

  assert.equal(result.ok, true);
  assert.equal(result.reloaded, true);
});

test("fetchConfig uses backend port configured in chrome.storage.local", async () => {
  const originalChrome = (globalThis as any).chrome;
  const calls: Array<{ url: string; options: any }> = [];

  (globalThis as any).chrome = {
    storage: {
      local: {
        get(_key: string, callback: (items: Record<string, unknown>) => void) {
          callback({
            popup_backend_endpoint: {
              host: "127.0.0.1",
              port: 9527,
              basePath: "/api",
            },
          });
        },
      },
    },
  };

  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { language: "zh" };
      },
    };
  };

  try {
    await fetchConfig();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "http://127.0.0.1:9527/api/config?reveal_keys=true");
  } finally {
    (globalThis as any).chrome = originalChrome;
  }
});
