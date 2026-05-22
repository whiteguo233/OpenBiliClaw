import test from "node:test";
import assert from "node:assert/strict";

import {
  appendRecommendations,
  cacheConfigSnapshot,
  fetchPendingDelight,
  fetchActivityFeed,
  fetchChatTurn,
  fetchChatTurns,
  fetchConfig,
  fetchProfileSummary,
  fetchSourceShareSuggestion,
  readCachedConfigSnapshot,
  requestJson,
  reshuffleRecommendations,
  startChatTurn,
  updateConfig,
} from "../popup/popup-api.js";
import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";

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

test("fetchConfig caches successful config snapshots in chrome storage", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const writes: Array<Record<string, unknown>> = [];
  const storage: Record<string, unknown> = {};
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(key: string, callback: (items: Record<string, unknown>) => void) {
          callback({ [key]: storage[key] });
        },
        set(items: Record<string, unknown>, callback: () => void) {
          writes.push(items);
          Object.assign(storage, items);
          callback();
        },
      },
    },
  };
  globalThis.fetch = async () => ({
    ok: true,
    async json() {
      return {
        language: "zh",
        llm: {
          default_provider: "openai",
          openai: { api_key: "sk-test" },
        },
      };
    },
  }) as Response;

  try {
    const result = await fetchConfig();
    const cached = await readCachedConfigSnapshot();

    assert.equal(result.llm.default_provider, "openai");
    assert.equal(writes.length, 1);
    assert.ok(writes[0]["openbiliclaw.config_cache"]);
    assert.equal(cached?.config.llm.openai.api_key, "sk-test");
    assert.match(cached?.cached_at ?? "", /^\d{4}-\d{2}-\d{2}T/);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("cacheConfigSnapshot no-ops when chrome storage is unavailable", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  delete (globalThis as { chrome?: unknown }).chrome;

  try {
    const snapshot = await cacheConfigSnapshot({ language: "zh" });
    assert.equal(snapshot, null);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("fetchSourceShareSuggestion loads source-share recommendation", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          event_counts: { bilibili: 9, youtube: 4 },
          enabled_sources: { bilibili: true, youtube: true },
          suggested_shares: { bilibili: 8, youtube: 5 },
        };
      },
    };
  };

  const result = await fetchSourceShareSuggestion();

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8420/api/config/source-share-suggestion",
  );
  assert.equal(calls[0].options.method, "GET");
  assert.equal(result.suggested_shares.youtube, 5);
});

test("fetchSourceShareSuggestion posts current settings overrides when provided", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          event_counts: { bilibili: 9, youtube: 4 },
          enabled_sources: { bilibili: true, youtube: true },
          suggested_shares: { bilibili: 6, youtube: 4 },
        };
      },
    };
  };

  const result = await fetchSourceShareSuggestion({
    enabled_sources: {
      bilibili: true,
      xiaohongshu: false,
      douyin: false,
      youtube: true,
    },
    configured_shares: {
      bilibili: 6,
      xiaohongshu: 2,
      douyin: 1,
      youtube: 2,
    },
  });

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8420/api/config/source-share-suggestion",
  );
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    enabled_sources: {
      bilibili: true,
      xiaohongshu: false,
      douyin: false,
      youtube: true,
    },
    configured_shares: {
      bilibili: 6,
      xiaohongshu: 2,
      douyin: 1,
      youtube: 2,
    },
  });
  assert.equal(result.suggested_shares.youtube, 4);
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

test("updateConfig preserves structured details from validation errors", async () => {
  const details = {
    ok: false,
    reloaded: false,
    rollback_applied: false,
    config: {
      issues: [
        {
          field: "llm",
          message: "LLM registry would fail to build",
          severity: "blocking",
        },
      ],
    },
    message: "配置校验失败，未写入 config.toml。",
  };
  globalThis.fetch = async () => ({
    ok: false,
    status: 400,
    async json() {
      return details;
    },
  });

  await assert.rejects(
    () => updateConfig({ reset_fields: ["llm.openai.api_key"] }),
    (error: any) => {
      assert.equal(error.message, "/config request failed: 400");
      assert.equal(error.status, 400);
      assert.deepEqual(error.details, details);
      return true;
    },
  );
});

test("startChatTurn posts durable chat turn metadata", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          turn_id: "turn-abc",
          session: "popup",
          scope: "delight",
          subject_id: "BV1DL",
          subject_title: "复杂系统入门",
          message: "我想聊聊这条",
          reply: "",
          status: "pending",
          error: "",
          created_at: "2026-05-15 10:00:00",
          updated_at: "2026-05-15 10:00:00",
        };
      },
    };
  };

  const result = await startChatTurn({
    turnId: "turn-abc",
    session: "popup",
    scope: "delight",
    subjectId: "BV1DL",
    subjectTitle: "复杂系统入门",
    message: "我想聊聊这条",
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/chat/turns");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    turn_id: "turn-abc",
    session: "popup",
    scope: "delight",
    subject_id: "BV1DL",
    subject_title: "复杂系统入门",
    message: "我想聊聊这条",
  });
  assert.equal(result.status, "pending");
});

test("fetchChatTurn and fetchChatTurns read durable chat state", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        if (String(url).endsWith("/api/chat/turns/turn-abc")) {
          return {
            turn_id: "turn-abc",
            session: "popup",
            scope: "chat",
            message: "你好",
            reply: "你好，我在。",
            status: "completed",
          };
        }
        return {
          items: [
            {
              turn_id: "turn-abc",
              session: "popup",
              scope: "chat",
              message: "你好",
              reply: "你好，我在。",
              status: "completed",
            },
          ],
        };
      },
    };
  };

  const turn = await fetchChatTurn("turn-abc");
  const history = await fetchChatTurns({ session: "popup", scope: "chat", limit: 10 });

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/chat/turns/turn-abc");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(
    calls[1].url,
    "http://127.0.0.1:8420/api/chat/turns?session=popup&scope=chat&limit=10",
  );
  assert.equal(calls[1].options.method, "GET");
  assert.equal(turn.reply, "你好，我在。");
  assert.equal(history.items[0].turn_id, "turn-abc");
});

test("popup-api requests honor configured backend host and port from chrome.storage.local", async () => {
  // Reset module cache so the previous tests' default-port resolution
  // doesn't shadow the stubbed chrome.storage value.
  __resetBackendEndpointForTests();
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(_key: string, callback: (items: Record<string, unknown>) => void) {
          callback({
            popup_backend_endpoint: {
              host: "192.168.1.100",
              port: 19090,
              basePath: "/api",
            },
          });
        },
      },
    },
  };

  const calls: Array<{ url: string; options: { method?: string } }> = [];
  globalThis.fetch = (async (url: string, options: { method?: string }) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { language: "zh" };
      },
    };
  }) as unknown as typeof fetch;

  try {
    await fetchConfig();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "http://192.168.1.100:19090/api/config?reveal_keys=true");
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetBackendEndpointForTests();
  }
});

test("requestJson aborts fetch after timeoutMs", async () => {
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      if (!options.signal) {
        setTimeout(() => reject(new Error("missing abort signal")), 100);
        return;
      }
      options.signal.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
    });
  }) as unknown as typeof fetch;

  await assert.rejects(
    requestJson("/slow", { method: "GET", timeoutMs: 20 }),
    (error: unknown) => error instanceof Error && error.name === "AbortError",
  );
});

test("requestJson without timeout preserves no-signal fetch behavior", async () => {
  const calls: Array<{ signal?: AbortSignal }> = [];
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    calls.push(options);
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }) as unknown as typeof fetch;

  const result = await requestJson("/fast", { method: "GET" });

  assert.deepEqual(result, { ok: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].signal, undefined);
});

test("requestJson preserves caller abort reason before timeout fires", async () => {
  const controller = new AbortController();
  const reason = new DOMException("caller cancelled", "AbortError");
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
      queueMicrotask(() => controller.abort(reason));
    });
  }) as unknown as typeof fetch;

  await assert.rejects(
    requestJson("/caller-abort", {
      method: "GET",
      signal: controller.signal,
      timeoutMs: 200,
    }),
    (error: unknown) => error === reason,
  );
});

test("updateConfig uses the shared 60s config PUT timeout", async () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const delays: number[] = [];
  globalThis.setTimeout = ((callback: TimerHandler, delay?: number) => {
    delays.push(Number(delay));
    queueMicrotask(() => {
      if (typeof callback === "function") callback();
    });
    return 1 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  globalThis.clearTimeout = ((_id?: unknown) => undefined) as typeof clearTimeout;
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
    });
  }) as unknown as typeof fetch;

  try {
    await assert.rejects(
      updateConfig({ language: "zh" }),
      (error: unknown) => error instanceof Error && error.name === "AbortError",
    );
    assert.deepEqual(delays, [60_000]);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});
